"""Signing in to the app under test: set once per project, tried in a headless browser, and
used by every exploration run, its replays and the app survey. The password is never sent
back, never shown to the AI, and never written into a step, a note or a draft."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tests.test_explore import chromium_available  # noqa: F401  (fixture)

PASSWORD = "Pa55-word-for-tests"


def page(title, body: bytes) -> bytes:
    return (f"<!doctype html><title>{title}</title><nav><a href='/account'>Account</a> <a href='/logout'>Sign out</a></nav>"
            + body.decode()).encode()


@pytest.fixture
def members_app():
    """An app whose account page needs a sign-in. Saving a name with an apostrophe fails (500)."""
    sessions = set()

    class App(BaseHTTPRequestHandler):
        def _signed_in(self):
            return any(c.strip() in sessions for c in (self.headers.get("Cookie") or "").split(";"))

        def _send(self, status, body=b"", headers=None):
            self.send_response(status)
            for k, v in (headers or {"Content-Type": "text/html"}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/login":
                return self._send(200, b"""<!doctype html><title>Sign in</title><h1>Sign in</h1>
<form method=post action=/login><label for=e>Email</label><input id=e name=email>
<label for=p>Password</label><input id=p name=password type=password><button type=submit>Sign in</button></form>""")
            if path == "/logout":
                sessions.clear()
                return self._send(302, headers={"Location": "/login"})
            if not self._signed_in():
                return self._send(302, headers={"Location": "/login"})
            return self._send(200, page("Account", b"""<h1>Your account</h1>
