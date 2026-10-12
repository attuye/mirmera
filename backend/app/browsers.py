"""Which browser engine Mirmera drives: Chromium (Chrome, Edge), Firefox, or WebKit (Safari's engine).

Users don't all use Chrome. An exploration run, its drafts' replays, a regression test and a
comparison can run in any of the three. Chromium can be a system browser (RUNNER_CHROMIUM_PATH);
Firefox and WebKit come from Playwright: `python -m playwright install firefox webkit`.
"""
from pathlib import Path

BROWSERS = ("chromium", "firefox", "webkit")
LABEL = {"chromium": "Chromium", "firefox": "Firefox", "webkit": "WebKit (Safari's engine)"}


class BrowserMissing(RuntimeError):
    """The engine isn't installed on this machine: says how to install it."""


def label(name: str | None) -> str:
    return LABEL.get(name or "chromium", name or "Chromium")


def install_hint(name: str) -> str:
    return (f"{label(name)} isn't installed for Playwright on this machine. In the backend's folder, run: "
            f"python -m playwright install {name}")


def installed(name: str, executable_path: str | None = None) -> bool:
    """Is this engine there to launch? (Chromium with a system executable always counts.)"""
    if name == "chromium" and executable_path:
        return Path(executable_path).exists()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    from concurrent.futures import ThreadPoolExecutor

    def look() -> bool:  # sync Playwright can't start inside a running event loop: a thread of its own
        with sync_playwright() as p:
            return Path(getattr(p, name).executable_path).exists()
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(look).result(timeout=60)


async def launch(pw, name: str | None, *, headless: bool = True, executable_path: str | None = None):
    """The browser, launched; BrowserMissing (with the command to install it) when it isn't there."""
    name = name or "chromium"
    if name not in BROWSERS:
        raise ValueError(f"Unknown browser {name}: use one of {', '.join(BROWSERS)}")
    kind = getattr(pw, name)
    try:
        if name == "chromium":
            return await kind.launch(headless=headless, executable_path=executable_path or None)
        return await kind.launch(headless=headless)
    except Exception as err:
        if "Executable doesn't exist" in str(err) or "playwright install" in str(err):
            raise BrowserMissing(install_hint(name)) from err
        raise


def context_options(name: str | None, options: dict) -> dict:
    """What this engine can emulate: Firefox has no mobile mode (a phone is its size, touch and agent)."""
    if (name or "chromium") == "firefox":
        return {k: v for k, v in options.items() if k != "is_mobile"}
    return options
