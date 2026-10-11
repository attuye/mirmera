"""Replay a resolved plan against the app in a headless browser, with the same
semantics as the rendered TypeScript test, to check what it does today:

  reproduces  every step ran and an assertion failed: the test catches the bug
  passes      every step ran and every assertion passed: the bug did not show
              (fixed, not reproducible this way, or the assertions miss it)
  broken      a step could not run (element not found, page changed...)
  incomplete  a step was skipped (a secret or a file the replay doesn't have)
  misses_bug  every step ran, but the request the bug was recorded on (e.g.
              POST /api/verify -> 500) was never sent: the steps don't reach the
              bug, so whatever the assertions say proves nothing

It runs on the runner's background event loop, in its own browser, separate
from the tester's recorded browser."""
import asyncio
import os
import time
from urllib.parse import urlsplit

from app.regression.plan import describe, is_same_origin

STEP_TIMEOUT = 10_000
ASSERT_TIMEOUT = 5_000


def _rebase(url: str, plan_base: str | None, base_url: str | None) -> str:
    """Point recorded URLs at the replay's base URL (e.g. staging instead of local)."""
    if base_url and plan_base and is_same_origin(url, plan_base):
        p = urlsplit(url)
        return base_url.rstrip("/") + (p.path or "/") + (f"?{p.query}" if p.query else "") + (f"#{p.fragment}" if p.fragment else "")
    return url


def _locator(page, loc: dict):
    m = loc.get("method")
    if m == "role":
        return page.get_by_role(loc.get("role"), name=loc["name"], exact=True) if loc.get("name") else page.get_by_role(loc.get("role"))
    if m == "label":
        return page.get_by_label(loc.get("value"), exact=True)
    if m == "placeholder":
        return page.get_by_placeholder(loc.get("value"), exact=True)
    if m == "text":
        return page.get_by_text(loc.get("value"), exact=True)
    if m == "testid":
        return page.get_by_test_id(loc.get("value"))
    return page.locator(loc.get("value"))


def _first_line(err: Exception) -> str:
    text = str(err).strip().splitlines()
    return text[0][:300] if text else type(err).__name__


async def _run_action(page, a: dict) -> str | None:
    """Performs one recorded action. Returns a reason if it has to be skipped."""
    if a.get("in_frame"):
        return "happened inside a frame, which replay does not support yet"
    target = _locator(page, a.get("locator") or {})
    act = a.get("action")
    if act == "click":
        await target.click(timeout=STEP_TIMEOUT)
        try:  # a submit's request answered (and a redirect started) before the next step, as for a user
            await page.wait_for_load_state("networkidle", timeout=3_000)
        except Exception:
            pass
    elif act == "dblclick":  # a user's double click: two clicks before the page answers
        await target.dblclick(timeout=STEP_TIMEOUT)
        try:  # let both requests reach the app before the next step moves on
            await page.wait_for_load_state("networkidle", timeout=5_000)
        except Exception:
            pass
    elif act == "fill" and a.get("masked"):
        secret = a.get("secret") or ""
        if secret not in os.environ:
            return f"needs {secret}: set it in the backend's environment to replay this step"
        await target.fill(os.environ[secret], timeout=STEP_TIMEOUT)
    elif act == "fill":
        await target.fill(a.get("value") or "", timeout=STEP_TIMEOUT)
    elif act == "select":
        await target.select_option(a.get("value"), timeout=STEP_TIMEOUT)
    elif act == "check":
        await target.check(timeout=STEP_TIMEOUT)
    elif act == "uncheck":
        await target.uncheck(timeout=STEP_TIMEOUT)
    elif act == "press":
        await target.press(a.get("key") or "Enter", timeout=STEP_TIMEOUT)
    elif act == "upload":
        return "uploads a file; the rendered test reads it from a fixtures/ folder"
    return None


async def _check(page, a: dict, console_errors: list[str], failed: list[str],
                 page_errors: list[str] | None = None) -> tuple[bool, str]:
    from playwright.async_api import expect
    kind = a["kind"]
    try:
        if kind == "visible":
            await expect(_locator(page, a["locator"])).to_be_visible(timeout=ASSERT_TIMEOUT)
        elif kind == "hidden":
            await expect(_locator(page, a["locator"])).to_be_hidden(timeout=ASSERT_TIMEOUT)
        elif kind == "text":
            await expect(_locator(page, a["locator"])).to_contain_text(a["expected"], timeout=ASSERT_TIMEOUT)
        elif kind == "value":
            await expect(_locator(page, a["locator"])).to_have_value(a["expected"], timeout=ASSERT_TIMEOUT)
        elif kind == "url":
            deadline = time.monotonic() + ASSERT_TIMEOUT / 1000
            while a["expected"] not in page.url:
                if time.monotonic() > deadline:
                    return False, f"URL is {page.url}, expected it to contain {a['expected']}"
                await page.wait_for_timeout(200)
        elif kind == "no_console_errors":
            if console_errors:
                return False, "Console errors: " + "; ".join(console_errors[:3])
        elif kind == "no_page_errors":
            if page_errors:
                return False, "Page errors: " + "; ".join(page_errors[:3])
        elif kind == "no_failed_requests":
            hits = [r for r in failed if (a.get("url_contains") or "") in r]
            if hits:
                return False, "Failed requests: " + "; ".join(hits[:3])
        return True, "passed"
    except AssertionError as err:
        return False, _first_line(err)
    except Exception as err:  # e.g. a locator matching several elements
        return False, _first_line(err)


