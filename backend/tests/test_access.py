"""Who sees what: after a signed-in run, the pages that look like the user's own are opened
signed out and as the project's second user."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tests.test_explore import chromium_available  # noqa: F401  (fixture)

USERS = {"abebe@example.com": "Pass-one-1", "sara@example.com": "Pass-two-2"}
ORDERS = {"1": ("abebe@example.com", "Coffee beans × 2"), "2": ("sara@example.com", "Berbere × 1")}


def make_app(secure: bool):
    sessions: dict[str, str] = {}

    class App(BaseHTTPRequestHandler):
        def _user(self):
            for c in (self.headers.get("Cookie") or "").split(";"):
                if c.strip() in sessions:
                    return sessions[c.strip()]
            return None

        def _send(self, status, body=b"", headers=None):
            self.send_response(status)
            for k, v in (headers or {"Content-Type": "text/html; charset=utf-8"}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _page(self, title, body):
            return self._send(200, (f"<!doctype html><title>{title}</title><nav><a href='/'>Home</a> <a href='/my-orders'>My orders</a>"
                                    f" <a href='/logout'>Sign out</a></nav>{body}").encode())

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/login":
                return self._send(200, b"""<!doctype html><title>Sign in</title><form method=post action=/login>
<label for=e>Email</label><input id=e name=email><label for=p>Password</label><input id=p name=password type=password>
<button type=submit>Sign in</button></form>""")
            user = self._user()
            if not user:
                return self._send(302, headers={"Location": "/login"})
            if path == "/":
                return self._page("Home", f"<h1>Welcome</h1><p>Signed in as {user}</p>")
            if path == "/my-orders":
                mine = [i for i, (owner, _) in ORDERS.items() if owner == user]
                return self._page("My orders", "<h1>My orders</h1>" + "".join(f"<p><a href='/orders/{i}'>Order {i}</a></p>" for i in mine))
            if path.startswith("/orders/"):
                oid = path.rsplit("/", 1)[1]
                if oid not in ORDERS or (secure and ORDERS[oid][0] != user):
                    return self._page("Not found", "<h1>Order not found</h1>")
                owner, item = ORDERS[oid]
                return self._page(f"Order {oid}", f"<h1>Order {oid}</h1><p>{item}</p><p>Deliver to {owner}</p><p>Paid: 700 Birr</p>")
            return self._send(404, b"not found")

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
            from urllib.parse import parse_qs
            form = {k: v[0] for k, v in parse_qs(body).items()}
            if USERS.get(form.get("email")) == form.get("password"):
                token = f"s={len(sessions) + 1}"
                sessions[token] = form["email"]
                return self._send(302, headers={"Location": "/", "Set-Cookie": f"{token}; Path=/"})
            return self._send(200, b"<!doctype html><p role=alert>Wrong email or password.</p>")

        def log_message(self, *args):
            pass

    return App


@pytest.fixture(params=[False, True], ids=["trusts-the-address", "checks-the-owner"])
def orders_app(request):
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_app(secure=request.param))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", request.param
    server.shutdown()


def step(thought, **action):
    return json.dumps({"thought": thought, "action": action})


def test_an_order_page_is_opened_signed_out_and_as_the_second_user(client, orders_app, monkeypatch, chromium_available):  # noqa: F811
    from tests.test_agent_eval import Scripted
    from app import models
    from app.database import SessionLocal
    from app.agent import explore as agent
    app_url, secure = orders_app
    project = client.post("/projects", json={"name": "Orders"}).json()
    client.patch(f"/projects/{project['id']}", json={"app_url": app_url})
    r = client.put(f"/projects/{project['id']}/sign-in", json={
        "url": "/login", "username_field": "Email", "username": "abebe@example.com", "password": USERS["abebe@example.com"],
        "submit": "Sign in", "signed_in_text": "Sign out",
        "second_username": "sara@example.com", "second_password": USERS["sara@example.com"]})
    assert r.json()["second_username"] == "sara@example.com" and r.json()["second_password_set"] is True
    assert USERS["sara@example.com"] not in r.text

    monkeypatch.setattr("app.ai.routing.get_provider", lambda name, keys=None: Scripted([
        step("My orders", kind="goto", url=f"{app_url}/my-orders"),
        step("The order", kind="goto", url=f"{app_url}/orders/1"),
        step("Done", kind="done", summary="Looked at an order.")]))
    charter = client.post(f"/projects/{project['id']}/charters", json={"mission_statement": "Explore my orders"}).json()
    run = client.post(f"/charters/{charter['id']}/explore", json={"max_steps": 5}).json()
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        run = client.get(f"/exploration-runs/{run['id']}").json()
        if run["status"] != "running" and run["id"] not in agent.ACTIVE:
            break
        time.sleep(0.3)
    access = next(s for s in run["steps"] if s["kind"] == "access")
    assert "/orders/1 signed out: sent to sign in" in access["detail"], access["detail"]
    with SessionLocal() as db:
        bugs = [db.get(models.Bug, b) for b in run["bug_ids"]]
    if secure:
        assert "/orders/1 signed in as sara@example.com: refused" in access["detail"] and bugs == []
        return
    [bug] = bugs
    assert bug.title == "Another user can open /orders/1: sara@example.com sees abebe@example.com's page"
    assert bug.severity == "high" and "Deliver to abebe@example.com" in bug.actual
    assert bug.steps_to_reproduce[0] == "Sign in as sara@example.com (not abebe@example.com), then open it"
    assert bug.confirmation is None  # replays sign in as the first user: not replayed
    everything = json.dumps(run) + (bug.description or "") + bug.actual
    assert all(p not in everything for p in USERS.values())
