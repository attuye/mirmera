"""All of a project's regression tests together: run all, history, export."""
import io
import time
import zipfile

from app import models
from app.database import SessionLocal
from app.regression.suite import meaning
from tests.test_regression import App, app_url, bug_present, generate, recorded_bug  # noqa: F401  (fixtures)
from tests.test_runner import chromium_available, headless_runner  # noqa: F401


def finished(client, run, timeout=120.0):
    deadline = time.monotonic() + timeout
    while True:
        r = client.get(f"/suite-runs/{run['id']}").json()
        if r["status"] != "running":
            return r
        assert time.monotonic() < deadline, r
        time.sleep(0.3)


def run_all(client, project, **body):
    r = client.post(f"/projects/{project['id']}/regression-suite", json=body)
    assert r.status_code == 200, r.text
    return finished(client, r.json())


def test_meanings_in_words():
    assert meaning("reproduces", True) == "Bug still there (expected: marked as a known bug)"
    assert meaning("passes", True).startswith("Now passes: the bug may be fixed")
    assert meaning("misses_bug", False) == "The steps never reached the bug"


def test_no_tests_yet_says_how_to_get_one(client, project):
    r = client.post(f"/projects/{project['id']}/regression-suite", json={})
    assert r.status_code == 409 and "Generate one" in r.json()["detail"]
    assert client.get(f"/projects/{project['id']}/regression-tests/export").status_code == 409


def test_run_all_reports_the_bug_still_there_then_fixed(client, project, recorded_bug, monkeypatch):
    monkeypatch.setenv("MIRMERA_SECRET_PASSWORD", "s3cret-pass")
    generate(client, recorded_bug)
    newer = generate(client, recorded_bug)  # only the latest test of a bug runs
    [listed] = client.get(f"/projects/{project['id']}/regression-tests").json()
    assert listed["test_id"] == newer["id"] and listed["outcome"] is None

    run = run_all(client, project)
    assert run["status"] == "done" and run["total"] == 1
    [row] = run["results"]
    assert (row["outcome"], row["test_id"], row["known_failing"]) == ("reproduces", newer["id"], True)
    assert row["meaning"] == "Bug still there (expected: marked as a known bug)"
    assert client.get(f"/projects/{project['id']}/regression-tests").json()[0]["outcome"] == "reproduces"  # the test's own replay too

    App.fixed = True
    row = run_all(client, project)["results"][0]
    assert row["outcome"] == "passes" and "remove test.fail()" in row["meaning"]
    history = client.get(f"/projects/{project['id']}/suite-runs").json()
    assert [h["results"][0]["outcome"] for h in history] == ["passes", "reproduces"]  # newest first


def test_another_address_that_is_down_is_broken_and_says_which_step(client, project, recorded_bug, monkeypatch):
    monkeypatch.setenv("MIRMERA_SECRET_PASSWORD", "s3cret-pass")
    generate(client, recorded_bug)
    run = run_all(client, project, base_url="http://127.0.0.1:9")
    assert run["base_url"] == "http://127.0.0.1:9"
    row = run["results"][0]
    assert row["outcome"] == "broken" and row["detail"].startswith("step 1:")


def test_one_run_at_a_time_and_it_can_be_cancelled(client, project, recorded_bug, monkeypatch):
    monkeypatch.setenv("MIRMERA_SECRET_PASSWORD", "s3cret-pass")
    generate(client, recorded_bug)
    r = client.post(f"/projects/{project['id']}/regression-suite", json={}).json()
    assert client.post(f"/projects/{project['id']}/regression-suite", json={}).status_code == 409
    client.post(f"/suite-runs/{r['id']}/cancel")
    assert finished(client, r)["status"] in ("cancelled", "done")


def test_export_is_a_playwright_project_that_runs_on_its_own(client, project, recorded_bug, session):
    test = generate(client, recorded_bug)
    twin = client.post(f"/sessions/{session['id']}/bugs", json={"title": "Passport verification fails"}).json()
    with SessionLocal() as db:  # a second bug with the same title: same filename
        db.add(models.RegressionTest(bug_id=twin["id"], filename=test["filename"], code=test["code"], plan=test["plan"],
                                     known_failing=False, base_url=test["base_url"]))
        db.get(models.Bug, twin["id"]).kind = "bug"
        db.commit()
    r = client.get(f"/projects/{project['id']}/regression-tests/export")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert 'filename="shopflow-regression-tests.zip"' in r.headers["content-disposition"]
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = sorted(z.namelist())
    assert names == ["README.md", "package.json", "playwright.config.ts",
                     "tests/passport-verification-fails-2.spec.ts", "tests/passport-verification-fails.spec.ts"]
    readme = z.read("README.md").decode()
    assert "`MIRMERA_SECRET_PASSWORD`" in readme and "BASE_URL=" in readme and "test.fail()" in readme
    assert '"@playwright/test"' in z.read("package.json").decode()
    assert z.read("tests/passport-verification-fails.spec.ts").decode() == test["code"]


def test_deleting_the_project_takes_its_suite_runs(client, project, session, recorded_bug, monkeypatch):
    monkeypatch.setenv("MIRMERA_SECRET_PASSWORD", "s3cret-pass")
    generate(client, recorded_bug)
    run_all(client, project)
    client.post(f"/sessions/{session['id']}/runner/stop")  # the recorded browser must be closed first
    r = client.delete(f"/projects/{project['id']}", params={"confirm": "ShopFlow"})
    assert r.status_code == 200, r.text
    d = r.json()["deleted"]
    assert d["suite_runs"] == 1
