"""Notifications: settings, a test message, which events go out, real channels, and the log."""
import httpx
import pytest

from app import notify
from app.config import settings
from app.regression.suite import noteworthy


@pytest.fixture
def mocked(monkeypatch):
    monkeypatch.setattr(settings, "mock_notifications", True)
    notify.MOCK_SENT.clear()
    return notify.MOCK_SENT


def setup(client, project, **body):
    r = client.put(f"/projects/{project['id']}/notifications", json={"telegram_chat_id": "123", "slack": True,
                                                                      "events": list(notify.DEFAULT_EVENTS), **body})
    assert r.status_code == 200, r.text
    return r.json()


def test_settings_and_a_test_message(client, project, mocked):
    out = client.get(f"/projects/{project['id']}/notifications").json()
    assert out["settings"] == {"telegram_chat_id": "", "slack": False, "events": notify.DEFAULT_EVENTS}
    assert out["channels"]["mock"] is True and [e["key"] for e in out["events"]][0] == "fix_failed"
    assert client.post(f"/projects/{project['id']}/notifications/test").status_code == 409  # nothing set up
    assert client.put(f"/projects/{project['id']}/notifications", json={"events": ["vibes"]}).status_code == 400

    setup(client, project)
    out = client.post(f"/projects/{project['id']}/notifications/test").json()
    assert [(m["channel"], m["to"]) for m in mocked] == [("telegram", "123"), ("slack", "webhook")]
    assert mocked[0]["text"].startswith("Mirmera · ShopFlow: Test message")
    assert sorted((r["channel"], r["status"], r["event"]) for r in out["recent"]) == [("slack", "mock", "test"), ("telegram", "mock", "test")]


def test_a_high_severity_bug_is_announced_once_and_only_if_wanted(client, project, session, mocked):
    setup(client, project, slack=False)
    bug = client.post(f"/sessions/{session['id']}/bugs", json={"title": "Coupon total wrong", "severity": "high"}).json()
    review = {"kind": "bug", "expected": "100", "actual": "Total shows 90", "oracle_heuristic": "claims", "oracle_explanation": "Spec"}
    client.patch(f"/bugs/{bug['id']}", json=review)
    assert [m["text"] for m in mocked] == ["Mirmera · ShopFlow: High-severity bug: Coupon total wrong\nTotal shows 90"]
    client.patch(f"/bugs/{bug['id']}", json={"actual": "Total shows 90 on Safari"})  # already high: no repeat
    assert len(mocked) == 1

    setup(client, project, slack=False, events=["fix_failed"])
    other = client.post(f"/sessions/{session['id']}/bugs", json={"title": "Another", "severity": "high"}).json()
    client.patch(f"/bugs/{other['id']}", json=review)
    assert len(mocked) == 1  # high_bug turned off


def test_links_need_the_app_address(client, project, mocked, monkeypatch):
    setup(client, project, slack=False)
    monkeypatch.setattr(settings, "public_app_url", "https://mirmera.example")
    notify.send(project["id"], "fix_failed", "#3 was closed, but the bug still reproduces", "Passport fails", "/regression")
    assert mocked[-1]["text"].endswith(f"Passport fails\nhttps://mirmera.example/projects/{project['id']}/regression")


def test_real_telegram_and_slack_and_failures_without_secrets(client, project, monkeypatch):
    monkeypatch.setattr(settings, "telegram_bot_token", "999:SECRET")
    monkeypatch.setattr(settings, "slack_webhook_url", "https://hooks.slack.com/services/T/B/SECRET")
    calls = []

    def fake_post(url, json, timeout):
        calls.append((url, json))
        if "telegram" in url:
            return httpx.Response(400, json={"ok": False, "description": "Bad Request: chat not found"})
        return httpx.Response(200, text="ok")

    monkeypatch.setattr(notify.httpx, "post", fake_post)
    setup(client, project)
    out = client.post(f"/projects/{project['id']}/notifications/test").json()
    assert calls[0][0] == "https://api.telegram.org/bot999:SECRET/sendMessage" and calls[0][1]["chat_id"] == "123"
    assert calls[1] == ("https://hooks.slack.com/services/T/B/SECRET", {"text": calls[1][1]["text"]})
    by = {r["channel"]: r for r in out["recent"]}
    tg, sl = by["telegram"], by["slack"]
    assert tg["status"] == "failed" and "chat not found" in tg["error"] and "start a chat with the bot" in tg["error"]
    assert "SECRET" not in tg["error"] and sl["status"] == "sent"


def test_channels_say_what_is_missing(client, project, monkeypatch):
    monkeypatch.setattr(settings, "telegram_bot_token", None)
    monkeypatch.setattr(settings, "slack_webhook_url", None)
    ch = client.get(f"/projects/{project['id']}/notifications").json()["channels"]
    assert ch["telegram"] == {"configured": False, "problem": "Add a Telegram bot token (from @BotFather) under Workspace keys."}
    assert ch["slack"]["configured"] is False


def test_what_in_a_regression_run_is_worth_a_message():
    rows = [{"bug_title": "a", "known_failing": True, "outcome": "reproduces"},   # expected
            {"bug_title": "b", "known_failing": True, "outcome": "passes"},       # known bug now passes
            {"bug_title": "c", "known_failing": False, "outcome": "passes"},      # expected
            {"bug_title": "d", "known_failing": False, "outcome": "broken"},      # broke
            {"bug_title": "e", "known_failing": True, "outcome": "misses_bug"}]   # broke
    n = noteworthy(rows)
    assert [r["bug_title"] for r in n["fixed"]] == ["b"] and [r["bug_title"] for r in n["wrong"]] == ["d", "e"]
    assert n["as_expected"] == 2
