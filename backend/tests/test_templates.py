"""Fresh values: {unique}, {email}, {phone}, {today+n} in steps, learned from "already registered",
and the fix for a regression test the app refused because its data was used."""
import asyncio
import json
import threading
import time
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app import test_data as td
from tests.test_agent_eval import Scripted, step  # noqa: F401
from tests.test_runner import chromium_available  # noqa: F401  (fixture)

SIGNUP = """<!doctype html><title>Sign up</title><h1>Create your account</h1>
<form id=f><label for=n>Full name</label><input id=n><label for=e>Email</label><input id=e type=email>
<button type=submit>Sign up</button></form><p role=status id=out></p>
<script>
document.getElementById('f').addEventListener('submit', async (e) => {
  e.preventDefault()
  const r = await fetch('/api/signup', { method: 'POST', body: document.getElementById('e').value })
  document.getElementById('out').textContent = (await r.json()).message
})
</script>"""


@pytest.fixture
def signup_app():
    emails = {"taken@example.com"}

    class App(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(SIGNUP.encode())

        def do_POST(self):
            email = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
            msg = "This email is already registered." if email in emails else "Welcome! Your account is ready."
            emails.add(email)
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


def test_templates_are_fresh_per_replay_and_the_same_within_one():
    td.start([], today=date(2026, 9, 11))
    a = td.resolve("{email} / {unique} / {email}")
    email, u, again = a.split(" / ")
    assert email == f"mirmera+{u}@example.com" == again  # one replay, one code
    assert td.resolve("{today}|{today+3}|{today-1}") == "2026-09-11|2026-09-14|2026-09-10"  # the run's clock
    phone = td.resolve("{phone}")
    assert phone.startswith("+2519") and len(phone) == 13 and phone[1:].isdigit()
    td.start([])
    assert td.resolve("{unique}") != u  # another replay, another code
    assert td.resolve("{data:nope} and {nothing}") == "{data:nope} and {nothing}"  # unknown: left as it is


def test_freshen_rewrites_the_steps_the_app_said_were_used():
    plan = {"steps": [{"type": "goto", "url": "http://x/"}] + [
        {"type": "action", "action": {"action": "fill", "locator": {"method": "label", "value": lbl}, "value": v}}
        for lbl, v in (("Full name", "Abebe"), ("Email", "abebe@example.com"), ("Phone", "+251911223344"))]}
    new, changes = td.freshen(plan, "This email is already registered.")
    assert changes == [{"field": "Email", "was": "abebe@example.com", "now": "{email}"}]
    assert plan["steps"][2]["action"]["value"] == "abebe@example.com"  # the original is untouched
    _, fallback = td.freshen(plan, "Something is already used.")
    assert [c["now"] for c in fallback] == ["Abebe {unique}", "{email}", "{phone}"]


def test_a_spec_with_templates_makes_fresh_values_itself():
    from app.regression.render import render
    plan = {"title": "t", "steps": [{"type": "goto", "url": "http://localhost:4200/"},
                                    {"type": "action", "action": {"action": "fill", "locator": {"method": "label", "value": "Email"},
                                                                  "value": "{email}"}}], "assertions": []}
    code = render(plan, bug_title="x", oracle="claims", issue_links=[], provenance="mock", known_failing=False)
    assert "const email = () => `mirmera+${RUN}@example.com`" in code
    assert ".fill(`${email()}`)" in code


def test_after_already_registered_the_run_uses_fresh_values(client, signup_app, monkeypatch, chromium_available):  # noqa: F811
    from app import models
    from app.agent import explore as agent
    from app.database import SessionLocal
    provider = Scripted([step("Sign up", kind="try", values={"Email": "taken@example.com"}),
                         step("Again", kind="try", values={"Full name": "Almaz Tesfaye"}),
                         step("Done", kind="done", summary="Signed up.")])
    monkeypatch.setattr("app.ai.routing.get_provider", lambda name, keys=None: provider)
    p = client.post("/projects", json={"name": "Signup"}).json()
    client.patch(f"/projects/{p['id']}", json={"app_url": signup_app})
    charter = client.post(f"/projects/{p['id']}/charters", json={"mission_statement": "Explore signing up"}).json()
    run = client.post(f"/charters/{charter['id']}/explore", json={"max_steps": 4}).json()
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        run = client.get(f"/exploration-runs/{run['id']}").json()
        if run["status"] != "running" and run["id"] not in agent.ACTIVE:
            break
        time.sleep(0.3)
    with SessionLocal() as db:
        notes = [n.content for n in db.get(models.Session, run["session_id"]).notes]
        journey = db.get(models.ExplorationRun, run["id"]).journey
    tries = [s for s in run["steps"] if s["kind"] == "try"]
    assert "already registered" in tries[0]["detail"] and "Welcome! Your account is ready." in tries[1]["detail"], tries
    assert any(n.startswith('The app said "This email is already registered.": from now on Mirmera fills Email with a fresh value ({email})')
               for n in notes), notes
    assert any(s.get("action", {}).get("value") == "{email}" for s in journey)  # replays get a fresh one too


def test_fresh_data_fixes_a_test_the_app_refused_and_replays_it(client, session, signup_app, chromium_available):  # noqa: F811
    from app import models
    from app.database import SessionLocal
    bug = client.post(f"/sessions/{session['id']}/bugs", json={"title": "Sign-up says nothing for a long name"}).json()
    client.patch(f"/bugs/{bug['id']}", json={"kind": "bug", "oracle_heuristic": "claims", "oracle_explanation": "x"})
    plan = {"title": "Sign up", "base_url": signup_app, "steps": [{"type": "goto", "url": signup_app + "/"}] + [
        {"type": "action", "action": {"action": "fill", "locator": {"method": "label", "value": lbl}, "value": v}}
        for lbl, v in (("Full name", "Abebe"), ("Email", "taken@example.com"))] + [
        {"type": "action", "action": {"action": "click", "locator": {"method": "role", "role": "button", "name": "Sign up"}}}],
        "assertions": [{"kind": "visible", "locator": {"method": "text", "value": "Welcome! Your account is ready."}}]}
    with SessionLocal() as db:
        t = models.RegressionTest(bug_id=bug["id"], plan=plan, code="", filename="t.spec.ts", base_url=signup_app,
                                  known_failing=False, warnings=[], ai_provider="mock", ai_model="mock", prompt_version="v")
        db.add(t)
        db.commit()
        test_id = t.id
    first = client.post(f"/regression-tests/{test_id}/replay", json={}).json()
    assert first["replay"]["outcome"] == "reproduces" and "already registered" in json.dumps(first["replay"]["page"])
    r = client.post(f"/regression-tests/{test_id}/fresh-data")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["changes"] == [{"field": "Email", "was": "taken@example.com", "now": "{email}"}]
    assert out["test"]["replay"]["outcome"] == "passes"  # a fresh email each replay
    assert "${email()}" in out["test"]["code"] and out["test"]["warnings"][-1] == "Fresh values each run: Email ({email})"
