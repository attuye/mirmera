"""Browser session runner: a Chromium window for the app under test, recorded
while the tester explores.

The tester starts it from the session screen and tests in the window as usual.
The runner records, without the tester doing anything:
  - signals: console errors, uncaught page errors, failed requests (4xx/5xx or
    no response) and the trail of pages visited;
  - a Playwright trace (DOM snapshots and screenshots of every action), and a
    HAR file of the network traffic, both written when the browser stops;
  - screenshots on demand, and automatically when a bug is logged.

Nothing here judges whether something is a problem. Signals are evidence for
the tester, who decides what matters (a 404 can be the correct answer).

Design notes:
  - Playwright objects belong to the event loop that created them, while web
    requests run on other threads and loops. So every runner lives on one
    shared background loop (`_BackgroundLoop`) and callers hand it coroutines.
  - Event callbacks only append to an in-memory buffer; routers drain it into
    the database with their own DB session. The runner never touches the DB.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Coroutine
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.timeutil import utcnow

logger = logging.getLogger(__name__)

MAX_BUFFERED_SIGNALS = 500  # a page logging errors in a loop must not eat memory
TEXT_LIMIT = 500  # long console messages and URLs are cut to this length
IGNORED_URL_SUFFIXES = ("/favicon.ico",)  # browser noise, not the app's behaviour


class RunnerError(Exception):
    """Base class; the message is shown to the tester."""


class RunnerUnavailable(RunnerError):
    """Playwright or its browser is missing, or the browser would not start (503)."""


class RunnerConflict(RunnerError):
    """Already running, or not running when it should be (409)."""


@dataclass
class Signal:
    kind: str  # "console_error" | "page_error" | "failed_request" | "navigation"
    summary: str
    url: str | None
    at: datetime


def _cut(text: str | None) -> str:
    text = (text or "").strip()
    return text if len(text) <= TEXT_LIMIT else text[: TEXT_LIMIT - 1] + "…"


class _BackgroundLoop:
    """One event loop on a daemon thread, started on first use."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def _get(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is None:
                loop = asyncio.new_event_loop()
                threading.Thread(target=loop.run_forever, name="mirmera-runner", daemon=True).start()
                self._loop = loop
            return self._loop

    def run(self, coro: Coroutine[Any, Any, Any], timeout: float = 60) -> Any:
        """Run on the background loop and wait (for sync code and threadpool endpoints)."""
        return asyncio.run_coroutine_threadsafe(coro, self._get()).result(timeout)

    async def arun(self, coro: Coroutine[Any, Any, Any]) -> Any:
        """Run on the background loop and await (for async endpoints)."""
        return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(coro, self._get()))


