"""OpenAPI import: YAML and JSON, Swagger 2.0, ready requests per endpoint, refs and examples,
auth, the endpoint list for charter suggestions, and which endpoints sessions tried."""
import json

import httpx
import pytest

from app import models, openapi
from app.database import SessionLocal
from app.timeutil import utcnow

SPEC = """
openapi: 3.0.3
info: {title: KYC API, version: "2.1"}
servers:
  - url: https://{env}.kyc.example/v2
    variables: {env: {default: staging}}
security: [{bearer: []}]
components:
  securitySchemes:
    bearer: {type: http, scheme: bearer}
    key: {type: apiKey, in: header, name: X-Api-Key}
  schemas:
    Document:
      type: object
      required: [type, number]
      properties:
        type: {type: string, enum: [passport, national_id]}
        number: {type: string, example: EP1234567}
        expires: {type: string, format: date}
        holder: {$ref: '#/components/schemas/Person'}
    Person:
      type: object
      properties:
        email: {type: string, format: email}
        age: {type: integer, minimum: 18}
        friend: {$ref: '#/components/schemas/Person'}
paths:
  /documents:
    get:
      tags: [Documents]
      summary: List documents
      parameters:
        - {name: status, in: query, required: true, schema: {type: string, enum: [pending, verified]}}
        - {name: page, in: query, schema: {type: integer}}
    post:
      tags: [Documents]
      summary: Upload a document
      requestBody:
        content:
          application/json:
            schema: {$ref: '#/components/schemas/Document'}
  /documents/{id}:
    parameters:
      - {name: id, in: path, required: true, schema: {type: string}}
    get: {tags: [Documents], summary: Get a document}
    delete: {tags: [Documents], summary: Delete a document, deprecated: true, security: [{key: []}]}
  /health:
    get: {summary: Health check, security: []}
"""

SWAGGER = {"swagger": "2.0", "info": {"title": "Old API", "version": "1"}, "host": "old.kyc.example", "basePath": "/api",
           "schemes": ["http"], "paths": {"/users/{userId}": {"put": {"operationId": "updateUser", "parameters": [
               {"name": "userId", "in": "path", "required": True, "type": "integer", "example": 7},
               {"name": "body", "in": "body", "schema": {"$ref": "#/definitions/User"}}]}}},
           "definitions": {"User": {"type": "object", "properties": {"name": {"type": "string", "example": "Abebe"}}}}}


def test_a_spec_becomes_ready_requests():
    info, ops, servers = openapi.parsed(SPEC)
    assert info == {"title": "KYC API", "version": "2.1"} and servers == ["https://staging.kyc.example/v2"]
    by = {o["key"]: o for o in ops}
    assert list(by) == ["GET /documents", "POST /documents", "GET /documents/{id}", "DELETE /documents/{id}", "GET /health"]
    assert by["GET /documents"]["url"] == "/documents?status=pending"  # required query filled, optional left out
    post = by["POST /documents"]
    body = json.loads(post["body"])
    assert body["type"] == "passport" and body["number"] == "EP1234567" and body["expires"] == "2026-01-31"
    assert body["holder"]["email"] == "tester@example.com" and body["holder"]["age"] == 18
    assert body["holder"]["friend"] == {}  # a schema that refers to itself stops
    assert {"name": "Content-Type", "value": "application/json"} in post["headers"]
    assert by["GET /documents/{id}"]["unfilled"] == ["id"]
    assert by["GET /documents"]["auth"] == ["bearer token"] and by["GET /health"]["auth"] == []
    assert by["DELETE /documents/{id}"]["auth"] == ["API key in header X-Api-Key"] and by["DELETE /documents/{id}"]["deprecated"]
    assert by["GET /documents"]["tag"] == "Documents" and by["GET /health"]["tag"] == "Other"


def test_swagger_2():
    info, ops, servers = openapi.parsed(json.dumps(SWAGGER))
    assert servers == ["http://old.kyc.example/api"] and info["title"] == "Old API"
    put = ops[0]
    assert (put["key"], put["url"], put["summary"]) == ("PUT /users/{userId}", "/users/7", "updateUser")
    assert json.loads(put["body"]) == {"name": "Abebe"} and put["unfilled"] == []


