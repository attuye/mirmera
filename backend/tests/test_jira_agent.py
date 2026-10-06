"""The Mirmera agent in Jira: assigned an issue, it proposes risks and charters for it in
Mirmera; mentioned, it says what's left. Only signed calls from a linked site get anywhere."""
from app import models
from app.database import SessionLocal
from tests.test_jira_app import forge, link_site  # noqa: F401  (forge is a fixture)

DESCRIPTION = {  # Jira can send the description as Atlassian Document Format
    "type": "doc", "version": 1, "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": "As a customer I can renew an expired national ID online."}]},
        {"type": "paragraph", "content": [{"type": "text", "text": "Acceptance: an ID expired over 5 years ago is refused with a reason."}]},
    ]}


def rpc(anon, forge, method, params, installation="ari:cloud:ecosystem::installation/aaaa-1111"):
    r = anon.post("/jira-app/a2a", json={"jsonrpc": "2.0", "id": "req-1", "method": method, "params": params},
                  headers=forge(installation))
    assert r.status_code == 200, r.text
    return r.json()


def message(key="KYC-21", invocation="ISSUE_ASSIGNMENT", comment=None, context_id=None):
    data = {"userAccountId": "acc-attu", "agentAccountId": "acc-mirmera", "invocationType": invocation,
            "issue": {"id": "10021", "fields": {"key": key, "summary": "Renew an expired national ID", "description": DESCRIPTION}}}
    if comment:
        data["comment"] = {"id": "c1", "body": comment}
    msg = {"role": "ROLE_USER", "messageId": "m1", "parts": [{"text": "You have been assigned to a work item."}, {"data": data}]}
    if context_id:
        msg["contextId"] = context_id
    return {"message": msg}


def kyc_project(client):
    project = client.post("/projects", json={"name": "KYC portal"}).json()
    client.patch(f"/projects/{project['id']}", json={"jira_project_key": "KYC"})
    return project


def test_assigned_an_issue_it_proposes_risks_and_charters(client, anon, forge):
    project = kyc_project(client)
    link_site(client, forge, anon)

    started = rpc(anon, forge, "SendMessage", message())["result"]["task"]
    assert started["status"]["state"] == "TASK_STATE_WORKING"
    assert "proposing risks and charters" in started["status"]["message"]["parts"][0]["text"]

    # The planning ran in the background; Jira polls until it is done.
    done = rpc(anon, forge, "GetTask", {"taskId": started["id"], "contextId": started["contextId"]})["result"]["task"]
    assert done["status"]["state"] == "TASK_STATE_COMPLETED", done
    assert done["contextId"] == started["contextId"]
    text = done["status"]["message"]["parts"][0]["text"]
    assert "Everything below is a proposal" in text and "Risks (" in text and "Charters (" in text
    assert f"/projects/{project['id']}/stories/" in text

    [story] = client.get(f"/projects/{project['id']}/stories").json()
    assert (story["key"], story["tracker"], story["title"]) == ("KYC-21", "jira", "Renew an expired national ID")
    detail = client.get(f"/stories/{story['id']}").json()
    assert "refused with a reason" in detail["description"]  # the ADF description, as words
    assert story["url"] == "https://addisverify.atlassian.net/browse/KYC-21"
    risks = client.get(f"/projects/{project['id']}/risks").json()
    assert risks and all(r["status"] == "proposed" for r in risks)  # the tester decides
    assert len(detail["risk_list"]) == len(risks) == detail["risks"] and detail["charter_list"]
    assert all(c["status"] == "proposed" and c["risk_refs"] for c in detail["charter_list"])
    with SessionLocal() as db:
        assert db.query(models.ProductModel).count() == 0  # the project's own model is left alone

    [logged] = client.get(f"/workspaces/{client.me['workspaces'][0]['id']}/jira-app").json()["agent_tasks"]
    assert (logged["issue_key"], logged["action"], logged["state"], logged["story_id"]) == ("KYC-21", "plan", "TASK_STATE_COMPLETED", story["id"])

    again = rpc(anon, forge, "SendMessage", message())["result"]["task"]
    again = rpc(anon, forge, "GetTask", {"taskId": again["id"]})["result"]["task"]
    assert "already planned" in again["status"]["message"]["parts"][0]["text"]
    assert len(client.get(f"/projects/{project['id']}/risks").json()) == len(risks)  # nothing doubled


