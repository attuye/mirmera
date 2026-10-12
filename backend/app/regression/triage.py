"""Why did a regression test go red? A red test isn't always a bug in the app: the test may be
out of date, its data used up, the app not reachable, or the result may just not repeat.

Triage reads what the replay saw (the step that stopped and why, failed requests, page errors,
what the page said) and the test's history, and sorts each unexpected result into one kind,
with the evidence and what to do next. No AI is asked: these are things the browser saw.

Kinds:
- product      the app is wrong: a server error, a page error, or a check that held now fails
- test         the test can't do its steps on a page that loaded fine: a field or button it
               looks for isn't there (renamed, moved), or its steps no longer reach the bug
- data         the app refused the test's data: already used, expired, not found
- environment  the app wasn't reachable, or answered as if down (502/503/504)
- setup        a secret or file the test needs isn't there
- flaky        the result didn't repeat: it changed on an immediate second try, or flips run to run
"""
import re
from urllib.parse import urlsplit

KINDS = {
    "product": "Product bug",
    "test": "Test problem",
    "data": "Test data",
    "environment": "Environment",
    "setup": "Test setup",
    "flaky": "Flaky",
}

UNREACHABLE = re.compile(r"ERR_CONNECTION_REFUSED|ERR_NAME_NOT_RESOLVED|ERR_CONNECTION_TIMED_OUT|ERR_ADDRESS_UNREACHABLE|"
                         r"ERR_INTERNET_DISCONNECTED|ERR_CONNECTION_RESET|ECONNREFUSED|ERR_SSL|ERR_CERT", re.I)
DOWN_STATUS = re.compile(r"-> (502|503|504)$")
SERVER_ERROR = re.compile(r"-> (5\d\d)$")
LOCATOR = re.compile(r"waiting for (get_by_\w+|locator)\((.{1,120}?)\)\s*$|strict mode violation|not visible|"
                     r"element is not (?:attached|enabled|editable)|resolved to \d+ elements|Timeout \d+ms exceeded", re.I | re.M)
DATA_WORDS = re.compile(r"\balready (?:exists|registered|used|verified|taken|in use)\b|\bduplicate\b|\bnot found\b|"
                        r"\bno longer (?:available|valid)\b|\bout of stock\b|\bexpired\b|\binsufficient\b|\blocked\b", re.I)
DATA_STATUS = re.compile(r"-> (409|410|422)$")


def expected(outcome: str, known_failing: bool) -> bool:
    return outcome == "reproduces" if known_failing else outcome == "passes"


def _step_detail(result: dict) -> tuple[dict | None, str]:
    step = next((s for s in result.get("steps") or [] if s["status"] in ("failed", "skipped")), None)
    return step, (step or {}).get("detail", "")


