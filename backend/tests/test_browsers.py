"""Other browsers: exploring, replaying and comparing in Firefox and WebKit (Safari's engine).
Locally they skip when the engine isn't installed; CI installs both, so there they must pass."""
import os
import time

import pytest

from app import browsers
from tests.test_agent_eval import DELIVERY, Scripted, step  # noqa: F401
from tests.test_agent_eval import delivery_app  # noqa: F401  (fixture)
from tests.test_compare import recorded, two_versions  # noqa: F401  (fixture)


def engine_or_skip(name: str) -> None:
    if browsers.installed(name):
        return
    if os.environ.get("CI"):
        pytest.fail(browsers.install_hint(name))
    pytest.skip(browsers.install_hint(name))


def test_a_browser_that_isnt_installed_says_how_to_install_it(client, session, monkeypatch):
    monkeypatch.setattr(browsers, "installed", lambda name, executable_path=None: False)
    charter_id = session["charter_id"]
    client.patch(f"/projects/{client.get(f'/charters/{charter_id}').json()['project_id']}", json={"app_url": "http://localhost:1"})
    r = client.post(f"/charters/{charter_id}/explore", json={"conditions": {"browser": "webkit"}})
    assert r.status_code == 409 and "python -m playwright install webkit" in r.text
    r = client.post(f"/sessions/{session['id']}/comparisons", json={
        "a": {"name": "app", "url": "http://localhost:1"}, "b": {"name": "app in Firefox", "url": "http://localhost:1", "browser": "firefox"}})
    assert r.status_code in (409,) and ("install firefox" in r.text or "no recorded steps" in r.text)
    same = client.post(f"/sessions/{session['id']}/comparisons", json={
        "a": {"name": "a", "url": "http://localhost:1"}, "b": {"name": "b", "url": "http://localhost:1/"}})
    assert same.status_code == 422 and "two browsers" in same.text


def test_a_spec_for_a_bug_found_in_firefox_runs_in_firefox():
    from app.regression.render import render
    plan = {"title": "t", "steps": [{"type": "goto", "url": "http://localhost:4200/"}], "assertions": [], "browser": "firefox"}
    code = render(plan, bug_title="x", oracle="claims", issue_links=[], provenance="mock", known_failing=False)
    assert "test.use({ browserName: 'firefox' })" in code or 'test.use({ browserName: "firefox" })' in code


def test_firefox_has_no_mobile_mode():
    phone = {"viewport": {"width": 390, "height": 844}, "is_mobile": True, "has_touch": True}
    assert "is_mobile" not in browsers.context_options("firefox", phone)
    assert browsers.context_options("webkit", phone) == phone


@pytest.mark.confirm
@pytest.mark.parametrize("engine", ["firefox", "webkit"])
def test_exploring_in_another_browser(engine, client, delivery_app, monkeypatch):  # noqa: F811
    """The clock test's run, in Firefox and in WebKit: the same two bugs, confirmed in the same engine."""
    engine_or_skip(engine)
    from app import models
    from app.database import SessionLocal
    from app.routers.explore import start_exploration
    from app.agent import explore as agent
    provider = Scripted([step("Open", kind="goto", url=delivery_app),
                         step("Yesterday", kind="try", values={"Delivery date": "2026-09-10"}),
                         step("New Year", kind="try", values={"Delivery date": "2026-09-11"}),
                         step("Done", kind="done", summary="Tried two days.")])
    monkeypatch.setattr("app.ai.routing.get_provider", lambda name, keys=None: provider)
    with SessionLocal() as db:
        project = models.Project(org_id="eval", name="Delivery", app_url=delivery_app, default_ai_provider="mock")
        db.add(project)
        db.flush()
        story = models.Story(project_id=project.id, title="Book a delivery",
                             description="Acceptance criteria\n- The delivery date is today or later.")
        charter = models.Charter(project_id=project.id, mission_statement="Explore delivery days", source="eval")
        charter.stories = [story]
        db.add_all([story, charter])
        db.commit()
        r = start_exploration(db, charter, started_by="eval", max_steps=6,
                              conditions={"clock": "2026-09-11T00:20", "browser": engine})
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            time.sleep(0.5)
            db.expire_all()
            r = db.get(models.ExplorationRun, r.id)
            if r.status != "running" and r.id not in agent.ACTIVE:
                break
        bugs = [db.get(models.Bug, b) for b in r.bug_ids]
        notes = [n.content for n in db.query(models.Note).filter_by(session_id=r.session_id)]
    assert r.status == "finished", r.detail
    assert any(n.startswith(f"Exploring in {browsers.label(engine)}") for n in notes), notes
    assert [b.title for b in bugs] == [
        "A delivery date in the past (2026-09-10, the user's today is 2026-09-11) is accepted although AC1 says it is refused",
        "Shows Pagume 6, 2018, a day that doesn't exist"], [(s["action"], s["detail"]) for s in r.steps]
    assert bugs[0].environment["engine"] == engine and bugs[0].environment["browser"].startswith(browsers.label(engine))
    assert [b.reproducibility for b in bugs] == ["always", "always"], [b.confirmation for b in bugs]


@pytest.mark.parametrize("engine", ["firefox", "webkit"])
def test_the_same_address_in_two_browsers(engine, client, session, two_versions):  # noqa: F811
    """Cross-browser: one address, Chromium against another engine. This app works alike in both."""
    engine_or_skip(engine)
    v1, _ = two_versions
    recorded(session["id"], v1)
    r = client.post(f"/sessions/{session['id']}/comparisons", json={
        "a": {"name": "Chromium", "url": v1}, "b": {"name": browsers.label(engine), "url": v1, "browser": engine}})
    assert r.status_code == 200, r.text
    c = r.json()
    assert c["status"] == "done", c["detail"]
    assert c["summary"].endswith("no difference seen."), [s["differences"] for s in c["steps"]]
    assert c["env_b"]["browser"] == engine
