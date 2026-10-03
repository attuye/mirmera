"""Reproduction agent with a real headless Chromium, against the planted-bug
app from test_regression ("Passport" makes POST /api/verify fail): the mock
agent follows the recorded actions; scripted agents test the guard rails."""
import json
import time

import pytest

from app.ai.base import LLMProvider
from tests.test_regression import App, app_url, bug_present, recorded_bug  # noqa: F401  (fixtures)
from tests.test_runner import chromium_available, headless_runner  # noqa: F401


class Scripted(LLMProvider):
    """Plays the given steps in order, then repeats the last one."""
    name = "scripted"
    model = "scripted-agent"

    def __init__(self, steps: list[dict], delay: float = 0):
        self.steps = steps
        self.users: list[str] = []
        self.delay = delay

    async def complete(self, system, user, **kwargs):
        import asyncio
        if self.delay:
            await asyncio.sleep(self.delay)
        self.users.append(user)
        return json.dumps(self.steps[min(len(self.users) - 1, len(self.steps) - 1)])


def step(thought, **action):
    return {"thought": thought, "action": action}


GIVE_UP = step("Nothing more to try", kind="give_up", reason="out of ideas")


def start(client, bug, **body):
    """The tests below are about the AI's own steps, so the known steps are not replayed first."""
    r = client.post(f"/bugs/{bug['id']}/reproduce", json={"replay_known_steps": False, **body})
    assert r.status_code == 200, r.text
    return r.json()


def finished(client, run, timeout=120.0):
    deadline = time.monotonic() + timeout
    while True:
        r = client.get(f"/reproduction-runs/{run['id']}").json()
        if r["status"] != "running":
            return r
        if time.monotonic() > deadline:
            raise AssertionError(f"Still running after {timeout}s: {r['steps'][-3:]}")
        time.sleep(0.3)


def scripted(monkeypatch, steps, **kw):
    provider = Scripted(steps, **kw)
    monkeypatch.setattr("app.routers.reproduce.get_provider", lambda name: provider)
    return provider


# ---------- the happy path (mock agent) ----------

def test_the_agent_reproduces_a_recorded_bug_and_measures_it(client, recorded_bug, app_url, monkeypatch):
    monkeypatch.setenv("MIRMERA_SECRET_PASSWORD", "s3cret-pass")
    run = finished(client, start(client, recorded_bug))
    assert run["status"] == "reproduced", run["detail"]
    assert run["start_url"] == app_url + "/"
    actions = [s["action"] for s in run["steps"]]
    assert actions[0] == 'fill label "Full name" = "Abebe Kebede"'
    assert actions[1] == 'fill label "Password" with MIRMERA_SECRET_PASSWORD'  # the value never reaches the AI
    assert run["steps"][-1]["status"] == "verified"
    assert run["symptom"]["kind"] == "failed_request" and "/api/verify -> 500" in run["symptom"]["evidence"]
    assert run["reproducibility"] == {"runs": 3, "reproduced": 3, "results": run["reproducibility"]["results"]}
    assert run["detail"] == "Reproduced, and 3 of 3 reruns reproduced it again."
    assert [s["type"] for s in run["plan"]["steps"]][:2] == ["goto", "action"]
    assert client.get(f"/reproduction-runs/{run['id']}/screenshot").headers["content-type"] == "image/png"
    assert client.get(f"/bugs/{recorded_bug['id']}/reproduction").json()["id"] == run["id"]


def test_a_slow_backend_does_not_hide_the_bug(client, recorded_bug, monkeypatch):
    """The failed request answers a second after the click: the claim is checked once it has."""
    monkeypatch.setenv("MIRMERA_SECRET_PASSWORD", "s3cret-pass")
    App.delay = 1.0
    run = finished(client, start(client, recorded_bug, runs=1))
    assert run["status"] == "reproduced", run["detail"]
    assert run["reproducibility"]["reproduced"] == 1


def test_an_intermittent_bug_is_measured_not_just_found(client, recorded_bug, monkeypatch):
    App.flaky = True  # Passport fails on every other request
    App.posts = 0
    run = finished(client, start(client, recorded_bug, runs=4))
    assert run["status"] == "reproduced", run["detail"]
    assert run["reproducibility"]["reproduced"] == 2  # requests 2-5: ok, fail, ok, fail
    assert run["detail"].endswith("(intermittent).")


