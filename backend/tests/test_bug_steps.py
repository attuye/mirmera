"""Steps to a bug: each bug owns its own recording, Mirmera picks the steps up to
the request that failed, the tester can edit them, and the regression test uses
them. Built on a real session: two bugs found one after the other."""
from datetime import datetime, timedelta

import pytest

from app import models
from app.ai.base import LLMProvider
from app.database import SessionLocal
from app.routers.regression import drop_bug_assertions

APP = "http://localhost:4000"
T0 = datetime(2026, 10, 3, 18, 28, 41)  # 9:28:41 PM in Addis Ababa, stored as UTC


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def fill(label, value):
    return {"action": "fill", "locator": {"method": "label", "value": label}, "value": value}


def secret(label, name):
    return {"action": "fill", "locator": {"method": "label", "value": label}, "masked": True, "secret": name}


def select(label, value):
    return {"action": "select", "locator": {"method": "label", "value": label}, "value": value}


def check(label):
    return {"action": "check", "locator": {"method": "label", "value": label}}


def click(role, name):
    return {"action": "click", "locator": {"method": "role", "role": role, "name": name}}


def record(session_id, events):
    """events: (seconds after T0, kind, summary or action dict, url)."""
    from app.runner import describe_action
    with SessionLocal() as db:
        for t, kind, what, url in events:
            data = what if isinstance(what, dict) else None
            db.add(models.Evidence(session_id=session_id, kind=kind, url=url, data=data,
                                   summary=describe_action(what) if data else what,
                                   captured_at=at(t), last_seen_at=at(t)))
        db.commit()


def log_bug(client, session, title, seconds):
    bug = client.post(f"/sessions/{session['id']}/bugs", json={"title": title}).json()
    with SessionLocal() as db:
        db.get(models.Bug, bug["id"]).created_at = at(seconds)
        db.commit()
    return bug


def review(client, bug, **fields):
    client.patch(f"/bugs/{bug['id']}", json={
        "kind": "bug", "steps_to_reproduce": ["Fill the form", "Choose Passport", "Click Verify"],
        "expected": "Verified. Welcome to AddisVerify!", "actual": "Something went wrong. Please try again later.",
        "oracle_heuristic": "claims", "oracle_explanation": "The home page says Passports are accepted", **fields})


@pytest.fixture
def two_bugs(client, session):
    """The Ge'ez attempt (no request: the page refuses the name), logged as a bug; then the
    Passport attempt, whose Verify gets a 500, logged as a second bug."""
    sid = session["id"]
    geez = [
        (0, "navigation", f"{APP}/", f"{APP}/"),
        (1, "action", click("link", "Start verification →"), f"{APP}/"),
        (1.2, "navigation", f"{APP}/verify", f"{APP}/verify"),
        (9, "action", fill("Full name", "አበበ ከበደ"), f"{APP}/verify"),
        (15, "action", fill("Phone number", "09127823901231"), f"{APP}/verify"),
        (18, "action", secret("Create a password", "MIRMERA_SECRET_CREATE_A_PASSWORD"), f"{APP}/verify"),
        (19, "action", select("Document type", "Passport"), f"{APP}/verify"),
        (25, "action", fill("Document number", "ጵዕ64622"), f"{APP}/verify"),
        (29, "action", fill("Expiry date", "2026-10-22"), f"{APP}/verify"),
        (29.5, "action", check("I accept the terms"), f"{APP}/verify"),
        (37, "action", click("button", "Verify"), f"{APP}/verify"),
        (46, "action", click("button", "Verify"), f"{APP}/verify"),
    ]
    passport = [
        (129, "navigation", f"{APP}/", f"{APP}/"),
        (131, "action", click("link", "Start verification →"), f"{APP}/"),
        (131.2, "navigation", f"{APP}/verify", f"{APP}/verify"),
        (139, "action", fill("Full name", "Abebe Kebede"), f"{APP}/verify"),
        (143, "action", fill("Phone number", "0934671298"), f"{APP}/verify"),
        (146, "action", secret("Create a password", "MIRMERA_SECRET_CREATE_A_PASSWORD"), f"{APP}/verify"),
        (147, "action", select("Document type", "Passport"), f"{APP}/verify"),
        (151, "action", fill("Document number", "3434"), f"{APP}/verify"),
        (154, "action", fill("Expiry date", "2026-10-30"), f"{APP}/verify"),
        (154.5, "action", check("I accept the terms"), f"{APP}/verify"),
        (155, "action", click("button", "Verify"), f"{APP}/verify"),
        (155.3, "failed_request", f"POST {APP}/api/verify → 500", f"{APP}/api/verify"),
        (159, "action", click("button", "Verify"), f"{APP}/verify"),
        (176, "action", click("button", "Verify"), f"{APP}/verify"),
    ]
    record(sid, geez)
    first = log_bug(client, session, "System doesn't accept Ge'ez scripts", 107)  # 9:30:28 PM
    record(sid, passport)
    second = log_bug(client, session, "System throws error for Passport", 180)
    return first, second


