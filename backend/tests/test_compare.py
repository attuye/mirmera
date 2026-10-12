"""Compare two versions: a session's journey on both, side by side, and what differs."""
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.compare import differences
from tests.test_runner import chromium_available  # noqa: F401  (fixture)

FORM = """<!doctype html><title>Order</title><h1>{heading}</h1>
<form id=f><label for=q>Quantity</label><input id=q type=number>
<label for=n>Full name</label><input id=n>
<button type=submit>Place order</button></form><p role=status id=out></p>
<script>
document.getElementById('f').addEventListener('submit', async (e) => {{
  e.preventDefault()
  const r = await fetch('/api/orders', {{ method: 'POST', body: document.getElementById('q').value }})
  const data = r.ok ? await r.json() : null
  document.getElementById('out').textContent = r.ok ? 'Order ' + data.id + ' placed.' : 'Something went wrong.'
}})
</script>"""


def serve(version: int):
    class App(BaseHTTPRequestHandler):
        n = 0

        def do_GET(self):
            body = FORM.format(heading="Place an order" if version == 1 else "Order").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            qty = int(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or 0)
            App.n += 1
            ok = version == 1 or qty <= 5  # version 2: more than 5 breaks the server
            self.send_response(201 if ok else 500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(f'{{"id": {App.n * 1000 + version}}}'.encode() if ok else b'{"error": "boom"}')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), App)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def two_versions():
    v1, v2 = serve(1), serve(2)
    yield f"http://127.0.0.1:{v1.server_port}", f"http://127.0.0.1:{v2.server_port}"
    v1.shutdown()
    v2.shutdown()


def recorded(session_id: str, base: str) -> None:
    """A tester's journey, as the session's browser records it: open the form, fill it, submit."""
    from app import models
    from app.database import SessionLocal
    from app.timeutil import utcnow
    t = utcnow()
    with SessionLocal() as db:
        db.add(models.Evidence(session_id=session_id, kind="navigation", url=base + "/", summary="Opened /", captured_at=t))
        for i, (data, summary) in enumerate([
            ({"action": "fill", "locator": {"method": "label", "value": "Quantity"}, "value": "7"}, 'fill label "Quantity" = "7"'),
            ({"action": "fill", "locator": {"method": "label", "value": "Full name"}, "value": "Abebe"}, 'fill label "Full name" = "Abebe"'),
            ({"action": "click", "locator": {"method": "role", "role": "button", "name": "Place order"}}, 'click button "Place order"'),
        ], 1):
            db.add(models.Evidence(session_id=session_id, kind="action", url=base + "/", summary=summary, data=data,
                                   captured_at=t + timedelta(seconds=i)))
        db.commit()


def test_the_same_journey_on_two_versions_shows_what_differs(client, session, two_versions, chromium_available):  # noqa: F811
    v1, v2 = two_versions
    recorded(session["id"], v1)
    r = client.post(f"/sessions/{session['id']}/comparisons",
                    json={"a": {"name": "v1", "url": v1}, "b": {"name": "v2", "url": v2}})
    assert r.status_code == 200, r.text
    c = r.json()
    assert c["status"] == "done", c["detail"]
    assert [s["description"] for s in c["steps"]] == ['open /', 'fill label "Quantity" = "7"', 'fill label "Full name" = "Abebe"',
                                                       'click button "Place order"']
    assert c["steps"][0]["differences"] == ['Only v1 has the heading "Place an order"', 'Only v2 has the heading "Order"']
    # filling the form: the same on both (the heading still differs, but that was said at step 1)
    assert c["steps"][1]["differences"] == [] and c["steps"][2]["differences"] == []
    # the order numbers differ every time, so they don't count; the server error on v2 does
    assert c["steps"][3]["differences"] == ['Only v1 says "Order 1001 placed."', 'Only v2 says "Something went wrong."',
                                            "On v2 only, a request failed: POST /api/orders -> 500"]
    assert c["summary"] == "The same 4 steps on v1 and v2: 2 steps differ (step 1, 4)."
    assert len(c["steps"][3]["shots"]) == 2 and client.get(f"/comparisons/{c['id']}/shots/{c['steps'][3]['shots'][1]}").status_code == 200
    assert client.get(f"/comparisons/{c['id']}/shots/nope.jpg").status_code == 404
    assert client.get(f"/sessions/{session['id']}/comparisons").json()[0]["id"] == c["id"]

    # A difference becomes a bug draft, with v1 as its oracle and steps that run on v2.
    r = client.post(f"/comparisons/{c['id']}/steps/4/bug", json={"right": "a"})
    assert r.status_code == 200, r.text
    bug = client.get(f"/bugs/{r.json()['bug_ids'][0]}").json()
    assert bug["title"] == 'On v2, POST /api/orders -> 500 after click button "Place order"'
    assert bug["oracle_heuristic"] == "comparable_products"
    assert bug["steps_to_reproduce"][0] == f"Use v2 ({v2}) in Chromium" and bug["steps_to_reproduce"][1] == f"open {v2}/"
    from app import models
    from app.database import SessionLocal
    with SessionLocal() as db:
        row = db.get(models.Bug, bug["id"])
        assert row.symptom == {"kind": "text_visible", "value": "Something went wrong.", "explanation": "not shown on v1"}
        assert row.edited_steps[0] == {"type": "goto", "url": f"{v2}/"}
        assert len(row.evidence) == 2 if hasattr(row, "evidence") else True


def test_a_session_without_steps_has_nothing_to_compare(client, session):
    r = client.post(f"/sessions/{session['id']}/comparisons",
                    json={"a": {"name": "prod", "url": "http://localhost:1"}, "b": {"name": "staging", "url": "http://localhost:2"}})
    assert r.status_code == 409 and "no recorded steps" in r.text
    r = client.post(f"/sessions/{session['id']}/comparisons",
                    json={"a": {"name": "prod", "url": "http://localhost:1"}, "b": {"name": "staging", "url": "http://localhost:1/"}})
    assert r.status_code == 422


def test_numbers_that_change_run_to_run_are_not_differences():
    snap = lambda **k: {"status": "ok", "detail": "", "path": "/", "title": "Shop", "headings": [], "messages": [],  # noqa: E731
                        "fields": [], "buttons": [], "links": [], "failed": [], "page_errors": [], **k}
    a = snap(messages=["Order 1001 placed at 10:42."], failed=["GET http://a:1/api/x -> 404"])
    b = snap(messages=["Order 2002 placed at 10:43."], failed=["GET http://b:2/api/x -> 404"])
    assert differences(a, b, ("prod", "staging")) == []
    assert differences(a, snap(status="failed", detail="no button", path="/login"), ("prod", "staging"))[:2] == [
        "The step failed on staging: no button", "prod is on /, staging on /login"]