def test_mentioned_it_says_whats_left_or_plans_when_asked(client, anon, forge):
    kyc_project(client)
    link_site(client, forge, anon)
    status = rpc(anon, forge, "SendMessage", message(invocation="ISSUE_COMMENT_MENTION", comment="@Mirmera status?"))["result"]["task"]
    assert status["status"]["state"] == "TASK_STATE_COMPLETED"
    assert "no testing on KYC-21 yet" in status["status"]["message"]["parts"][0]["text"]

    planned = rpc(anon, forge, "SendMessage", message(invocation="ISSUE_COMMENT_MENTION", comment="@Mirmera plan the risks"))["result"]["task"]
    assert planned["status"]["state"] == "TASK_STATE_WORKING"
    rpc(anon, forge, "GetTask", {"taskId": planned["id"]})

    # A follow-up in the Rovo chat: same conversation, the issue is remembered.
    chat = {"message": {"role": "ROLE_USER", "messageId": "m2", "contextId": planned["contextId"], "parts": [{"text": "what's left?"}]}}
    reply = rpc(anon, forge, "SendMessage", chat)["result"]["task"]
    assert reply["contextId"] == planned["contextId"] and reply["status"]["state"] == "TASK_STATE_COMPLETED"
    assert "Not looked at yet" in reply["status"]["message"]["parts"][0]["text"]


def test_what_it_refuses(client, anon, forge):
    # Not linked yet: rejected, with how to link.
    r = rpc(anon, forge, "SendMessage", message())["result"]["task"]
    assert r["status"]["state"] == "TASK_STATE_REJECTED" and "isn't linked" in r["status"]["message"]["parts"][0]["text"]

    link_site(client, forge, anon)
    r = rpc(anon, forge, "SendMessage", message(key="PAY-3"))["result"]["task"]
    assert r["status"]["state"] == "TASK_STATE_FAILED"
    assert "uses the Jira project key PAY" in r["status"]["message"]["parts"][0]["text"]

    # Another site can't read this site's tasks.
    other = rpc(anon, forge, "GetTask", {"taskId": r["id"]}, installation="someone-elses-site")
    assert other["error"]["code"] == -32001
    assert rpc(anon, forge, "Teleport", {})["error"]["code"] == -32601
    bad = anon.post("/jira-app/a2a", content=b"not json", headers={**forge(), "Content-Type": "application/json"})
    assert bad.json()["error"]["code"] == -32700
    assert anon.post("/jira-app/a2a", json={"jsonrpc": "2.0", "id": 1, "method": "GetTask"}).status_code == 401  # unsigned


def test_cancel_keeps_the_task_terminal(client, anon, forge, monkeypatch):
    from app import jira_agent
    kyc_project(client)
    link_site(client, forge, anon)

    async def never(task_id):  # the background planning hasn't run yet
        return None
    monkeypatch.setattr(jira_agent, "run_plan", never)
    t = rpc(anon, forge, "SendMessage", message())["result"]["task"]
    c = rpc(anon, forge, "CancelTask", {"taskId": t["id"], "contextId": t["contextId"]})["result"]["task"]
    assert c["status"]["state"] == "TASK_STATE_CANCELED"
    assert rpc(anon, forge, "CancelTask", {"taskId": t["id"]})["result"]["task"]["status"]["state"] == "TASK_STATE_CANCELED"


def test_the_panel_button_plans_without_rovo(client, anon, forge):
    project = kyc_project(client)
    assert anon.post("/jira-app/plan", json={"issue_key": "KYC-30"}, headers=forge()).status_code == 409  # not linked
    link_site(client, forge, anon)

    started = anon.post("/jira-app/plan", headers=forge(), json={
        "issue_key": "KYC-30", "summary": "Verify identity with a national ID", "description": DESCRIPTION}).json()
    assert started["state"] == "TASK_STATE_WORKING" and started["done"] is False
    status = anon.get(f"/jira-app/tasks/{started['id']}", headers=forge()).json()
    assert status["done"] and status["ok"] and "Everything below is a proposal" in status["message"]
    assert status["story_url"].startswith(f"https://mirmera.addisverify.et/projects/{project['id']}/stories/")
    [story] = client.get(f"/projects/{project['id']}/stories").json()
    assert story["key"] == "KYC-30" and story["risks"] > 0

    assert anon.get(f"/jira-app/tasks/{started['id']}", headers=forge("another-site")).status_code == 404
    failed = anon.post("/jira-app/plan", headers=forge(), json={"issue_key": "PAY-1", "summary": "x"}).json()
    assert failed["done"] and not failed["ok"] and "PAY" in failed["message"]