def test_a_fixed_app_is_not_reproduced_and_the_claim_is_refused(client, recorded_bug):
    App.fixed = True
    run = finished(client, start(client, recorded_bug))
    assert run["status"] == "not_reproduced"
    claim = next(s for s in run["steps"] if s["kind"] == "reproduced")
    assert claim["status"] == "not verified" and "no failed request matching" in claim["detail"]
    assert run["reproducibility"] is None


def test_the_found_steps_become_a_regression_test(client, recorded_bug, monkeypatch):
    monkeypatch.setenv("MIRMERA_SECRET_PASSWORD", "s3cret-pass")
    run = finished(client, start(client, recorded_bug, runs=1))
    test = client.post(f"/reproduction-runs/{run['id']}/regression-test", json={}).json()
    code = test["code"]
    assert 'failedRequests.filter((r) => r.includes("/api/verify"))' in code
    assert 'await page.getByLabel("Full name", { exact: true }).fill("Abebe Kebede")' in code
    assert "process.env.MIRMERA_SECRET_PASSWORD" in code and "s3cret" not in code
    replay = client.post(f"/regression-tests/{test['id']}/replay", json={}).json()["replay"]
    assert replay["outcome"] == "reproduces"


# ---------- guard rails (scripted agents) ----------

def test_the_agent_stays_inside_the_app(client, recorded_bug, monkeypatch):
    scripted(monkeypatch, [
        step("Try another site", kind="goto", url="https://example.org/"),
        step("Follow the link", kind="click", locator={"method": "role", "role": "link", "name": "Elsewhere"}),
        GIVE_UP,
    ])
    run = finished(client, start(client, recorded_bug))
    goto, click, _ = run["steps"]
    assert goto["status"] == "refused" and "outside the app" in goto["detail"]
    assert "left the app" in click["detail"] and click["url"].startswith("http://127.0.0.1")
    assert (run["status"], run["detail"]) == ("not_reproduced", "The agent gave up: out of ideas")


def test_an_unchecked_claim_is_refused_and_a_missing_secret_is_not_guessed(client, recorded_bug, monkeypatch):
    monkeypatch.delenv("MIRMERA_SECRET_PASSWORD", raising=False)
    scripted(monkeypatch, [
        step("Type the password", kind="fill_secret", secret="MIRMERA_SECRET_PASSWORD",
             locator={"method": "label", "value": "Password"}),
        step("I'm sure it broke", kind="reproduced",
             symptom={"kind": "text_visible", "value": "Totally fake error", "explanation": "I imagine it"}),
        GIVE_UP,
    ])
    run = finished(client, start(client, recorded_bug))
    secret, claim, _ = run["steps"]
    assert secret["status"] == "refused" and "not set" in secret["detail"]
    assert claim["status"] == "not verified"
    assert run["status"] == "not_reproduced"


def test_the_page_is_shown_to_the_agent_as_data(client, recorded_bug, monkeypatch):
    provider = scripted(monkeypatch, [GIVE_UP])
    finished(client, start(client, recorded_bug))
    user = provider.users[0]
    assert '- button "Verify"' in user  # the accessibility tree
    assert "MIRMERA_SECRET_PASSWORD" in user and "s3cret" not in user  # hints name secrets, never values
    assert "<page>" in user and "Steps left: 20" in user


def test_cancel_stops_the_agent(client, recorded_bug, monkeypatch):
    scripted(monkeypatch, [step("Wait for it", kind="wait")], delay=0.3)
    run = start(client, recorded_bug, max_steps=40)
    time.sleep(1.0)
    assert client.post(f"/reproduction-runs/{run['id']}/cancel").status_code == 200
    run = finished(client, run, timeout=30)
    assert run["status"] == "cancelled" and len(run["steps"]) < 40
    assert client.post(f"/reproduction-runs/{run['id']}/cancel").status_code == 409


def test_one_run_at_a_time_per_bug(client, recorded_bug, monkeypatch):
    scripted(monkeypatch, [step("Wait", kind="wait")], delay=0.3)
    run = start(client, recorded_bug, max_steps=10)
    assert client.post(f"/bugs/{recorded_bug['id']}/reproduce", json={}).status_code == 409
    client.post(f"/reproduction-runs/{run['id']}/cancel")
    finished(client, run, timeout=30)


def test_without_a_recording_the_address_is_needed(client, session, chromium_available, monkeypatch):  # noqa: F811
    bug = client.post(f"/sessions/{session['id']}/bugs", json={"title": "Something odd"}).json()
    r = client.post(f"/bugs/{bug['id']}/reproduce", json={})
    assert r.status_code == 409 and "base URL" in r.json()["detail"]