def steps(client, bug):
    r = client.get(f"/bugs/{bug['id']}/steps")
    assert r.status_code == 200, r.text
    return r.json()


def summaries(out):
    return [s["summary"] for s in out["steps"]]


# ---------- each bug owns its own recording ----------

def test_the_passport_bug_never_sees_the_geez_attempt(client, two_bugs):
    _, passport = two_bugs
    out = steps(client, passport)
    assert out["recorded_actions"] == 11  # only what came after the Ge'ez bug was logged
    assert not any("አበበ" in s or "09127823901231" in s for s in summaries(out))


def test_steps_run_from_where_the_attempt_began_to_the_request_that_failed(client, two_bugs):
    _, passport = two_bugs
    out = steps(client, passport)
    assert out["source"] == "recording"
    assert out["anchor"] == f"POST {APP}/api/verify → 500"
    assert summaries(out) == [
        f"open {APP}/",
        'click link "Start verification →"',
        'fill label "Full name" = "Abebe Kebede"',
        'fill label "Phone number" = "0934671298"',
        'fill label "Create a password" = (hidden: MIRMERA_SECRET_CREATE_A_PASSWORD)',
        'select "Passport" in label "Document type"',
        'fill label "Document number" = "3434"',
        'fill label "Expiry date" = "2026-10-30"',
        'check label "I accept the terms"',
        'click button "Verify"',  # once: the clicks after the 500 are not steps to it
    ]
    assert "up to the action that sent the request that failed" in out["note"]


def test_without_a_failed_request_the_ai_picks_from_the_bugs_own_actions(client, two_bugs):
    geez, _ = two_bugs
    out = steps(client, geez)
    assert out["source"] == "ai" and out["anchor"] is None
    assert out["recorded_actions"] == 10
    assert summaries(out)[0] == f"open {APP}/" and 'fill label "Full name" = "አበበ ከበደ"' in summaries(out)
    assert "Abebe Kebede" not in " ".join(summaries(out))  # the later attempt belongs to the other bug


def test_a_404_while_the_page_loads_is_not_where_the_bug_is(client, session):
    record(session["id"], [
        (0, "navigation", f"{APP}/verify", f"{APP}/verify"),
        (0.5, "failed_request", f"GET {APP}/missing.css → 404", f"{APP}/missing.css"),
        (5, "action", fill("Full name", "Abebe Kebede"), f"{APP}/verify"),
    ])
    bug = log_bug(client, session, "Name refused", 10)
    assert steps(client, bug)["source"] == "ai"


# ---------- the tester edits the steps ----------

def edited(out, change):
    """The steps as the editor sends them back: type, url and action, nothing empty."""
    return [{k: v for k, v in s.items() if k in ("type", "url", "action") and v is not None} for s in change(out["steps"])]


def test_edited_steps_are_saved_used_and_can_be_reset(client, two_bugs):
    geez, _ = two_bugs
    out = steps(client, geez)
    # Keep the attempt, but with a National ID so only the name differs from a passing case.
    new = edited(out, lambda s: [x for x in s if (x.get("action") or {}).get("value") != "Passport"])
    new.insert(5, {"type": "action", "action": select("Document type", "National ID")})
    new = [x for i, x in enumerate(new) if not (i == len(new) - 1 and x["type"] == "action")]  # one Verify click
    r = client.put(f"/bugs/{geez['id']}/steps", json={"steps": new})
    assert r.status_code == 200, r.text
    saved = r.json()
    assert saved["source"] == "edited" and "Edited by you" in saved["note"]
    assert 'select "National ID" in label "Document type"' in summaries(saved)
    assert summaries(saved).count('click button "Verify"') == 1
    assert steps(client, geez)["source"] == "edited"

    reset = client.delete(f"/bugs/{geez['id']}/steps").json()
    assert reset["source"] == "ai" and 'select "Passport" in label "Document type"' in summaries(reset)


@pytest.mark.parametrize("bad, message", [
    ([], "at least one step"),
    ([{"type": "action", "action": click("button", "Verify")}], "first step must open a page"),
    ([{"type": "goto", "url": "localhost:4000"}], "full http(s) address"),
    ([{"type": "goto", "url": f"{APP}/"}, {"type": "action", "action": {"action": "eval", "locator": {"method": "label", "value": "x"}}}], "not a valid action"),
    ([{"type": "goto", "url": f"{APP}/"}, {"type": "action", "action": {"action": "click", "locator": {"method": "role", "role": "button"}}}], "needs a role and a name"),
    ([{"type": "goto", "url": f"{APP}/"}, {"type": "action", "action": {"action": "fill", "locator": {"method": "label", "value": "Name"}}}], "fill needs a value"),
    ([{"type": "goto", "url": f"{APP}/"}, {"type": "action", "action": {"action": "press", "locator": {"method": "label", "value": "Name"}}}], "press needs a key"),
    ([{"type": "goto", "url": f"{APP}/"}, {"type": "click"}], "type must be goto or action"),
])
def test_bad_steps_are_refused_with_a_reason(client, two_bugs, bad, message):
    r = client.put(f"/bugs/{two_bugs[0]['id']}/steps", json={"steps": bad})
    assert r.status_code == 422 and message in r.json()["detail"]


