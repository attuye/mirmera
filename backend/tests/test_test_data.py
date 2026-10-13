"""The project's test data: values the app really accepts, kept by the team and used by runs."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tests.test_agent_eval import Scripted, step  # noqa: F401
from tests.test_runner import chromium_available  # noqa: F401  (fixture)

REAL_ID = "ET-4471-0093"
FORM = """<!doctype html><title>Register</title><h1>Register for the pilot</h1>
<form id=f><label for=n>Full name</label><input id=n><label for=i>National ID number</label><input id=i>
<label for=v>Voucher code</label><input id=v><button type=submit>Register</button></form><p role=status id=out></p>
<script>
document.getElementById('f').addEventListener('submit', async (e) => {
  e.preventDefault()
  const body = JSON.stringify({ name: document.getElementById('n').value, id: document.getElementById('i').value,
                                voucher: document.getElementById('v').value })
  const r = await fetch('/api/register', { method: 'POST', body })
  document.getElementById('out').textContent = (await r.json()).message
})
</script>"""


@pytest.fixture
def pilot_app():
    """Accepts one real national ID only, and each voucher once."""
    used = set()

    class App(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(FORM.encode())

        def do_POST(self):
            d = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
            if d["id"] != REAL_ID:
                msg = "Invalid National ID number."
            elif d["voucher"] in used or not d["voucher"].startswith("V-"):
                msg = "This voucher is not valid or already used."
            else:
                used.add(d["voucher"])
                msg = "Registered. Welcome to the pilot!"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"message": msg}).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), App)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def project_on(client, url):
    p = client.post("/projects", json={"name": "Pilot"}).json()
    client.patch(f"/projects/{p['id']}", json={"app_url": url})
    return p


def explore(client, project, replies, monkeypatch):
    from app import models
    from app.agent import explore as agent
    from app.database import SessionLocal
    provider = Scripted(replies)
    monkeypatch.setattr("app.ai.routing.get_provider", lambda name, keys=None: provider)
    charter = client.post(f"/projects/{project['id']}/charters", json={"mission_statement": "Explore registering"}).json()
    run = client.post(f"/charters/{charter['id']}/explore", json={"max_steps": 6}).json()
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        run = client.get(f"/exploration-runs/{run['id']}").json()
        if run["status"] != "running" and run["id"] not in agent.ACTIVE:
            break
        time.sleep(0.3)
    with SessionLocal() as db:
        notes = [n.content for n in db.get(models.Session, run["session_id"]).notes]
    return run, provider, notes


def test_runs_use_the_teams_data_keep_secrets_and_use_once_values_once(client, pilot_app, monkeypatch, chromium_available):  # noqa: F811
    project = project_on(client, pilot_app)
    url = f"/projects/{project['id']}/test-data"
    assert client.post(url, json={"name": "real national ID", "value": REAL_ID, "secret": True,
                                  "labels": ["National ID"]}).status_code == 200
    assert client.post(url, json={"name": "expired ID", "value": "ET-0000-0001", "kind": "invalid",
                                  "labels": ["National ID"], "note": "expired 2020"}).status_code == 200
    for v in ("V-ONE", "V-TWO"):
        client.post(url, json={"name": f"voucher {v[-3:]}", "value": v, "once": True, "labels": ["Voucher code"]})
    client.post(url, json={"name": "Fayda", "value": "1234", "labels": ["Fayda number"]})  # a field this app doesn't have
    rows = client.get(url).json()
    assert rows[0]["value"] is None and rows[0]["secret"] is True  # a secret is never sent back

    run, provider, notes = explore(client, project, [
        step("Valid", kind="try", values={}),
        step("Another", kind="try", values={"Full name": "Almaz Tesfaye"}),
        step("Third", kind="try", values={"Full name": "Kebede Alemu"}),
        step("Done", kind="done", summary="Registered.")], monkeypatch)
    tries = [s for s in run["steps"] if s["kind"] == "try"]
    assert [t["detail"].split('"')[1] for t in tries[:2]] == ["Registered. Welcome to the pilot!"] * 2, tries
    # the vouchers ran out: the third try gets a made-up one, and the app refuses it
    assert "This voucher is not valid or already used." in tries[2]["detail"]
    assert any(n.startswith("Test data: every \"once\" value for Voucher code is used now (the last was voucher TWO)") for n in notes), notes
    assert any(n.startswith("The app refused the Voucher code Mirmera made up") for n in notes), notes
    # the team's bad value is a test idea for its field
    assert "expired ID (your test data)" in provider.users[0]
    # the secret ID: typed into the app, never shown to the AI, a step, a note
    everything = json.dumps(run) + json.dumps(provider.users) + json.dumps(notes)
    from app import models
    from app.database import SessionLocal
    with SessionLocal() as db:
        journey = json.dumps(db.get(models.ExplorationRun, run["id"]).journey)
        session_text = json.dumps([e.summary for e in db.query(models.Evidence).filter_by(session_id=run["session_id"])])
    assert REAL_ID not in everything + session_text and "{data:real national ID}" in journey
    rows = {r["name"]: r for r in client.get(url).json()}
    assert rows["voucher ONE"]["used_at"] and rows["voucher TWO"]["used_at"]
    assert rows["real national ID"]["not_seen"] is False and rows["Fayda"]["not_seen"] is True  # renamed? Settings says so
    # used values come back when the test database is reset
    assert client.post(f"{url}/{rows['voucher ONE']['id']}/reset").json()["used_at"] is None


def test_without_test_data_a_refused_made_up_value_is_noted(client, pilot_app, monkeypatch, chromium_available):  # noqa: F811
    project = project_on(client, pilot_app)
    run, _, notes = explore(client, project, [step("Valid", kind="try", values={}), step("Done", kind="done", summary="x")], monkeypatch)
    assert any(n.startswith('The app refused the National ID number Mirmera made up ("Invalid National ID number.")') for n in notes), notes


def test_csv_import_and_known_fields(client, project):
    url = f"/projects/{project['id']}/test-data"
    csv = "name,value,labels,kind,once,secret\nphone A,+251911000001,Phone|Mobile,valid,yes,no\ncard,4111111111111111,Card number,valid,no,yes\n"
    made = client.post(f"{url}/import", json={"csv": csv}).json()
    assert [(m["name"], m["labels"], m["once"], m["secret"], m["value"]) for m in made] == [
        ("phone A", ["Phone", "Mobile"], True, False, "+251911000001"), ("card", ["Card number"], False, True, None)]
    again = client.post(f"{url}/import", json={"csv": "name,value\nphone A,1\n"})
    assert again.status_code == 409 and "already test data named" in again.text
    bad = client.post(f"{url}/import", json={"csv": "name,value,kind\nx,1,weird\n"})
    assert bad.status_code == 422 and bad.json()["detail"].startswith("Line 2:")
    assert client.post(url, json={"name": "a {b}", "value": "1"}).status_code == 422  # braces would break {data:…}
    assert client.get(f"/projects/{project['id']}/known-fields").json() == []


def test_matching_is_loose_but_not_wild():
    from app.test_data import matches
    row = {"labels": ["National ID"], "page": ""}
    assert matches({"label": "National ID number"}, row, page="/")
    assert matches({"label": "Your national ID", "keys": []}, row, page="/")
    assert matches({"label": "", "keys": ["national_id"]}, row, page="/") is False  # word split on _ isn't a word boundary
    assert matches({"label": "ID", "keys": []}, row, page="/") is False  # half the words: not this row
    assert matches({"label": "National ID"}, {"labels": ["National ID"], "page": "/verify"}, page="/register") is False


def test_a_replay_types_the_teams_value_for_a_data_step(pilot_app, chromium_available):  # noqa: F811
    import asyncio
    from app.regression.replay import replay
    plan = {"title": "Register", "base_url": pilot_app, "assertions": [
        {"kind": "visible", "locator": {"method": "text", "value": "Registered. Welcome to the pilot!"}}],
        "steps": [{"type": "goto", "url": pilot_app + "/"}] + [
            {"type": "action", "action": {"action": "fill", "locator": {"method": "label", "value": label}, "value": value}}
            for label, value in (("Full name", "Abebe"), ("National ID number", "{data:real national ID}"), ("Voucher code", "V-REPLAY"))
        ] + [{"type": "action", "action": {"action": "click", "locator": {"method": "role", "role": "button", "name": "Register"}}}]}
    rows = [{"id": "1", "name": "real national ID", "value": REAL_ID, "labels": [], "page": "", "kind": "valid",
             "once": False, "secret": True, "used": False}]
    result = asyncio.run(replay(plan, base_url=None, data=rows))
    assert result["outcome"] == "passes", result
    without = asyncio.run(replay(plan, base_url=None, data=[]))  # the token isn't known: typed as it is, and refused
    assert without["outcome"] != "passes"
