"""The progress shown in Mirmera while its agents work: which stage a Jira work item is at,
how far exploring has got, and what was left behind."""
from datetime import timedelta

from fastapi.testclient import TestClient

from app import models
from app.ai.mock_provider import MockProvider
from app.database import SessionLocal
from app.main import app
from app.timeutil import utcnow
from tests.conftest import sign_up
from tests.test_explore import finished
from tests.test_jira_agent import kyc_project, message, rpc
from tests.test_jira_app import forge, link_site  # noqa: F401  (forge is a fixture)
from tests.test_regression import app_url, bug_present  # noqa: F401  (fixtures)
from tests.test_runner import chromium_available, headless_runner  # noqa: F401


def ws(client):
    return client.me["workspaces"][0]["id"]


def test_each_stage_shows_while_the_agent_works(client, anon, forge, monkeypatch):
    kyc_project(client)
    link_site(client, forge, anon)
    seen = []

    class Watching(MockProvider):
        async def complete(self, system, user, **kwargs):
            # What someone watching the activity page sees while this AI call runs.
            items = client.get(f"/workspaces/{ws(client)}/agent-activity").json()["items"]
            seen.append((kwargs.get("task"), items[0]["stage"], [s["status"] for s in items[0]["stages"]][:4]))
            return await super().complete(system, user, **kwargs)

    monkeypatch.setattr("app.ai.routing.get_provider", lambda name, keys=None: Watching())
    rpc(anon, forge, "GetTask", {"taskId": rpc(anon, forge, "SendMessage", message())["result"]["task"]["id"]})
    assert seen == [
        ("story_risks", "risks", ["done", "done", "active", "todo"]),  # the story with the project's risks: one call
        ("risk_charters", "charters", ["done", "done", "done", "active"]),
    ]
    [done] = client.get(f"/workspaces/{ws(client)}/agent-activity").json()["items"]
    assert done["state"] == "done" and done["kind"] == "jira" and done["issue_key"] == "KYC-21"
    assert [s["status"] for s in done["stages"]] == ["done", "done", "done", "done", "skipped", "done"]  # no app URL: no exploring
    assert done["story_id"] and done["exploration"] is None


def test_exploring_shows_steps_and_bug_drafts(client, anon, forge, app_url, chromium_available):  # noqa: F811
    project = kyc_project(client)
    client.patch(f"/projects/{project['id']}", json={"app_url": app_url})
    link_site(client, forge, anon)
    rpc(anon, forge, "GetTask", {"taskId": rpc(anon, forge, "SendMessage", message())["result"]["task"]["id"]})
    activity = client.get(f"/workspaces/{ws(client)}/agent-activity").json()
    assert activity["running"] == 0
    [item] = activity["items"]  # the exploration is shown inside the Jira work item, not twice
    assert [s["status"] for s in item["stages"]] == ["done"] * 6
    ex = item["exploration"]
    assert ex["status"] == "finished" and ex["actions"] >= 1 and ex["steps_used"] <= ex["max_steps"]
    assert ex["recent"] and {"thought", "action", "status"} <= set(ex["recent"][-1])
    assert ex["session_id"]

    story = client.get(f"/stories/{item['story_id']}/agent-activity").json()["items"]
    assert [i["id"] for i in story] == [item["id"]]


def test_an_exploration_started_in_mirmera_and_who_may_see_it(client, app_url, chromium_available):  # noqa: F811
    project = client.post("/projects", json={"name": "KYC portal"}).json()
    client.patch(f"/projects/{project['id']}", json={"app_url": app_url})
    charter = client.post(f"/projects/{project['id']}/charters", json={"mission_statement": "Explore verification"}).json()
    run = finished(client, client.post(f"/charters/{charter['id']}/explore", json={}).json())
    [item] = client.get(f"/workspaces/{ws(client)}/agent-activity").json()["items"]
    assert item["kind"] == "explore" and item["id"] == run["id"] and item["state"] == "done"
    assert [s["key"] for s in item["stages"]] == ["explore", "done"]
    with TestClient(app) as sara:
        sign_up(sara, name="Sara", email="sara@x.et", workspace="Sara's")
        assert sara.get(f"/workspaces/{ws(client)}/agent-activity").status_code == 404


def test_work_cut_off_by_a_restart_shows_as_interrupted(client, anon, forge, monkeypatch):
    kyc_project(client)
    link_site(client, forge, anon)

    async def never(task_id):  # the background job never ran (the backend stopped)
        return None
    monkeypatch.setattr("app.jira_agent.run_plan", never)
    rpc(anon, forge, "SendMessage", message())
    assert client.get(f"/workspaces/{ws(client)}/agent-activity").json()["running"] == 1
    with SessionLocal() as db:
        db.query(models.JiraAgentTask).update({"updated_at": utcnow() - timedelta(hours=1)}, synchronize_session=False)
        db.commit()
    [item] = client.get(f"/workspaces/{ws(client)}/agent-activity").json()["items"]
    assert item["state"] == "failed" and "Interrupted" in item["message"]
    assert [s["status"] for s in item["stages"]][:2] == ["failed", "todo"]