class BrowserRunner:
    """One recorded browser for one session. Methods run on the background loop."""

    def __init__(self, session_id: str, folder: Path) -> None:
        self.session_id = session_id
        self.folder = folder
        self.started_at = utcnow()
        self.start_url: str | None = None
        self.trace_path = folder / "trace.zip"
        self.har_path = folder / "network.har"
        self._signals: list[Signal] = []
        self._dropped = 0
        self._pages: list[Any] = []
        self._last_navigation: str | None = None
        self._pw = self._browser = self._context = None

    # ---------- lifecycle ----------

    async def start(self, url: str, *, headless: bool, executable_path: str | None) -> None:
        try:
            from playwright.async_api import async_playwright
        except ImportError as err:
            raise RunnerUnavailable(
                "Playwright is not installed. Run: pip install -r requirements.txt, "
                "then: python -m playwright install chromium"
            ) from err

        self.folder.mkdir(parents=True, exist_ok=True)
        self.start_url = url
        self._pw = await async_playwright().start()
        try:
            self._browser = await self._pw.chromium.launch(headless=headless, executable_path=executable_path or None)
        except Exception as err:  # missing browser binary, no display, ...
            await self._pw.stop()
            raise RunnerUnavailable(
                f"The browser would not start: {str(err).splitlines()[0]}. "
                "If Chromium is missing, run: python -m playwright install chromium"
            ) from err

        self._context = await self._browser.new_context(
            record_har_path=str(self.har_path),
            record_har_content="omit",  # requests, statuses and timings; not the bodies
            no_viewport=not headless,  # a real window sizes itself; headless needs a viewport
        )
        await self._context.tracing.start(screenshots=True, snapshots=True)
        self._context.on("page", self._watch)
        self._context.on("response", self._on_response)
        self._context.on("requestfailed", self._on_request_failed)

        page = await self._context.new_page()
        self._watch(page)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        except Exception as err:  # unreachable app: keep the window, tell the tester
            self._add("failed_request", f"Could not open {url}: {str(err).splitlines()[0]}", url)

    async def stop(self) -> dict[str, Path]:
        """Write the trace and HAR and close everything. Returns the files written."""
        files: dict[str, Path] = {}
        if self._context is not None:
            try:
                await self._context.tracing.stop(path=str(self.trace_path))
            except Exception as err:
                logger.warning("Session %s: trace not saved: %s", self.session_id, err)
            try:
                await self._context.close()  # the HAR is written on close
            except Exception as err:
                logger.warning("Session %s: browser context did not close cleanly: %s", self.session_id, err)
        for closer in (self._browser, self._pw):
            if closer is not None:
                try:
                    await (closer.close() if closer is self._browser else closer.stop())
                except Exception:
                    pass
        self._context = self._browser = self._pw = None
        if self.trace_path.exists():
            files["trace"] = self.trace_path
        if self.har_path.exists():
            files["har"] = self.har_path
        return files

    # ---------- what the tester asks for ----------

    async def screenshot(self, path: Path) -> tuple[str, str]:
        """Screenshot of the most recently opened page still open. Returns (url, title)."""
        page = self._current_page()
        if page is None:
            raise RunnerConflict("The browser has no open page to screenshot. Open a page or restart the browser.")
        path.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(path))
        return page.url, await page.title()

    async def status(self) -> dict:
        page = self._current_page()
        return {
            "url": page.url if page else None,
            "title": (await page.title()) if page else None,
            "window_open": page is not None,
        }

    def drain(self) -> list[Signal]:
        """Signals captured since the last drain (list swaps are atomic, so this
        is safe to call from another thread)."""
        signals, self._signals = self._signals, []
        return signals

    @property
    def dropped(self) -> int:
        """Signals not recorded because the buffer was full between drains."""
        return self._dropped

    # ---------- event handlers (called by Playwright on the background loop) ----------

    def _current_page(self):
        self._pages = [p for p in self._pages if not p.is_closed()]
        return self._pages[-1] if self._pages else None

    def _add(self, kind: str, summary: str, url: str | None) -> None:
        if len(self._signals) >= MAX_BUFFERED_SIGNALS:
            self._dropped += 1
            return
        self._signals.append(Signal(kind, _cut(summary), _cut(url) or None, utcnow()))

    def _watch(self, page) -> None:
        if page in self._pages:
            return  # the "page" event and start() both report the first page
        self._pages.append(page)
        page.on("console", lambda msg: self._on_console(msg, page))
        page.on("pageerror", lambda err: self._add("page_error", str(err), page.url))
        page.on("framenavigated", lambda frame: frame == page.main_frame and self._on_navigation(frame.url))

    def _on_console(self, msg, page) -> None:
        # Chrome also logs every failed request as "Failed to load resource";
        # those are already recorded, with method and URL, as failed requests.
        if msg.type == "error" and not msg.text.startswith("Failed to load resource"):
            self._add("console_error", msg.text, page.url)

    def _on_navigation(self, url: str) -> None:
        if url and url != self._last_navigation and not url.startswith("about:"):
            self._last_navigation = url
            self._add("navigation", url, url)

    def _on_response(self, response) -> None:
        if response.status >= 400 and not response.url.endswith(IGNORED_URL_SUFFIXES):
            self._add("failed_request", f"{response.request.method} {response.url} → {response.status}", response.url)

    def _on_request_failed(self, request) -> None:
        failure = request.failure or "no response"
        if "ERR_ABORTED" in failure or request.url.endswith(IGNORED_URL_SUFFIXES):
            return  # cancelled by navigation or the page itself; not a failure of the app
        self._add("failed_request", f"{request.method} {request.url} → {failure}", request.url)


class RunnerManager:
    """Keeps at most one runner per session."""

    def __init__(self) -> None:
        self.loop = _BackgroundLoop()
        self._runners: dict[str, BrowserRunner] = {}
        self._lock = threading.Lock()

    def get(self, session_id: str) -> BrowserRunner | None:
        return self._runners.get(session_id)

    def start(self, session_id: str, url: str, folder: Path, *, headless: bool, executable_path: str | None) -> BrowserRunner:
        with self._lock:
            if session_id in self._runners:
                raise RunnerConflict("The browser is already running for this session.")
            runner = BrowserRunner(session_id, folder)
            self._runners[session_id] = runner
        try:
            self.loop.run(runner.start(url, headless=headless, executable_path=executable_path), timeout=90)
        except BaseException:
            self._runners.pop(session_id, None)
            raise
        return runner

    def pop(self, session_id: str) -> BrowserRunner | None:
        with self._lock:
            return self._runners.pop(session_id, None)

    def stop_all(self) -> None:
        """On shutdown: close every browser so no Chromium is left behind."""
        for session_id in list(self._runners):
            runner = self.pop(session_id)
            if runner:
                try:
                    self.loop.run(runner.stop(), timeout=30)
                except Exception as err:
                    logger.warning("Session %s: browser did not stop cleanly: %s", session_id, err)


manager = RunnerManager()
