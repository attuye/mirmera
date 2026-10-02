"""Chrome extension endpoints: the server sanitizes and redacts whatever the
extension sends, because the everyday browser carries real sessions."""
import base64
from pathlib import Path

import pytest

from app.config import settings
from app.routers import extension

PNG = base64.b64decode(  # a 1x1 transparent PNG
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=")
JPEG_HEAD = b"\xff\xd8\xff\xe0" + b"\x00" * 16


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "evidence_dir", str(tmp_path / "evidence"))
    extension.LAST_SEEN.clear()


def send(client, session, *events):
    return client.post(f"/sessions/{session['id']}/extension/events", json={"events": list(events)})


def evidence(client, session, kind):
    return client.get(f"/sessions/{session['id']}/evidence?kind={kind}").json()


def data_url(raw: bytes, mime="image/png"):
    return f"data:{mime};base64," + base64.b64encode(raw).decode()


# ---------- sessions ----------

def test_active_sessions_lists_running_ones_only(client, charter, session):
    other = client.post("/sessions", json={"charter_id": charter["id"], "tester_id": "attu"}).json()
    client.post(f"/sessions/{other['id']}/pause")
    ended = client.post("/sessions", json={"charter_id": charter["id"], "tester_id": "attu"}).json()
    client.post(f"/sessions/{ended['id']}/end")
    listed = {s["id"]: s for s in client.get("/sessions/active").json()}
    assert set(listed) == {session["id"], other["id"]}
    assert listed[other["id"]]["paused"] is True
    assert listed[session["id"]]["charter_mission"] == "Explore checkout with coupons"


def test_an_ended_session_takes_nothing(client, session):
    client.post(f"/sessions/{session['id']}/end")
    r = send(client, session, {"kind": "navigation", "url": "https://app.test/"})
    assert r.status_code == 409 and "has ended" in r.json()["detail"]
    assert client.post("/sessions/nope/extension/events", json={"events": []}).status_code == 404


# ---------- events ----------

def test_urls_lose_query_values_and_fragments(client, session):
    send(client, session, {"kind": "navigation", "url": "https://app.test/reset?token=abc123&email=a%40b.et#step2"})
    [page] = evidence(client, session, "navigation")
    assert page["url"] == "https://app.test/reset?token=…&email=…"


def test_failed_requests_are_summarised_by_the_server(client, session):
    r = send(client, session,
             {"kind": "failed_request", "url": "https://app.test/api/verify?session=s3cr3t", "method": "post", "status": 500},
             {"kind": "failed_request", "url": "https://app.test/api/next", "method": "GET", "error": "net::ERR_CONNECTION_RESET"},
             {"kind": "failed_request", "url": "https://app.test/api/ok", "method": "GET", "status": 200},
             {"kind": "failed_request", "url": "https://app.test/x", "method": "GET", "error": "net::ERR_ABORTED"})
    assert r.json() == {"stored": 2, "ignored": 2}
    assert [e["summary"] for e in evidence(client, session, "failed_request")] == [
        "POST https://app.test/api/verify?session=… → 500",
        "GET https://app.test/api/next → net::ERR_CONNECTION_RESET",
    ]


def test_errors_are_counted_and_chrome_duplicates_dropped(client, session):
    send(client, session,
         {"kind": "console_error", "message": "Upload widget failed to init", "url": "https://app.test/"},
         {"kind": "console_error", "message": "Upload widget failed to init", "url": "https://app.test/"},
         {"kind": "console_error", "message": "Failed to load resource: the server responded with a status of 500"},
         {"kind": "page_error", "message": "Cannot read properties of undefined (reading 'map')"})
    [console] = evidence(client, session, "console_error")
    assert (console["summary"], console["count"]) == ("Upload widget failed to init", 2)
    assert evidence(client, session, "page_error")[0]["summary"] == "Cannot read properties of undefined (reading 'map')"