def test_a_typed_secret_name_is_normalised_and_never_a_value(client, two_bugs):
    r = client.put(f"/bugs/{two_bugs[0]['id']}/steps", json={"steps": [
        {"type": "goto", "url": f"{APP}/verify"},
        {"type": "action", "action": {**secret("Create a password", "mirmera_secret_pin"), "value": "1234"}},
    ]})
    action = r.json()["steps"][1]["action"]
    assert action["secret"] == "MIRMERA_SECRET_PIN" and "value" not in action


# ---------- the regression test uses the steps ----------

def test_the_test_replays_mirmeras_steps_and_the_ai_only_writes_checks(client, two_bugs):
    _, passport = two_bugs
    review(client, passport)
    test = client.post(f"/bugs/{passport['id']}/regression-test", json={}).json()
    code = test["code"]
    assert 'fill("Abebe Kebede")' in code and "አበበ" not in code and "09127823901231" not in code
    assert "fill(process.env.MIRMERA_SECRET_CREATE_A_PASSWORD ?? '')" in code
    assert 'getByLabel("Document number", { exact: true }).fill("3434")' in code
    assert code.count('getByRole("button", { name: "Verify", exact: true }).click()') == 1
    assert test["prompt_version"] == "regression-checks-v1"
    assert test["plan"]["reach"]["url_contains"] == "/api/verify"


def test_edited_steps_are_what_the_test_replays(client, two_bugs):
    _, passport = two_bugs
    review(client, passport)
    out = steps(client, passport)
    new = edited(out, lambda s: [x for x in s if "Phone number" not in str(x.get("action"))])
    client.put(f"/bugs/{passport['id']}/steps", json={"steps": new})
    code = client.post(f"/bugs/{passport['id']}/regression-test", json={}).json()["code"]
    assert "Phone number" not in code and 'fill("Abebe Kebede")' in code


class Checks(LLMProvider):
    name, model = "scripted", "checks"

    def __init__(self, reply):
        self.reply = reply

    async def complete(self, system, user, **kwargs):
        import json
        return json.dumps(self.reply)


def test_a_check_that_asserts_the_bug_is_dropped(client, two_bugs, monkeypatch):
    _, passport = two_bugs
    review(client, passport)
    monkeypatch.setattr("app.ai.routing.get_provider", lambda name, keys=None: Checks({
        "test_title": "Passport verification succeeds",
        "assertions": [
            {"kind": "no_failed_requests", "url_contains": "/api/verify", "why": "Verify must not fail"},
            {"kind": "text", "locator": {"method": "role", "role": "status"},
             "expected": "Something went wrong", "why": "Confirm the error appears"},
        ]}))
    test = client.post(f"/bugs/{passport['id']}/regression-test", json={}).json()
    assert [a["kind"] for a in test["plan"]["assertions"]] == ["no_failed_requests"]
    assert any('expected "Something went wrong"' in a and "pass while the bug exists" in a for a in test["plan"]["assumptions"])


def test_a_test_that_only_asserts_the_bug_is_refused(client, two_bugs, monkeypatch):
    _, passport = two_bugs
    review(client, passport)
    monkeypatch.setattr("app.ai.routing.get_provider", lambda name, keys=None: Checks({
        "test_title": "Passport error shows",
        "assertions": [{"kind": "visible", "locator": {"method": "text", "value": "Something went wrong. Please try again later."},
                        "why": "The error appears"}]}))
    r = client.post(f"/bugs/{passport['id']}/regression-test", json={})
    assert r.status_code == 502 and "would pass while the bug exists" in r.json()["detail"]


def test_dropping_bug_checks_leaves_unrelated_text_alone():
    kept, notes = drop_bug_assertions([
        {"kind": "text", "locator": {"method": "role", "role": "status"}, "expected": "Verified", "why": "w"},
        {"kind": "text", "locator": {"method": "role", "role": "status"}, "expected": "went", "why": "too short to judge"},
    ], "Something went wrong")
    assert len(kept) == 2 and notes == []


def test_actions_the_ai_skipped_are_listed():
    from app.routers.regression import skipped_actions
    from app.schemas import LLMRegressionPlan
    actions = [models.Evidence(summary=s) for s in (
        'fill label "Full name" = "Abebe"', 'fill label "Document number" = "3434"',
        'check label "I accept the terms"', 'click button "Verify"')]
    plan = LLMRegressionPlan.model_validate({"test_title": "Passport works", "steps": [
        {"kind": "goto", "url": f"{APP}/verify"}, {"kind": "recorded", "action_index": 0},
        {"kind": "recorded", "action_index": 3}],
        "assertions": [{"kind": "no_failed_requests", "url_contains": "/api/verify", "why": "Must not fail"}]})
    [warning] = skipped_actions(plan, actions)
    assert warning.startswith("The AI left out 2 recorded action(s) between the steps it kept: "
                              'fill label "Document number" = "3434"; check label "I accept the terms".')
    plan.steps = plan.steps[:2]
    assert skipped_actions(plan, actions) == []