def classify(result: dict, known_failing: bool, *, retry: dict | None = None, history: list[str] | None = None) -> dict | None:
    """The kind of an unexpected result, with why and what to do next; None when the result is the
    expected one. retry: the same test replayed once more straight away. history: earlier
    outcomes of this test, newest first."""
    outcome = result.get("outcome", "error")
    if expected(outcome, known_failing):
        return None
    signals = result.get("signals") or {}
    failed, page_errors = signals.get("failed") or [], signals.get("page_errors") or []
    page = result.get("page") or {}
    said = " ".join(page.get("messages") or [])
    step, detail = _step_detail(result)

    def out(kind: str, why: str, next_step: str) -> dict:
        return {"kind": kind, "label": KINDS[kind], "why": why[:400], "next": next_step[:300]}

    # A result that doesn't repeat says nothing about the app or the test on its own.
    if retry is not None and expected(retry.get("outcome", "error"), known_failing):
        return out("flaky", f"It went {_word(outcome)}, then {_word(retry['outcome'])} on a second try straight away.",
                   "Replay it a few times; look for timing (slow answers, animations) or shared state between runs.")
    flips = flip_count([outcome] + (history or []))
    if flips >= 2:
        return out("flaky", f"Its result changed {flips} times in its last {len((history or [])) + 1} runs: "
                            f"{pattern([outcome] + (history or []), known_failing)} (newest first).",
                   "Don't read this run alone. Find what differs between runs (timing, data left by an earlier run).")

    if outcome == "incomplete":
        return out("setup", f"A step was skipped: {detail or 'a secret or a file is missing'}.",
                   "Set the secret in backend/.env, or add the file under fixtures/, and run it again.")

    opening = (step or {}).get("description", "").startswith("open ")
    unreachable = next((f for f in failed if UNREACHABLE.search(f)), None) or \
        (detail if UNREACHABLE.search(detail) or (opening and "net::ERR_" in detail) else None)
    down = next((f for f in failed if DOWN_STATUS.search(f)), None)
    if unreachable or (outcome in ("broken", "error") and down):
        what = unreachable or down
        return out("environment", f"The app wasn't reachable: {_short(what)}.",
                   "Check the app is running at that address (and the VPN or network), then run it again.")

    data_hit = DATA_WORDS.search(said) or next((f for f in failed if DATA_STATUS.search(f)), None)
    if outcome in ("broken", "misses_bug", "passes", "reproduces") and data_hit and not page_errors and \
            not any(SERVER_ERROR.search(f) for f in failed):
        evidence = f'the page said "{said[:160]}"' if DATA_WORDS.search(said) else _short(data_hit)
        return out("data", f"The app refused the test's data: {evidence}.",
                   "Give the test fresh data (a new name or number each run), or reset the data it uses.")

    server = next((f for f in failed if SERVER_ERROR.search(f)), None)
    if outcome in ("broken", "error") and (server or page_errors):
        return out("product", f"A step failed and the app broke on the way: {_short(server or page_errors[0])}.",
                   "Look at the server or page error first: that's likely a bug in the app.")
    if outcome == "broken":
        if LOCATOR.search(detail):
            target = (step or {}).get("description", "a step")
            return out("test", f"Step {step['n'] if step else '?'} ({target}) couldn't find what it acts on, on a page that "
                               f"loaded fine{_at(page)}.",
                       "The page changed (a renamed label, a moved button) or the locator is fragile: update the test's step.")
        return out("test", f"Step {step['n'] if step else '?'} failed: {_short(detail)}.",
                   "Replay it on the Bugs page and see where it stops; update the step if the app changed on purpose.")
    if outcome == "misses_bug":
        return out("test", "Its steps ran but never sent the request the bug is about: the flow changed.",
                   "Re-record the steps to the bug, or edit them on the Bugs page.")
    if outcome == "reproduces":  # a check that held fails now: the app changed
        failing = next((c for c in result.get("assertions") or [] if c["status"] == "failed"), None)
        evidence = (failing or {}).get("detail") or _short(server or (page_errors or [""])[0])
        return out("product", f"A check that held fails now: {_short(evidence)}.",
                   "A regression: compare with the last passing run, and file it if it isn't intended.")
    if outcome == "passes" and known_failing:
        return None  # "may be fixed" is news, not a failure; the page says so already
    if outcome == "error":
        return out("environment", f"The replay couldn't run: {_short(result.get('detail') or detail)}.",
                   "Run it again; if it keeps failing, check Mirmera's browser (Chromium) on this machine.")
    return out("test", f"Unexpected result: {outcome}.", "Replay it on the Bugs page.")


def flip_count(outcomes: list[str]) -> int:
    """How often the result changed between consecutive runs (broken, error and incomplete count
    as runs that say nothing either way, so they are left out)."""
    seen = [o for o in outcomes if o in ("reproduces", "passes")]
    return sum(1 for a, b in zip(seen, seen[1:]) if a != b)


def flakiness(outcomes: list[str], known_failing: bool) -> dict | None:
    """For the tests list: a test whose result flips is flaky. outcomes: newest first."""
    flips = flip_count(outcomes)
    if flips < 2:
        return None
    runs = [o for o in outcomes if o in ("reproduces", "passes")]
    shows = sum(1 for o in runs if o == "reproduces")
    return {"flips": flips, "runs": len(runs), "pattern": pattern(outcomes, known_failing),
            "summary": f"the bug showed in {shows} of its last {len(runs)} runs"}


def pattern(outcomes: list[str], known_failing: bool) -> str:
    """✓ for an expected result, ✗ for an unexpected one, newest first."""
    return "".join("✓" if expected(o, known_failing) else "✗" for o in outcomes if o in ("reproduces", "passes"))[:12]


def _word(outcome: str) -> str:
    return {"reproduces": "red (the bug showed)", "passes": "green", "broken": "broken", "error": "not run"}.get(outcome, outcome)


def _short(text: str | None) -> str:
    text = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    m = re.match(r"(\w+) (\S+) -> (.+)$", text)
    if m:  # a request: its path, not the whole address
        return f"{m.group(1)} {urlsplit(m.group(2)).path or '/'} answered {m.group(3)}"
    return text[:200]


def _at(page: dict) -> str:
    return f" ({urlsplit(page['url']).path or '/'})" if page.get("url") else ""
