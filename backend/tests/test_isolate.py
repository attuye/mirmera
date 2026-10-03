"""Isolate the bug: change one recorded value at a time and see which changes make
the bug go away. Real headless Chromium against the planted-bug app."""
import time

import pytest

from app.regression.isolate import plan_variants, summarise
from tests.test_bug_steps import APP, check, click, fill, secret, select
from tests.test_regression import App, app_url, bug_present, recorded_bug  # noqa: F401  (fixtures)
from tests.test_runner import chromium_available, headless_runner  # noqa: F401


def finished(client, run, timeout=120.0):
    deadline = time.monotonic() + timeout
    while True:
        r = client.get(f"/isolation-runs/{run['id']}").json()
        if r["status"] != "running":
            return r
        assert time.monotonic() < deadline, f"still running: {r}"
        time.sleep(0.3)


def start(client, bug, **body):
    r = client.post(f"/bugs/{bug['id']}/isolation", json=body)
    assert r.status_code == 200, r.text
    return r.json()


# ---------- planning (no browser) ----------

def test_variations_for_the_geez_attempt():
    steps = [{"type": "goto", "url": f"{APP}/verify"}] + [{"type": "action", "action": a} for a in (
        fill("Full name", "አበበ ከበደ"), secret("Create a password", "MIRMERA_SECRET_PW"), select("Document type", "Passport"),
        fill("Document number", "ጵዕ64622"), fill("Expiry date", "2026-10-22"), check("I accept the terms"),
        click("button", "Verify"))]
    plan = plan_variants(steps, {3: ["National ID", "Passport", "Driver's licence"]})
    changes = [(v["field"], v["change"]) for v in plan]
    assert changes == [
        ('label "Full name"', '"Abebe Kebede" instead of "አበበ ከበደ"'),
        ('label "Full name"', 'left empty instead of "አበበ ከበደ"'),
        ('label "Document type"', '"National ID" instead of "Passport"'),
        ('label "Document type"', '"Driver\'s licence" instead of "Passport"'),
        ('label "Document number"', '"Test 123" instead of "ጵዕ64622"'),
        ('label "Document number"', 'left empty instead of "ጵዕ64622"'),
        ('label "Expiry date"', 'a past date (2020-01-01) instead of "2026-10-22"'),
        ('label "Expiry date"', 'a far-future date (2035-12-31) instead of "2026-10-22"'),
        ('label "I accept the terms"', "left unticked"),
    ]  # the password (a secret) and the click are never varied
    assert plan[0]["replace"]["action"]["summary"] == 'fill label "Full name" = "Abebe Kebede"'
    assert plan[-1]["replace"] is None  # the tick step is skipped


def test_the_summary_says_what_the_bug_depends_on():
    s = summarise([
        {"field": 'label "Document type"', "change": '"National ID" instead of "Passport"', "result": "gone"},
        {"field": 'label "Full name"', "change": "left empty", "result": "bug"},
        {"field": 'label "I accept the terms"', "change": "left unticked", "result": "inconclusive"},
    ])
    assert s == {"depends_on": [{"field": 'label "Document type"', "gone_with": ['"National ID" instead of "Passport"']}],
                 "irrelevant": ['label "Full name"'], "inconclusive": ['label "I accept the terms"']}


# ---------- runs (real browser) ----------

def test_the_passport_bug_depends_on_the_document_type_only(client, recorded_bug, monkeypatch):
    monkeypatch.setenv("MIRMERA_SECRET_PASSWORD", "s3cret-pass")
    run = finished(client, start(client, recorded_bug))
    assert run["status"] == "done", run["detail"]
    assert run["symptom"] == {"kind": "failed_request", "value": "/api/verify"}  # from the recording
    assert run["baseline"]["result"] == "bug" and "/api/verify -> 500" in run["baseline"]["detail"]
    results = {(v["field"], v["change"]): v["result"] for v in run["variants"]}
    assert results[('label "Document type"', '"National ID" instead of "Passport"')] == "gone"
    assert run["summary"]["depends_on"] == [{"field": 'label "Document type"', "gone_with": ['"National ID" instead of "Passport"']}]
    assert {'label "Full name"', 'label "Expiry date"'} <= set(run["summary"]["irrelevant"])
    assert run["total"] == len(run["variants"])


def test_a_text_on_the_page_can_show_the_bug(client, recorded_bug, monkeypatch):
    monkeypatch.setenv("MIRMERA_SECRET_PASSWORD", "s3cret-pass")
    run = finished(client, start(client, recorded_bug, symptom={"kind": "text", "value": "Something went wrong"}))
    assert run["status"] == "done", run["detail"]
    assert [d["field"] for d in run["summary"]["depends_on"]] == ['label "Document type"']


def test_a_fixed_app_has_nothing_to_isolate(client, recorded_bug, monkeypatch):
    monkeypatch.setenv("MIRMERA_SECRET_PASSWORD", "s3cret-pass")
    App.fixed = True
    run = finished(client, start(client, recorded_bug))
    assert run["status"] == "not_reproduced" and run["variants"] == []
    assert "did not show the bug" in run["detail"]


def test_a_missing_secret_stops_the_baseline_with_the_reason(client, recorded_bug, monkeypatch):
    monkeypatch.delenv("MIRMERA_SECRET_PASSWORD", raising=False)
    run = finished(client, start(client, recorded_bug))
    assert run["status"] == "error" and "MIRMERA_SECRET_PASSWORD" in run["detail"]


def test_one_run_at_a_time_and_the_latest_is_shown(client, recorded_bug, monkeypatch):
    monkeypatch.setenv("MIRMERA_SECRET_PASSWORD", "s3cret-pass")
    run = start(client, recorded_bug)
    assert client.post(f"/bugs/{recorded_bug['id']}/isolation", json={}).status_code == 409
    assert client.post(f"/isolation-runs/{run['id']}/cancel").status_code == 200
    done = finished(client, run)
    assert done["status"] in ("cancelled", "done", "not_reproduced")
    assert client.get(f"/bugs/{recorded_bug['id']}/isolation").json()["id"] == run["id"]


def test_without_steps_or_a_symptom_it_says_what_is_missing(client, session):
    bug = client.post(f"/sessions/{session['id']}/bugs", json={"title": "Nothing recorded"}).json()
    r = client.post(f"/bugs/{bug['id']}/isolation", json={})
    assert r.status_code == 409 and "Steps to the bug" in r.json()["detail"]
    client.put(f"/bugs/{bug['id']}/steps", json={"steps": [{"type": "goto", "url": "http://localhost:4000/"}]})
    r = client.post(f"/bugs/{bug['id']}/isolation", json={})
    assert r.status_code == 409 and "Say what shows the bug" in r.json()["detail"]