@pytest.mark.parametrize("action", [
    {"kind": "fill_secret", "locator": {"method": "label", "value": "Password"}, "secret": "hunter2"},
    {"kind": "press", "locator": {"method": "label", "value": "Search"}, "key": "Control+W"},
    {"kind": "click", "locator": {"method": "css", "value": "#x"}},
    {"kind": "reproduced"},
])
def test_the_agent_action_contract_refuses(action):
    from app import schemas
    with pytest.raises(ValueError):
        schemas.AgentAction(**action)


# ---------- a smaller model's unusable reply ----------

BAD = step("Click it", kind="click")  # no locator: what a small local model sometimes sends


def test_an_unusable_reply_is_a_failed_step_and_the_agent_carries_on(client, recorded_bug, monkeypatch):
    provider = scripted(monkeypatch, [BAD, BAD, GIVE_UP])  # complete_json asks twice before giving up on a reply
    run = finished(client, start(client, recorded_bug))
    bad, stop = run["steps"]
    assert (bad["action"], bad["status"]) == ("(unusable reply)", "failed") and "click needs a locator" in bad["detail"]
    assert (run["status"], run["detail"]) == ("not_reproduced", "The agent gave up: out of ideas")
    assert "(unusable reply) -> failed" in provider.users[-1]  # the agent saw its mistake


def test_three_unusable_replies_in_a_row_stop_the_run(client, recorded_bug, monkeypatch):
    scripted(monkeypatch, [BAD])
    run = finished(client, start(client, recorded_bug))
    assert run["status"] == "error" and "returned an unusable reply twice" in run["detail"]
    assert [s["status"] for s in run["steps"]] == ["failed"] * 3


# ---------- the known steps are replayed first ----------

class NoAI(LLMProvider):
    """Fails the test if the agent asks the AI anything."""
    name, model = "no-ai", "none"

    async def complete(self, system, user, **kwargs):
        raise AssertionError("the AI should not have been asked")


def replaying(client, bug, **body):
    r = client.post(f"/bugs/{bug['id']}/reproduce", json=body)  # replay_known_steps defaults to on
    assert r.status_code == 200, r.text
    return r.json()


def test_replaying_the_known_steps_reproduces_without_the_ai(client, recorded_bug, monkeypatch):
    monkeypatch.setenv("MIRMERA_SECRET_PASSWORD", "s3cret-pass")
    monkeypatch.setattr("app.routers.reproduce.get_provider", lambda name: NoAI())
    run = finished(client, replaying(client, recorded_bug, runs=2))
    assert run["status"] == "reproduced", run["detail"]
    kinds = [s["kind"] for s in run["steps"]]
    assert set(kinds[:-1]) == {"replay"} and kinds[-1] == "reproduced"
    assert run["steps"][-1]["status"] == "verified" and "/api/verify -> 500" in run["steps"][-1]["detail"]
    assert run["symptom"]["kind"] == "failed_request" and "no AI step was needed" in run["symptom"]["explanation"]
    assert run["reproducibility"]["reproduced"] == 2  # the reruns replay the same steps
    assert run["detail"] == "Reproduced, and 2 of 2 reruns reproduced it again."


def test_when_the_replay_does_not_show_it_the_ai_takes_over(client, recorded_bug, monkeypatch):
    monkeypatch.setenv("MIRMERA_SECRET_PASSWORD", "s3cret-pass")
    App.fixed = True
    provider = scripted(monkeypatch, [GIVE_UP])
    run = finished(client, replaying(client, recorded_bug))
    check = next(s for s in run["steps"] if s["kind"] == "reproduced")
    assert check["status"] == "not verified" and check["detail"].endswith("the AI takes over from here")
    assert run["status"] == "not_reproduced" and run["detail"] == "The agent gave up: out of ideas"
    assert "the AI takes over from here" in provider.users[0]  # the AI sees what the replay did


def test_a_step_the_replay_cannot_run_hands_over_to_the_ai(client, recorded_bug, monkeypatch):
    monkeypatch.delenv("MIRMERA_SECRET_PASSWORD", raising=False)  # the password step is skipped
    scripted(monkeypatch, [GIVE_UP])
    run = finished(client, replaying(client, recorded_bug))
    skipped = next(s for s in run["steps"] if s["status"] == "skipped")
    assert "MIRMERA_SECRET_PASSWORD" in skipped["detail"] and skipped["detail"].endswith("the AI takes over from here")
    assert run["status"] == "not_reproduced" and run["detail"] == "The agent gave up: out of ideas"
