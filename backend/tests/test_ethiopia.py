"""The Ethiopia pack: ideas for how people here write phones, names, Birr and addresses, and the check
that valid Ethiopian forms are all accepted."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tests.test_agent_eval import Scripted, step  # noqa: F401
from tests.test_runner import chromium_available  # noqa: F401  (fixture)

FORM = """<!doctype html><title>Open an account</title><h1>Open an account</h1>
<form id=f><label for=n>Full name</label><input id=n><label for=p>Phone number</label><input id=p type=tel>
<button type=submit>Open account</button></form><p role=status id=out></p>
<script>
document.getElementById('f').addEventListener('submit', (e) => {
  e.preventDefault()
  const name = document.getElementById('n').value.trim(), phone = document.getElementById('p').value.trim()
  const out = document.getElementById('out')
  if (!/^[A-Za-z ]+$/.test(name)) out.textContent = 'Name must contain letters only.'
  else if (!/^\\+2519\\d{8}$/.test(phone)) out.textContent = 'Enter a valid phone number.'
  else out.textContent = 'Account opened. Welcome!'
})
</script>"""


@pytest.fixture
def bank_app():
    class App(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(FORM.encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), App)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def explore(client, url, replies, monkeypatch, *, ethiopia=True, rule=None):
    from app.agent import explore as agent
    provider = Scripted(replies)
    monkeypatch.setattr("app.ai.routing.get_provider", lambda name, keys=None: provider)
    p = client.post("/projects", json={"name": "Bank"}).json()
    assert client.patch(f"/projects/{p['id']}", json={"app_url": url, "ethiopia": ethiopia}).json()["ethiopia"] is ethiopia
    charter = client.post(f"/projects/{p['id']}/charters", json={"mission_statement": "Explore opening an account"}).json()
    if rule:
        story = client.post(f"/projects/{p['id']}/stories", json={"title": "Open an account",
                                                                   "description": f"Acceptance criteria\n- {rule}"}).json()
        client.put(f"/stories/{story['id']}/charters", json={"ids": [charter["id"]]})
    run = client.post(f"/charters/{charter['id']}/explore", json={"max_steps": 5}).json()
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        run = client.get(f"/exploration-runs/{run['id']}").json()
        if run["status"] != "running" and run["id"] not in agent.ACTIVE:
            break
        time.sleep(0.3)
    titles = [client.get(f"/bugs/{b}").json()["title"] for b in run["bug_ids"]]
    return run, provider, titles


TRIES = [step("Baseline", kind="try", values={}),
         step("Safaricom", kind="try", values={"Phone number": "0711223344"}),
         step("W/", kind="try", values={"Full name": "Haile W/Mariam"}),
         step("Done", kind="done", summary="Tried Ethiopian formats.")]


def test_valid_ethiopian_forms_refused_are_drafted(client, bank_app, monkeypatch, chromium_available):  # noqa: F811
    run, provider, titles = explore(client, bank_app, TRIES, monkeypatch)
    ideas = provider.users[0].split("<ideas>")[1].split("</ideas>")[0]
    assert "a Safaricom Ethiopia number (07…) (\"0711223344\")" in ideas and "W/ for Wolde (Haile W/Mariam)" in ideas, ideas
    assert titles == [
        'Phone number "0711223344" (a Safaricom Ethiopia number) is refused although "+251911223344" is accepted',
        'Full name "Haile W/Mariam" (W/ for Wolde) is refused although "Abebe Kebede" is accepted'], titles


def test_a_rule_that_names_the_format_is_respected(client, bank_app, monkeypatch, chromium_available):  # noqa: F811
    run, _, titles = explore(client, bank_app, TRIES, monkeypatch, rule="The phone number must start with +251.")
    assert not any(t.startswith("Phone number") for t in titles), titles  # the rule asks for that format
    assert any(t.startswith('Full name "Haile W/Mariam"') for t in titles), titles


def test_off_for_projects_whose_users_are_elsewhere(client, bank_app, monkeypatch, chromium_available):  # noqa: F811
    run, provider, titles = explore(client, bank_app, TRIES, monkeypatch, ethiopia=False)
    assert "Safaricom" not in provider.users[0] and titles == []


def test_kinds_and_number_fields():
    from app.agent import ethiopia
    assert ethiopia.kind_of({"label": "Mobile", "type": "text"}) == "phone"
    assert ethiopia.kind_of({"label": "Username", "type": "text"}) is None
    assert ethiopia.kind_of({"label": "Amount (Birr)", "type": "number"}) == "amount"
    token = ethiopia.ON.set(True)
    try:
        assert ("Birr with a thousands separator (1,250.50)", "1250.50") in ethiopia.ideas({"label": "Amount", "type": "number"})
    finally:
        ethiopia.ON.reset(token)
