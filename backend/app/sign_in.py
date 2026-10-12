"""Signing in to the app under test, so the agent tests what users see after they sign in.

A project keeps how to sign in: the sign-in page, the username, the password (encrypted
with the workspace keys' server key, never sent back and never shown to the AI), the
button, and the words that show it worked ("Sign out", "Welcome, Abebe"). Exploration runs,
flow tours, replays and the app survey start signed in with it.
"""
import logging
import re
from urllib.parse import urljoin

from app import models
from app.keys import decrypt, encrypt

log = logging.getLogger(__name__)

SIGN_OUT = re.compile(r"\b(log ?out|sign ?out|log ?off|sign ?off)\b", re.I)


FIELDS = ("url", "username_field", "username", "password_field", "submit", "signed_in_text", "second_username")


def settings_of(project: models.Project) -> dict | None:
    """The project's sign-in as the API shows it: never the password, only whether it's set."""
    cfg = project.sign_in or None
    if not cfg:
        return None
    return {k: cfg.get(k, "") for k in FIELDS} \
        | {"password_set": bool(cfg.get("password")), "second_password_set": bool(cfg.get("second_password"))}


def save(project: models.Project, payload: dict) -> None:
    """Keeps the stored password when none is given; an empty one clears it."""
    old = project.sign_in or {}
    cfg = {k: (payload.get(k) or "").strip() for k in FIELDS}
    for key in ("password", "second_password"):
        given = payload.get(key)
        cfg[key] = old.get(key, "") if given is None else (encrypt(given) if given else "")
    project.sign_in = cfg


def for_run(project: models.Project) -> dict | None:
    """What a run needs to sign in, with the password readable: kept in memory, never saved anywhere."""
    cfg = project.sign_in or None
    if not cfg or not cfg.get("url"):
        return None
    out = dict(cfg)
    for key in ("password", "second_password"):
        out[key] = decrypt(cfg[key]) if cfg.get(key) else ""
    return out


def second(cfg: dict | None) -> dict | None:
    """The same sign-in as the second user, when one is set: for checking who sees what."""
    if not cfg or not cfg.get("second_username"):
        return None
    return {**cfg, "username": cfg["second_username"], "password": cfg.get("second_password", "")}


def address(cfg: dict, base: str) -> str:
    return urljoin(base.rstrip("/") + "/", cfg["url"]) if not re.match(r"^https?://", cfg["url"]) else cfg["url"]


def secrets(cfg: dict | None) -> list[str]:
    """Values never to show anywhere (the AI's view, step details, notes)."""
    return [v for v in ((cfg or {}).get("password"), (cfg or {}).get("second_password")) if v]


async def sign_in(page, cfg: dict, base: str, signals=None) -> tuple[bool, str]:
    """Signs in on this page (and so its browser context). (worked?, what happened). The fields are
    found the way the agent finds them: by label, id, name, placeholder or aria-label."""
    from app.agent.explore import find_field, form_state, set_field
    url = address(cfg, base)
    await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    if signals:
        await signals.settle()
    form = await form_state(page)
    filled = []
    for label_key, value_key in (("username_field", "username"), ("password_field", "password")):
        name = cfg.get(label_key) or ("password" if label_key == "password_field" else "")
        if not name:
            continue
        field = find_field(form, name)
        if not field and label_key == "password_field":
            field = next((f for f in form.fields if f["type"] == "password"), None)
        if not field:
            return False, f'no field "{name}" on {url}. Fields there: ' + (", ".join(f'"{f["label"]}"' for f in form.fields[:10]) or "none")
        status, detail, _ = await set_field(page, field, "fill", cfg.get(value_key, ""))
        if status != "ok":
            return False, detail
        filled.append(field["label"])
    submit = cfg.get("submit") or form.submit
    if submit:
        await page.get_by_role("button", name=submit, exact=True).first.click(timeout=10_000)
    else:
        await page.keyboard.press("Enter")
    if signals:
        await signals.settle(timeout_ms=10_000)
    want = cfg.get("signed_in_text", "")
    if want:
        try:
            await page.get_by_text(want, exact=False).first.wait_for(state="visible", timeout=10_000)
        except Exception:
            after = await form_state(page)
            said = "; ".join(f'"{m[:120]}"' for m in after.messages[:3]) or "nothing"
            return False, f'"{want}" didn\'t appear after signing in (the page is {page.url}; it says {said})'
    return True, f"signed in as {cfg.get('username') or 'the saved user'}" + (f' ("{want}" is on the page)' if want else "")


def signed_out(url: str, cfg: dict, base: str) -> bool:
    """Is the browser back on the sign-in page (the session ended)?"""
    def path(u: str) -> str:
        return re.sub(r"[?#].*$", "", u).rstrip("/")
    return path(url) == path(address(cfg, base))


async def try_it(project_id: str, executable_path: str | None) -> dict:
    """Signs in once in a fresh headless browser and returns what happened, with a screenshot."""
    import base64
    from playwright.async_api import async_playwright
    from app.database import SessionLocal
    with SessionLocal() as db:
        project = db.get(models.Project, project_id)
        cfg, base = for_run(project), (project.app_url or "")
    if not cfg:
        return {"ok": False, "detail": "No sign-in is set up for this project.", "url": "", "screenshot": None}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, executable_path=executable_path or None)
        try:
            page = await (await browser.new_context(viewport={"width": 1280, "height": 800})).new_page()
            try:
                ok, detail = await sign_in(page, cfg, base)
            except Exception as err:
                ok, detail = False, str(err).strip().splitlines()[0][:300] if str(err).strip() else type(err).__name__
            shot = await page.screenshot(type="jpeg", quality=60)
            for s in secrets(cfg):
                detail = detail.replace(s, "•••")
            return {"ok": ok, "detail": detail, "url": page.url, "screenshot": "data:image/jpeg;base64," + base64.b64encode(shot).decode()}
        finally:
            await browser.close()
