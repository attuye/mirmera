"""Why a regression test went red: product, test, data, environment, setup, or flaky. Read from
what the replay saw, without AI."""
import pytest

from app.regression.triage import classify, flakiness, flip_count, pattern
from app.regression.suite import noteworthy


def result(outcome, *, step=None, failed=(), page_errors=(), messages=(), checks=()):
    steps = [{"n": 1, "description": "open http://shop/", "status": "ok", "detail": ""}]
    if step:
        steps.append({"n": 2, "description": step[0], "status": step[1], "detail": step[2]})
    return {"outcome": outcome, "steps": steps,
            "assertions": [{"n": 1, "kind": "text", "status": s, "detail": d} for s, d in checks],
            "signals": {"failed": list(failed), "page_errors": list(page_errors), "console_errors": []},
            "page": {"url": "http://shop/order", "title": "Order", "messages": list(messages)}}


def test_an_expected_result_needs_no_triage():
    assert classify(result("reproduces"), known_failing=True) is None
    assert classify(result("passes"), known_failing=False) is None
    assert classify(result("passes"), known_failing=True) is None  # a known bug passing is news, not a failure


@pytest.mark.parametrize("case,kind,why", [
    (result("broken", step=("open http://shop/", "failed", "Page.goto: net::ERR_CONNECTION_REFUSED at http://shop/"),
            failed=["GET http://shop/ -> net::ERR_CONNECTION_REFUSED"]),
     "environment", "The app wasn't reachable: GET / answered net::ERR_CONNECTION_REFUSED"),
    (result("broken", step=("click button \"Pay\"", "failed", "Timeout 10000ms exceeded."), failed=["GET http://shop/api/cart -> 503"]),
     "environment", "GET /api/cart answered 503"),
    (result("incomplete", step=("fill label \"Password\"", "skipped", "needs MIRMERA_SECRET_PASSWORD")),
     "setup", "needs MIRMERA_SECRET_PASSWORD"),
    (result("broken", step=("click button \"Next\"", "failed", "Timeout 10000ms exceeded."),
            messages=["This email is already registered."]),
     "data", 'the page said "This email is already registered."'),
    (result("misses_bug", failed=["POST http://shop/api/orders -> 409"]),
     "data", "POST /api/orders answered 409"),
    (result("broken", step=("click button \"Pay\"", "failed", "Timeout 10000ms exceeded."),
            failed=["POST http://shop/api/pay -> 500"]),
     "product", "POST /api/pay answered 500"),
    (result("broken", step=('fill label "Full name"', "failed",
                            'Locator.fill: Timeout 10000ms exceeded.\nCall log:\n  - waiting for get_by_label("Full name", exact=True)')),
     "test", 'Step 2 (fill label "Full name") couldn\'t find what it acts on, on a page that loaded fine (/order)'),
    (result("misses_bug"), "test", "never sent the request the bug is about"),
    (result("reproduces", checks=[("failed", 'expected "Order placed", got "Something went wrong"')]),
     "product", 'A check that held fails now: expected "Order placed"'),
])
def test_each_kind_of_red(case, kind, why):
    t = classify(case, known_failing=False)
    assert t["kind"] == kind and why in t["why"], t
    assert t["next"] and t["label"]


def test_a_result_that_doesnt_repeat_is_flaky_not_a_bug():
    t = classify(result("passes"), known_failing=True, retry=result("reproduces"))
    assert t["kind"] == "flaky" and t["why"] == "It went green, then red (the bug showed) on a second try straight away."
    t = classify(result("reproduces"), known_failing=False, history=["passes", "reproduces", "passes"])
    assert t["kind"] == "flaky" and "changed 3 times in its last 4 runs: ✗✓✗✓" in t["why"]


def test_flakiness_over_the_history():
    assert flip_count(["reproduces", "broken", "reproduces", "passes"]) == 1  # broken runs say nothing either way
    assert flakiness(["reproduces", "reproduces", "passes"], True) is None  # one change: maybe fixed, then back
    f = flakiness(["reproduces", "passes", "reproduces", "passes"], True)
    assert f == {"flips": 3, "runs": 4, "pattern": "✓✗✓✗", "summary": "the bug showed in 2 of its last 4 runs"}
    assert pattern(["passes", "error", "reproduces"], False) == "✓✗"


def test_a_flaky_result_is_no_alarm():
    rows = [{"bug_title": "A", "known_failing": True, "outcome": "passes", "triage": {"kind": "flaky"}},
            {"bug_title": "B", "known_failing": True, "outcome": "passes", "triage": None},
            {"bug_title": "C", "known_failing": False, "outcome": "broken", "triage": {"kind": "test"}}]
    n = noteworthy(rows)
    assert [r["bug_title"] for r in n["flaky"]] == ["A"] and [r["bug_title"] for r in n["fixed"]] == ["B"]
    assert [r["bug_title"] for r in n["wrong"]] == ["C"] and n["as_expected"] == 0