<form id=f><label for=n>Display name</label><input id=n><button type=submit>Save</button></form><p role=status id=out></p>
<script>document.getElementById('f').addEventListener('submit', async (e) => {
  e.preventDefault()
  const r = await fetch('/api/name', { method: 'POST', body: document.getElementById('n').value })
  document.getElementById('out').textContent = r.ok ? 'Saved.' : 'Something went wrong.'
})</script>"""))

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n).decode()
            if self.path == "/login":
                if f"password={PASSWORD}" in body.replace("%2D", "-"):
                    sessions.add("session=abc123")
                    return self._send(302, headers={"Location": "/account", "Set-Cookie": "session=abc123; Path=/"})
                return self._send(200, b"<!doctype html><title>Sign in</title><p role=alert>Wrong email or password.</p>")
            if not self._signed_in():
                return self._send(401, b"{}", {"Content-Type": "application/json"})
            return self._send(500 if "'" in body else 200, b"{}", {"Content-Type": "application/json"})

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), App)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def set_up(client, app_url, password=PASSWORD):
    project = client.post("/projects", json={"name": "Members"}).json()
    client.patch(f"/projects/{project['id']}", json={"app_url": app_url})
    r = client.put(f"/projects/{project['id']}/sign-in", json={
        "url": "/login", "username_field": "Email", "username": "abebe@example.com", "password_field": "Password",
        "password": password, "submit": "Sign in", "signed_in_text": "Sign out"})
    assert r.status_code == 200, r.text
    return project


def test_the_password_is_kept_but_never_sent_back(client, project):
    pid = project["id"]
    assert client.get(f"/projects/{pid}/sign-in").json() is None
    r = client.put(f"/projects/{pid}/sign-in", json={"url": "/login", "username": "abebe@example.com", "password": PASSWORD})
    assert r.status_code == 200 and r.json()["password_set"] is True and PASSWORD not in r.text
    # saving again without a password keeps it; an empty one clears it
    r = client.put(f"/projects/{pid}/sign-in", json={"url": "/signin", "username": "abebe@example.com"})
    assert r.json()["password_set"] is True and r.json()["url"] == "/signin"
    from app import models
    from app.database import SessionLocal
    with SessionLocal() as db:
        stored = db.get(models.Project, pid).sign_in
        assert stored["password"] and PASSWORD not in json.dumps(stored)  # encrypted at rest
    assert client.put(f"/projects/{pid}/sign-in", json={"url": "/signin", "password": ""}).json()["password_set"] is False
    assert client.put(f"/projects/{pid}/sign-in", json={"url": "login"}).status_code == 422  # a path or an address
    assert client.delete(f"/projects/{pid}/sign-in").status_code == 204
    assert client.get(f"/projects/{pid}/sign-in").json() is None


def test_try_sign_in_shows_whether_it_worked(client, members_app, chromium_available):  # noqa: F811
    project = set_up(client, members_app)
    r = client.post(f"/projects/{project['id']}/sign-in/try").json()
    assert r["ok"] is True and r["url"].endswith("/account") and r["screenshot"].startswith("data:image/jpeg;base64,")
    assert '"Sign out" is on the page' in r["detail"]
    client.put(f"/projects/{project['id']}/sign-in", json={"url": "/login", "username_field": "Email", "username": "abebe@example.com",
                                                           "password": "wrong", "signed_in_text": "Sign out"})
    r = client.post(f"/projects/{project['id']}/sign-in/try").json()
    assert r["ok"] is False and "Wrong email or password." in r["detail"] and "wrong" not in r["detail"].replace("Wrong", "")


def run_on(client, monkeypatch, project, replies):
    from tests.test_agent_eval import Scripted
    from app import models
    from app.database import SessionLocal
    from app.agent import explore as agent
    provider = Scripted(replies)
    monkeypatch.setattr("app.ai.routing.get_provider", lambda name, keys=None: provider)
    charter = client.post(f"/projects/{project['id']}/charters", json={"mission_statement": "Explore the account page"}).json()
    run = client.post(f"/charters/{charter['id']}/explore", json={"max_steps": 6}).json()
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        run = client.get(f"/exploration-runs/{run['id']}").json()
        if run["status"] != "running" and run["id"] not in agent.ACTIVE:
            break
        time.sleep(0.3)
    with SessionLocal() as db:
        bugs = [db.get(models.Bug, b) for b in run["bug_ids"]]
        notes = [n.content for n in db.get(models.Session, run["session_id"]).notes]
        return run, provider, [(b.title, b.reproducibility, b.confirmation) for b in bugs], notes


def step(thought, **action):
    return json.dumps({"thought": thought, "action": action})


@pytest.mark.confirm
def test_runs_and_their_replays_start_signed_in_and_stay_signed_in(client, members_app, monkeypatch, chromium_available):  # noqa: F811
    project = set_up(client, members_app)
    run, provider, bugs, notes = run_on(client, monkeypatch, project, [
        step("Sign out?", kind="click", locator={"method": "text", "value": "Sign out"}),
        step("A name", kind="try", values={"Display name": "O'Brien"}),
        step("Done", kind="done", summary="Saved a name.")])
    assert run["status"] == "finished", run["detail"]
    assert notes[0] == '''Signed in to the app: signed in as abebe@example.com ("Sign out" is on the page).'''
    assert "/account" in provider.users[0] and "Your account" in provider.users[0]  # it starts on the signed-in page
    assert "Mirmera signed you in as abebe@example.com" in provider.users[0]
    assert run["steps"][0]["status"] == "refused" and "stay signed in" in run["steps"][0]["detail"]
    # the crash behind the sign-in is drafted, and its replays sign in first, so it shows every time
    assert bugs[0][0] == "POST /api/name fails with 500" and bugs[0][1] == "always", bugs
    everything = json.dumps(run) + json.dumps(provider.users) + json.dumps(notes) + json.dumps(bugs, default=str)
    assert PASSWORD not in everything


def test_a_run_that_cant_sign_in_stops_and_says_so(client, members_app, monkeypatch, chromium_available):  # noqa: F811
    project = set_up(client, members_app, password="not-the-password")
    run, provider, bugs, notes = run_on(client, monkeypatch, project, [step("Done", kind="done", summary="x")])
    assert run["status"] == "error" and run["detail"].startswith("Couldn't sign in to the app:")
    assert "Settings" in run["detail"] and provider.users == []  # the AI isn't asked to explore a sign-in page


def test_the_app_survey_maps_the_pages_behind_the_sign_in(client, members_app, chromium_available):  # noqa: F811
    project = set_up(client, members_app)
    job = client.post(f"/projects/{project['id']}/ai-jobs", json={"kind": "app_map", "request": {"max_pages": 5}}).json()
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        job = client.get(f"/ai-jobs/{job['id']}").json()
        if job["status"] not in ("queued", "running"):
            break
        time.sleep(0.3)
    assert job["status"] == "done", job
    paths = [p["path"] for p in job["result"]["pages"]]
    assert "/account" in paths and "/login" not in paths and "/logout" not in paths
    account = next(p for p in job["result"]["pages"] if p["path"] == "/account")
    assert [f["label"] for f in account["fields"]] == ["Display name"]