@pytest.mark.parametrize("text,problem", [
    ("not: [valid", "Couldn't read it"), ("title: just yaml", "isn't an OpenAPI"),
    ('{"openapi": "3.0.0", "info": {}, "paths": {}}', "no paths"),
])
def test_what_isnt_a_spec(text, problem):
    with pytest.raises(openapi.SpecProblem, match=problem):
        openapi.load(text)


def test_import_list_tried_and_delete(client, project, session, monkeypatch):
    pid = project["id"]
    r = client.post(f"/projects/{pid}/openapi", json={"text": SPEC})
    assert r.status_code == 200, r.text
    spec = r.json()
    assert (spec["label"], spec["operations"], spec["source"]) == ("OpenAPI: KYC API 2.1", 5, None)
    again = client.post(f"/projects/{pid}/openapi", json={"text": SPEC.replace('"2.1"', '"2.2"')}).json()
    assert again["id"] == spec["id"] and again["version"] == "2.2"  # the same API replaces the earlier import
    assert [s["title"] for s in client.get(f"/projects/{pid}/openapi").json()] == ["KYC API"]
    artifacts = client.get(f"/projects/{pid}/artifacts").json()
    assert artifacts[0]["type"] == "openapi" and "- POST /documents: Upload a document" in artifacts[0]["ai_summary"]

    now = utcnow()
    with SessionLocal() as db:  # two requests a session sent: GET one document, and an unknown path
        for url, status in [("https://staging.kyc.example/v2/documents/42?x=1", 404), ("https://staging.kyc.example/v2/other", 200)]:
            db.add(models.Evidence(session_id=session["id"], kind="api_call", summary="x", url=url,
                                   data={"method": "GET", "status": status}, captured_at=now, last_seen_at=now))
        db.commit()
    ops = client.get(f"/projects/{pid}/openapi/{spec['id']}").json()
    tried = {e["key"]: (e["tried"], e["last_status"]) for e in ops["endpoints"]}
    assert tried["GET /documents/{id}"] == (1, 404) and tried["GET /documents"] == (0, None)
    assert tried["DELETE /documents/{id}"] == (0, None) and ops["tried"] == 1

    assert client.post(f"/projects/{pid}/openapi/{spec['id']}/refresh").status_code == 422  # pasted, nothing to fetch
    assert client.delete(f"/projects/{pid}/openapi/{spec['id']}").status_code == 204
    assert client.get(f"/projects/{pid}/openapi").json() == []


def test_import_from_an_address(client, project, monkeypatch):
    real = httpx.Client

    def fake(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(lambda req: httpx.Response(200, text=SPEC) if req.url.path == "/openapi.yaml"
                                                  else httpx.Response(404, text="no"))
        return real(*args, **kwargs)
    monkeypatch.setattr("app.routers.openapi.httpx.Client", fake)
    pid = project["id"]
    ok = client.post(f"/projects/{pid}/openapi", json={"url": "https://kyc.example/openapi.yaml"}).json()
    assert ok["source"] == "https://kyc.example/openapi.yaml" and ok["operations"] == 5
    assert client.post(f"/projects/{pid}/openapi/{ok['id']}/refresh").status_code == 200
    missing = client.post(f"/projects/{pid}/openapi", json={"url": "https://kyc.example/nope.json"})
    assert missing.status_code == 422 and "answered 404" in missing.json()["detail"]
    assert client.post(f"/projects/{pid}/openapi", json={}).status_code == 422
    bad = client.post(f"/projects/{pid}/openapi", json={"text": "title: x"})
    assert bad.status_code == 422 and "isn't an OpenAPI" in bad.json()["detail"]


def test_charters_suggested_from_a_spec_survive_its_deletion(client, project):
    pid = project["id"]
    spec = client.post(f"/projects/{pid}/openapi", json={"text": SPEC}).json()
    assert client.post(f"/projects/{pid}/charters/suggest", json={"artifact_ids": [spec["id"]]}).status_code == 200
    charter = client.post(f"/projects/{pid}/charters", json={"mission_statement": "Explore document upload via the API",
                                                            "source_artifact_id": spec["id"]}).json()
    client.delete(f"/projects/{pid}/openapi/{spec['id']}")
    assert client.get(f"/charters/{charter['id']}").json()["source_artifact_id"] is None