class Signals:
    """Console errors, uncaught page errors and failed requests seen on a page,
    in order. Shared by replays and the reproduction agent."""

    def __init__(self) -> None:
        self.console_errors: list[str] = []
        self.page_errors: list[str] = []
        self.failed: list[str] = []
        self.in_flight = 0  # requests sent and not yet answered
        self.requested: list[str] = []  # every request URL, to check a test reaches the bug

    def attach(self, page) -> None:
        page.on("request", lambda r: (self._count(+1), self.requested.append(r.url)))
        page.on("requestfinished", lambda r: self._count(-1))
        page.on("requestfailed", lambda r: self._count(-1))
        page.on("console", lambda m: m.type == "error" and not m.text.startswith("Failed to load resource")
                and self.console_errors.append(m.text))
        page.on("pageerror", lambda e: self.page_errors.append(str(e)))
        page.on("response", lambda r: r.status >= 400 and self.failed.append(f"{r.request.method} {r.url} -> {r.status}"))
        page.on("requestfailed", lambda r: "ERR_ABORTED" not in (r.failure or "")
                and self.failed.append(f"{r.method} {r.url} -> {r.failure}"))

    def _count(self, change: int) -> None:
        self.in_flight = max(0, self.in_flight + change)

    async def settle(self, quiet_ms: int = 500, timeout_ms: int = 3_000) -> None:
        """Waits until no request has been in flight for quiet_ms, so the answer to
        what the last click sent is in before anyone looks; at most timeout_ms, so a
        page that never goes quiet (polling) only costs that.
        (Playwright's "networkidle" can't do this: once a page has reached it on load,
        it returns at once, even with a request just sent.)"""
        deadline = time.monotonic() + timeout_ms / 1000
        quiet_since = None
        while time.monotonic() < deadline:
            if self.in_flight:
                quiet_since = None
            elif quiet_since is None:
                quiet_since = time.monotonic()
            elif time.monotonic() - quiet_since >= quiet_ms / 1000:
                return
            await asyncio.sleep(0.05)


async def run_step(page, step: dict, plan_base: str | None, base_url: str | None) -> str | None:
    """Runs one resolved plan step. Returns a reason when it has to be skipped;
    raises when it fails."""
    if step["type"] == "goto":
        url = _rebase(step["url"], plan_base, base_url)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        except Exception as err:
            # The page was still moving on by itself (a form that redirects once its request is
            # answered): let it arrive, then open the address again.
            if "ERR_ABORTED" not in str(err) and "interrupted by another navigation" not in str(err):
                raise
            try:
                await page.wait_for_load_state("networkidle", timeout=5_000)
            except Exception:
                pass
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        return None
    return await _run_action(page, step["action"])


async def replay(plan: dict, *, base_url: str | None, headless: bool = True, executable_path: str | None = None) -> dict:
    from playwright.async_api import async_playwright

    started = time.monotonic()
    plan_base = plan.get("base_url")
    def described(step: dict) -> str:  # goto steps show the address actually opened
        return f"open {_rebase(step['url'], plan_base, base_url)}" if step["type"] == "goto" else describe(step)

    steps = [{"n": i + 1, "description": described(s), "status": "not_run", "detail": ""} for i, s in enumerate(plan["steps"])]
    checks = [{"n": i + 1, "kind": a["kind"], "why": a.get("why", ""), "status": "not_run", "detail": ""}
              for i, a in enumerate(plan["assertions"])]
    signals = Signals()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless, executable_path=executable_path or None)
        try:
            page = await (await browser.new_context()).new_page()
            signals.attach(page)

            stopped = False
            for step, result in zip(plan["steps"], steps):
                try:
                    skipped = await run_step(page, step, plan_base, base_url)
                    result["status"], result["detail"] = ("skipped", skipped) if skipped else ("ok", "")
                except Exception as err:
                    result["status"], result["detail"] = "failed", _first_line(err)
                    stopped = True
                    break

            if not stopped:
                await signals.settle(timeout_ms=5_000)
                for a, result in zip(plan["assertions"], checks):
                    ok, detail = await _check(page, a, signals.console_errors, signals.failed, signals.page_errors)
                    result["status"], result["detail"] = ("passed" if ok else "failed"), detail
        finally:
            await browser.close()

    reach = plan.get("reach")
    reached = None
    if reach and not any(s["status"] in ("failed", "skipped") for s in steps):
        reached = any(reach["url_contains"] in urlsplit(u).path for u in signals.requested)
    if any(s["status"] == "failed" for s in steps):
        outcome = "broken"
    elif any(s["status"] == "skipped" for s in steps):
        outcome = "incomplete"
    elif reached is False:
        outcome = "misses_bug"
    elif any(c["status"] == "failed" for c in checks):
        outcome = "reproduces"
    else:
        outcome = "passes"
    return {"outcome": outcome, "base_url": base_url or plan_base, "steps": steps, "assertions": checks,
            **({"reach": {**reach, "reached": reached}} if reach and reached is not None else {}),
            "duration_ms": int((time.monotonic() - started) * 1000)}
