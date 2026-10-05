"""API exploration: curl parsing, redaction, sending from the server, evidence, replaying
what the browser recorded, attaching a request to a bug, and the private-address setting."""
import json

import httpx
import pytest

from app import api_explore, models
from app.api_explore import ApiProblem, parse_curl, redact_body, redact_headers, redact_url
from app.config import settings
from app.database import SessionLocal
from app.timeutil import utcnow


def test_curl_from_devtools_bash_and_windows():
    bash = """curl 'https://kyc.example/api/documents?q=passport' \\
  -H 'accept: application/json' \\
  -H 'authorization: Bearer eyJabc' \\
  --data-raw '{"type":"passport","number":"EP123"}' \\
  --compressed"""
    r = parse_curl(bash)
    assert (r["method"], r["url"]) == ("POST", "https://kyc.example/api/documents?q=passport")
    assert {"name": "authorization", "value": "Bearer eyJabc"} in r["headers"]
    assert json.loads(r["body"]) == {"type": "passport", "number": "EP123"}
    windows = 'curl "https://kyc.example/api/x" ^\n  -X DELETE ^\n  -H "x-api-key: k1"'
    assert parse_curl(windows)["method"] == "DELETE"
    assert parse_curl("curl -I https://kyc.example/")["method"] == "HEAD"
    get = parse_curl("curl -G https://kyc.example/search -d q=passport -d page=2")
    assert (get["method"], get["url"], get["body"]) == ("GET", "https://kyc.example/search?q=passport&page=2", None)
    basic = parse_curl("curl -u attu:pw https://kyc.example/")
    assert basic["headers"][0]["value"].startswith("Basic ")
    js = parse_curl("curl --json '{\"a\":1}' https://kyc.example/a")
    assert js["method"] == "POST" and {"name": "Content-Type", "value": "application/json"} in js["headers"]
    with pytest.raises(ApiProblem, match="start with curl"):
        parse_curl("wget https://kyc.example/")
    with pytest.raises(ApiProblem, match="no address"):
        parse_curl("curl -H 'a: b'")


def test_redaction_keeps_what_matters_and_blanks_secrets():
    assert redact_headers([{"name": "Authorization", "value": "Bearer x"}, {"name": "X-Api-Key", "value": "k"},
                           {"name": "Cookie", "value": "s=1"}, {"name": "Accept", "value": "application/json"}]) == [
        {"name": "Authorization", "value": "•••"}, {"name": "X-Api-Key", "value": "•••"},
        {"name": "Cookie", "value": "•••"}, {"name": "Accept", "value": "application/json"}]
    body = json.loads(redact_body('{"email":"a@b.et","password":"hunter2","profile":{"api_key":"k","author":"Abebe"},"items":[{"token":"t"}]}'))
    assert body == {"email": "a@b.et", "password": "•••", "profile": {"api_key": "•••", "author": "Abebe"}, "items": [{"token": "•••"}]}
    assert redact_body("username=attu&password=hunter2", "application/x-www-form-urlencoded") == "username=attu&password=%E2%80%A2%E2%80%A2%E2%80%A2"
    assert redact_body("plain text, not a form") == "plain text, not a form"
    assert redact_url("https://x.et/s?q=passport&access_token=abc") == "https://x.et/s?q=passport&access_token=•••"


def handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/documents" and request.method == "POST":
        if request.headers.get("authorization") != "Bearer good":
            return httpx.Response(401, json={"detail": "Sign in"})
        return httpx.Response(201, json={"id": 7, "number": json.loads(request.content)["number"]}, headers={"Set-Cookie": "s=secret"})
    if request.url.path == "/moved":
        return httpx.Response(302, headers={"Location": "/elsewhere"})
    return httpx.Response(404, text="nope")


@pytest.fixture
def fake_network(monkeypatch):
    real = httpx.Client

    def client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)
    monkeypatch.setattr(api_explore.httpx, "Client", client)