def test_actions_go_through_the_shared_sanitizer(client, session):
    r = send(client, session,
             {"kind": "action", "url": "https://app.test/verify?x=1",
              "action": {"action": "fill", "locator": {"method": "label", "value": "Full name"}, "value": "Abebe"}},
             {"kind": "action", "action": {"action": "fill", "locator": {"method": "label", "value": "Password"},
                                           "masked": True, "secret": "MIRMERA_SECRET_PASSWORD", "value": "leaked!"}},
             {"kind": "action", "action": {"action": "fill", "locator": {"method": "label", "value": "PIN"},
                                           "masked": True, "secret": "rm -rf /"}},
             {"kind": "action", "action": {"action": "eval", "locator": {"method": "label", "value": "x"}}},
             {"kind": "action", "action": {"action": "click", "locator": {"method": "xpath", "value": "//a"}}})
    assert r.json() == {"stored": 3, "ignored": 2}
    actions = evidence(client, session, "action")
    assert [a["summary"] for a in actions] == [
        'fill label "Full name" = "Abebe"',
        'fill label "Password" = (hidden: MIRMERA_SECRET_PASSWORD)',
        'fill label "PIN" = (hidden: MIRMERA_SECRET_VALUE)',  # a secret name not in the fixed form is replaced
    ]
    assert actions[0]["url"] == "https://app.test/verify?x=…"
    from app.database import SessionLocal
    from app import models
    with SessionLocal() as db:
        stored = str([e.data for e in db.query(models.Evidence).all()])
    assert "leaked!" not in stored and "rm -rf" not in stored


def test_a_batch_is_capped(client, session):
    events = [{"kind": "navigation", "url": f"https://app.test/{i}"} for i in range(201)]
    assert send(client, session, *events).status_code == 422


def test_the_session_screen_knows_the_extension_is_recording(client, session):
    status = lambda: client.get(f"/sessions/{session['id']}/runner").json()["extension_active"]
    assert status() is False
    send(client, session, {"kind": "navigation", "url": "https://app.test/"})
    assert status() is True
    client.post(f"/sessions/{session['id']}/extension/stop")
    assert status() is False


# ---------- screenshots ----------

def test_a_screenshot_is_saved_and_linked_to_its_bug(client, session):
    bug = client.post(f"/sessions/{session['id']}/bugs", json={"title": "Upload fails"}).json()
    shot = client.post(f"/sessions/{session['id']}/extension/screenshot", json={
        "image": data_url(PNG), "caption": "When logged: Upload fails", "bug_id": bug["id"],
        "url": "https://app.test/upload?token=abc"}).json()
    assert (shot["kind"], shot["bug_id"], shot["url"]) == ("screenshot", bug["id"], "https://app.test/upload?token=…")
    image = client.get(f"/evidence/{shot['id']}/file")
    assert image.headers["content-type"] == "image/png" and image.content == PNG
    assert client.get(f"/bugs/{bug['id']}").json()["evidence"][0]["id"] == shot["id"]


def test_jpeg_screenshots_are_served_as_jpeg(client, session):
    shot = client.post(f"/sessions/{session['id']}/extension/screenshot", json={"image": data_url(JPEG_HEAD, "image/jpeg")}).json()
    assert shot["summary"] == "Screenshot from Chrome"
    assert client.get(f"/evidence/{shot['id']}/file").headers["content-type"] == "image/jpeg"


@pytest.mark.parametrize("image, status", [
    ("data:image/gif;base64,R0lGODlh", 422),            # not PNG or JPEG
    ("data:image/png;base64,not-base64!!", 422),        # broken base64
    (data_url(b"<svg onload=alert(1)>"), 422),          # says PNG, isn't
])
def test_screenshots_must_be_real_images(client, session, image, status):
    assert client.post(f"/sessions/{session['id']}/extension/screenshot", json={"image": image}).status_code == status


def test_screenshots_have_a_size_limit_and_stay_in_their_session(client, charter, session, monkeypatch):
    monkeypatch.setattr(extension, "MAX_SCREENSHOT_BYTES", 10)
    assert client.post(f"/sessions/{session['id']}/extension/screenshot", json={"image": data_url(PNG)}).status_code == 413
    monkeypatch.setattr(extension, "MAX_SCREENSHOT_BYTES", 5_000_000)
    other = client.post("/sessions", json={"charter_id": charter["id"], "tester_id": "attu"}).json()
    foreign_bug = client.post(f"/sessions/{other['id']}/bugs", json={"title": "Elsewhere"}).json()
    r = client.post(f"/sessions/{session['id']}/extension/screenshot", json={"image": data_url(PNG), "bug_id": foreign_bug["id"]})
    assert r.status_code == 404


# ---------- the extension ships the same recorder ----------

def test_the_extension_uses_the_same_recorder_as_the_recorded_browser():
    root = Path(__file__).resolve().parents[2]
    assert (root / "extension" / "recorder.js").read_text() == (root / "backend" / "app" / "recorder.js").read_text(), \
        "extension/recorder.js differs from backend/app/recorder.js: copy the backend file over"
