"""A survey of the app before exploring it: what an experienced tester does on day one.
A headless browser opens the app and follows its links (same site only), reading each
page's title, heading, form fields (with their choices) and buttons. It never submits a
form or clicks a button, so nothing in the app changes. The result is the app's map: the
agent gets it as context, and testers see it on the Coverage page."""
import asyncio
from urllib.parse import urljoin, urlsplit

from fastapi import HTTPException

from app import models, schemas
from app.agent.explore import FORM_JS
from app.config import settings
from app.regression.plan import origin

SKIP = ("logout", "log-out", "signout", "sign-out", "delete", "remove", "unsubscribe")
PAGE_JS = """() => ({
  links: [...document.querySelectorAll('a[href]')].map((a) => a.href),
  buttons: [...document.querySelectorAll('button, input[type=submit]')].filter((b) => b.offsetWidth || b.offsetHeight)
    .map((b) => (b.innerText || b.value || '').trim()).filter(Boolean).slice(0, 8),
})"""


def summary(page: dict) -> str:
    fields = ", ".join(f["label"] + (f" ({'/'.join(f['options'][:4])})" if f.get("options") else "") for f in page["fields"][:8])
    return (f"{page['path']} ({page['heading'] or page['title'] or 'untitled'})"
            + (f": form {fields}" if fields else "") + (f"; buttons {', '.join(page['buttons'][:4])}" if page["buttons"] else ""))


async def crawl(base: str, max_pages: int = 25, executable_path: str | None = None) -> list[dict]:
    from playwright.async_api import async_playwright
    home = origin(base)
    queue, seen, pages = [base.rstrip("/") + "/"], set(), []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, executable_path=executable_path or None)
        try:
            page = await (await browser.new_context(viewport={"width": 1280, "height": 800})).new_page()
            page.on("dialog", lambda d: asyncio.ensure_future(d.dismiss()))
            while queue and len(pages) < max_pages:
                url = queue.pop(0)
                key = url.split("#")[0].split("?")[0]
                if key in seen:
                    continue
                seen.add(key)
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=15_000)
                    await page.wait_for_timeout(400)  # let scripts fill the page
                    form = await page.evaluate(FORM_JS)
                    extra = await page.evaluate(PAGE_JS)
                except Exception as err:
                    pages.append({"path": urlsplit(url).path or "/", "url": url, "title": "", "heading": "", "fields": [],
                                  "buttons": [], "error": str(err).splitlines()[0][:200]})
                    continue
                if origin(page.url) != home:
                    continue  # it redirected away
                p = {"path": urlsplit(page.url).path or "/", "url": page.url, "title": (form.get("title") or "")[:200],
                     "heading": (form.get("heading") or "")[:200],
                     "fields": [{k: f.get(k) for k in ("label", "type", "required", "options") if f.get(k) is not None}
                                for f in form.get("fields") or []],
                     "buttons": extra.get("buttons") or []}
                p["summary"] = summary(p)
                pages.append(p)
                for link in extra.get("links") or []:
                    link = urljoin(page.url, link).split("#")[0]
                    if origin(link) == home and link.split("?")[0] not in seen and not any(w in link.lower() for w in SKIP):
                        queue.append(link)
        finally:
            await browser.close()
    return pages


async def survey(project_id: str, payload: schemas.AppMapRequest, db) -> dict:
    """An AI job kind ("app_map"), though no AI is involved: it runs in the background and
    keeps its result like the others."""
    project = db.get(models.Project, project_id)
    base = (payload.base_url or project.app_url or "").strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        raise HTTPException(409, f"{project.name} has no app URL to survey. Set it in the project's Settings.")
    db.commit()  # nothing is held open while the browser works
    # On the runner's own event loop, as the agents run: it can start browsers on every platform
    # (on Windows the web server's loop may not be able to start subprocesses).
    from app.runner import manager
    pages = await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(
        crawl(base, payload.max_pages, settings.runner_chromium_path), manager.loop._get()))
    return {"base_url": base, "pages": pages, "forms": sum(1 for p in pages if p["fields"])}
