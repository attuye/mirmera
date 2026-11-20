"""The network log of a run, kept without anyone's access: cookies, authorization, passwords,
tokens, known secrets and pictures are taken out; everything else stays as the browser saw it."""
import json

from app.har import MASK, clean_har, clean_har_file


def entry(**over):
    e = {
        "startedDateTime": "2026-11-21T10:00:00Z", "time": 12,
        "request": {
            "method": "POST", "url": "https://kyc.example/api/verify?token=abc123&lang=am",
            "headers": [{"name": "Cookie", "value": "sid=xyz"}, {"name": "Authorization", "value": "Bearer abc"},
                        {"name": "Content-Type", "value": "application/json"}],
            "cookies": [{"name": "sid", "value": "xyz"}],
            "queryString": [{"name": "token", "value": "abc123"}, {"name": "lang", "value": "am"}],
            "postData": {"mimeType": "application/json",
                         "text": json.dumps({"fullName": "Abebe Kebede", "password": "hunter2", "pin": "1234"})},
        },
        "response": {
            "status": 500, "headers": [{"name": "Set-Cookie", "value": "sid=new"}, {"name": "Content-Type", "value": "application/json"}],
            "cookies": [{"name": "sid", "value": "new"}],
            "content": {"mimeType": "application/json", "size": 60,
                        "text": json.dumps({"error": "boom", "access_token": "tok-999", "echo": "s3cret-pass"})},
        },
    }
    e.update(over)
    return e


def test_secrets_are_taken_out_and_the_rest_stays():
    har = clean_har({"log": {"version": "1.2", "entries": [entry()]}}, secrets=["s3cret-pass"])
    e = har["log"]["entries"][0]
    req, res = e["request"], e["response"]
    assert {h["name"]: h["value"] for h in req["headers"]} == {"Cookie": MASK, "Authorization": MASK, "Content-Type": "application/json"}
    assert req["cookies"][0]["value"] == MASK and res["cookies"][0]["value"] == MASK
    assert "token=%E2%80%A2" in req["url"] or f"token={MASK}" in req["url"]
    assert "abc123" not in json.dumps(har) and "lang=am" in req["url"]
    assert {p["name"]: p["value"] for p in req["queryString"]} == {"token": MASK, "lang": "am"}
    body = json.loads(req["postData"]["text"])
    assert body == {"fullName": "Abebe Kebede", "password": MASK, "pin": MASK}
    assert {h["name"]: h["value"] for h in res["headers"]}["Set-Cookie"] == MASK
    answer = json.loads(res["content"]["text"])
    assert answer == {"error": "boom", "access_token": MASK, "echo": MASK}
    assert res["status"] == 500 and e["time"] == 12
    assert har["log"]["creator"]["name"] == "Mirmera"


def test_pictures_and_files_are_left_out():
    """A KYC answer is often someone's ID or face: a log only keeps that a picture came back."""
    picture = entry(response={"status": 200, "headers": [], "cookies": [],
                              "content": {"mimeType": "image/jpeg", "size": 48213, "encoding": "base64", "text": "/9j/4AAQSk"}})
    form = entry()
    form["request"]["postData"] = {"mimeType": "application/x-www-form-urlencoded", "text": "name=Abebe&password=hunter2",
                                   "params": [{"name": "name", "value": "Abebe"}, {"name": "password", "value": "hunter2"}]}
    har = clean_har({"log": {"entries": [picture, form]}})
    content = har["log"]["entries"][0]["response"]["content"]
    assert "text" not in content and content["size"] == 48213 and content["mimeType"] == "image/jpeg"
    assert "ID or face" in content["comment"]
    post = har["log"]["entries"][1]["request"]["postData"]
    assert post["text"] == f"name=Abebe&password={MASK}"
    assert post["params"] == [{"name": "name", "value": "Abebe"}, {"name": "password", "value": MASK}]
    assert "hunter2" not in json.dumps(har)


def test_a_long_answer_is_cut(tmp_path):
    big = entry(response={"status": 200, "headers": [], "cookies": [],
                          "content": {"mimeType": "text/html", "text": "<p>" + "x" * 300_000}})
    raw, kept = tmp_path / "raw.har", tmp_path / "kept.har"
    raw.write_text(json.dumps({"log": {"entries": [big]}}))
    assert clean_har_file(raw, kept) == 1
    content = json.loads(kept.read_text())["log"]["entries"][0]["response"]["content"]
    assert len(content["text"]) == 200_000 and "Cut by Mirmera" in content["comment"]
