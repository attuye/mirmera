"""AI steps as jobs: building the product model, proposing risks and drafting charters from
risks run in the background and keep their result, so a refresh (or another tab, or a
colleague) sees the step working and then its result."""
import asyncio

from fastapi.testclient import TestClient

from app import ai_jobs, models
from app.ai.mock_provider import MockProvider
from app.database import SessionLocal
from app.main import app
from tests.conftest import sign_up
from tests.test_risks_api import accept, artifact, modelled, risks  # noqa: F401  (fixtures)


def job(client, project, kind, request=None):
    r = client.post(f"/projects/{project['id']}/ai-jobs", json={"kind": kind, "request": request or {}})
    assert r.status_code == 200, r.text
    return r.json()


def test_each_step_of_the_risk_page_runs_as_a_job_and_keeps_its_result(client, project, artifact):  # noqa: F811
    started = job(client, project, "product_model", {"artifact_ids": [artifact["id"]]})
    assert started["status"] == "running" and started["label"] == "Build the product model"  # answered at once
    built = client.get(f"/ai-jobs/{started['id']}").json()  # the test client returns after the background work
    assert built["status"] == "done" and built["result"]["summary"]
    assert client.get(f"/projects/{project['id']}/product-model").status_code == 200

    proposed = client.get(f"/ai-jobs/{job(client, project, 'risk_analysis')['id']}").json()
    assert proposed["status"] == "done" and proposed["result"]
    listed = client.get(f"/projects/{project['id']}/risks").json()
    assert len(listed) == len(proposed["result"])
    accept(client, listed[0])

    drafted = job(client, project, "charters_from_risks", {"risk_ids": [listed[0]["id"]]})
    # After a refresh the page asks for the latest one: it is there, with its drafts.
    latest = client.get(f"/projects/{project['id']}/ai-jobs/latest", params={"kind": "charters_from_risks"}).json()
    assert latest["id"] == drafted["id"] and latest["status"] == "done"
    assert latest["result"]["charters"][0]["risk_ids"] == [listed[0]["id"]]
    assert latest["request"] == {"risk_ids": [listed[0]["id"]]} and latest["started_by"]


def test_a_refused_or_failed_step_says_why(client, modelled, risks):  # noqa: F811
    refused = client.get(f"/ai-jobs/{job(client, modelled, 'charters_from_risks')['id']}").json()
    assert refused["status"] == "failed" and "Accept at least one risk" in refused["error"]
    bad = client.post(f"/projects/{modelled['id']}/ai-jobs", json={"kind": "charters_from_risks", "request": {"max_charters": "lots"}})
    assert bad.status_code == 422
    assert client.post(f"/projects/{modelled['id']}/ai-jobs", json={"kind": "write_my_tests"}).status_code == 422
    assert client.get(f"/projects/{modelled['id']}/ai-jobs/latest", params={"kind": "product_model"}).json() is None


def test_while_it_works_a_refresh_sees_it_and_a_second_click_joins_it(client, modelled, risks, monkeypatch):  # noqa: F811
    accept(client, risks[0])
    seen = {}

    class Slow(MockProvider):
        async def complete(self, system, user, **kwargs):
            if kwargs.get("task") == "risk_charters":
                def meanwhile():  # what a refreshed page and the sidebar see while the AI works
                    seen["latest"] = client.get(f"/projects/{modelled['id']}/ai-jobs/latest", params={"kind": "charters_from_risks"}).json()
                    seen["again"] = job(client, modelled, "charters_from_risks")
                    ws = client.me["workspaces"][0]["id"]
                    seen["activity"] = client.get(f"/workspaces/{ws}/agent-activity").json()
                await asyncio.to_thread(meanwhile)
            return await super().complete(system, user, **kwargs)

    monkeypatch.setattr("app.ai.routing.get_provider", lambda name, keys=None: Slow())
    first = job(client, modelled, "charters_from_risks")
    assert seen["latest"]["id"] == first["id"] and seen["latest"]["status"] == "running"
    assert seen["again"]["id"] == first["id"]  # not a second AI call
    [working] = [i for i in seen["activity"]["items"] if i["kind"] == "ai"]
    assert working["state"] == "running" and working["title"] == "Draft charters from risks"
    assert seen["activity"]["running"] == 1
    assert client.get(f"/ai-jobs/{first['id']}").json()["status"] == "done"


def test_a_job_cut_off_by_a_restart_shows_as_interrupted(client, project):
    with SessionLocal() as db:
        db.add(models.AiJob(id="job-cut-off", project_id=project["id"], kind="risk_analysis", status="running"))
        db.commit()
    cut = client.get("/ai-jobs/job-cut-off").json()
    assert cut["status"] == "failed" and cut["error"] == ai_jobs.INTERRUPTED


def test_other_workspaces_cannot_see_a_job(client, project, artifact):  # noqa: F811
    started = job(client, project, "product_model", {"artifact_ids": [artifact["id"]]})
    with TestClient(app) as sara:
        sign_up(sara, name="Sara", email="sara@x.et", workspace="Sara's")
        assert sara.get(f"/ai-jobs/{started['id']}").status_code == 404
        assert sara.post(f"/projects/{project['id']}/ai-jobs", json={"kind": "risk_analysis"}).status_code == 404