def test_send_keeps_redacted_evidence(client, project, session, fake_network):
    client.patch(f"/projects/{project['id']}", json={"app_url": "https://kyc.example"})
    sid = session["id"]
    r = client.post(f"/sessions/{sid}/api/send", json={
        "method": "POST", "url": "/api/documents", "source": "curl",
        "headers": [{"name": "Authorization", "value": "Bearer good"}, {"name": "Content-Type", "value": "application/json"}],
        "body": '{"number":"EP123","password":"hunter2"}'})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["response"]["status"] == 201 and out["response"]["url"] == "https://kyc.example/api/documents"
    assert json.loads(out["response"]["body"]) == {"id": 7, "number": "EP123"}  # the tester sees it all
    e = out["evidence"]
    assert e["kind"] == "api_call" and e["summary"].startswith("POST https://kyc.example/api/documents → 201 (")
    kept = json.dumps(e["data"])
    assert "Bearer good" not in kept and "hunter2" not in kept and "s=secret" not in kept
    assert e["data"]["source"] == "curl" and e["data"]["status"] == 201

    unauthorised = client.post(f"/sessions/{sid}/api/send", json={"method": "POST", "url": "https://kyc.example/api/documents", "body": "{}"}).json()
    assert unauthorised["response"]["status"] == 401
    redirect = client.post(f"/sessions/{sid}/api/send", json={"url": "https://kyc.example/moved"}).json()["response"]
    assert redirect["status"] == 302  # not followed: a redirect is something to see
    history = client.get(f"/sessions/{sid}/api/history").json()
    assert [h["data"]["status"] for h in history] == [302, 401, 201]

    bug = client.post(f"/sessions/{sid}/bugs", json={"title": "Documents API accepts no auth?", "evidence_ids": [history[1]["id"]]}).json()
    attached = client.get(f"/bugs/{bug['id']}/evidence").json()
    assert [a["summary"] for a in attached][0].startswith("POST https://kyc.example/api/documents → 401")


def test_problems_are_explained(client, project, session, fake_network):
    sid = session["id"]
    no_base = client.post(f"/sessions/{sid}/api/send", json={"url": "/api/x"})
    assert no_base.status_code == 422 and "full address" in no_base.json()["detail"]
    assert client.post(f"/sessions/{sid}/api/send", json={"url": "ftp://kyc.example/x"}).status_code == 422
    client.post(f"/sessions/{sid}/end")
    ended = client.post(f"/sessions/{sid}/api/send", json={"url": "https://kyc.example/x"})
    assert ended.status_code == 409


def test_unreachable_and_private_addresses(client, session, monkeypatch):
    sid = session["id"]
    down = client.post(f"/sessions/{sid}/api/send", json={"url": "http://127.0.0.1:9/nothing"}).json()
    assert down["response"]["status"] is None and down["response"]["error"].startswith("Could not connect")
    assert "Could not connect" in down["evidence"]["summary"]
    monkeypatch.setattr(settings, "api_block_private", True)
    for url in ("http://127.0.0.1:8000/", "http://169.254.169.254/latest/meta-data", "http://10.0.0.5/admin"):
        r = client.post(f"/sessions/{sid}/api/send", json={"url": url})
        assert r.status_code == 422 and "private address" in r.json()["detail"], url


def test_replay_what_the_browser_recorded(client, session):
    sid = session["id"]
    now = utcnow()
    with SessionLocal() as db:
        for kind, summary, data in [
            ("failed_request", "POST https://kyc.example/api/verify → 500", {"body": '{"password":"•••","n":1}', "content_type": "application/json"}),
            ("failed_request", "POST https://kyc.example/api/verify → 500", None),  # the same again
            ("slow_request", "GET https://kyc.example/api/documents took longer than 2 s", {"ms": 2400}),
            ("slow_request", "Page https://kyc.example/ took longer than 3 s to load", {"ms": 3100}),
            ("failed_request", "Could not open https://kyc.example: net::ERR", None),
        ]:
            db.add(models.Evidence(session_id=sid, kind=kind, summary=summary, url=None, data=data, captured_at=now, last_seen_at=now))
        db.commit()
    rows = client.get(f"/sessions/{sid}/api/recorded").json()
    assert sorted((r["method"], r["url"]) for r in rows) == [("GET", "https://kyc.example/api/documents"), ("POST", "https://kyc.example/api/verify")]
    verify = next(r for r in rows if r["method"] == "POST")
    assert verify["body"] == '{"password":"•••","n":1}' and verify["headers"] == [{"name": "Content-Type", "value": "application/json"}]


def test_parse_curl_endpoint(client, session):
    ok = client.post(f"/sessions/{session['id']}/api/parse-curl", json={"command": "curl https://kyc.example/api -H 'Accept: */*'"}).json()
    assert ok == {"method": "GET", "url": "https://kyc.example/api", "headers": [{"name": "Accept", "value": "*/*"}], "body": None}
    assert client.post(f"/sessions/{session['id']}/api/parse-curl", json={"command": "nope"}).status_code == 422
