"""Who sees what: after a signed-in run, the pages the first user opened that look like their own
things (an order, a document, an account page) are opened again by someone else: signed out,
and as the project's second user. Seeing the first user's things there is a bug a tester checks
for and rarely has time to: the app trusts the address instead of checking who asks.

No AI: what counts as "the first user's" is what their page shows that the second user's own
start page doesn't (a heading, an order line, their email), and it must be there for the other
viewer too, without a "not found" or "not allowed" in sight."""
import re
from urllib.parse import parse_qs, urlsplit

from app import schemas

MAX_PAGES = 6
OWN_WORDS = ("account", "profile", "my", "order", "orders", "document", "documents", "invoice", "invoices", "settings",
             "dashboard", "message", "messages", "booking", "bookings", "payment", "payments", "user", "users")
DENIED = re.compile(r"\b(not found|forbidden|not allowed|access denied|no access|permission|unauthori[sz]ed|"
                    r"sign in|log in|login|404|403|doesn't exist|does not exist)\b", re.I)
TEXT_JS = "() => document.body ? document.body.innerText : ''"


def personal(url: str, origin_: str, sign_in_url: str) -> bool:
    """Is this address likely one user's own thing: an id in the path or query, or an account-like page?"""
    parts = urlsplit(url)
    if f"{parts.scheme}://{parts.netloc}" != origin_ or url.split("#")[0].split("?")[0].rstrip("/") == sign_in_url.rstrip("/"):
        return False
    path = parts.path.lower()
    if re.search(r"log ?out|sign ?out", path):
        return False
    segments = [s for s in path.split("/") if s]
    has_id = any(re.fullmatch(r"\d+|[0-9a-f-]{8,}", s) for s in segments) or \
        any(k.lower() in ("id", "order", "user", "doc", "document") for k in parse_qs(parts.query))
    return has_id or any(s.rstrip("s") in {w.rstrip("s") for w in OWN_WORDS} for s in segments)


def lines_of(text: str) -> list[str]:
    return [l for l in (re.sub(r"\s+", " ", x).strip() for x in text.splitlines()) if len(l) > 2]


async def view(browser, url: str, cfg: dict | None, base: str, viewport: dict) -> tuple[str, list[str], object]:
    """(final address, lines on the page, the page) as this viewer sees it: signed in with cfg, or signed out."""
    from app.sign_in import sign_in
    context = await browser.new_context(viewport=viewport)
    page = await context.new_page()
    if cfg:
        ok, how = await sign_in(page, cfg, base)
        if not ok:
            raise RuntimeError(f"couldn't sign in as {cfg.get('username')}: {how}")
    await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    try:
        await page.wait_for_load_state("networkidle", timeout=4_000)
    except Exception:
        pass
    return page.url, lines_of(await page.evaluate(TEXT_JS)), page


async def access_check(pw, urls: list[str], cfg: dict, second: dict | None, base: str, executable_path: str | None,
                       headless: bool, draft) -> str:
    """Opens each of the first user's own pages as the first user, signed out, and as the second user.
    draft(problem, page, steps) keeps a checked draft. Returns what it did."""
    from app.regression.plan import origin
    from app.sign_in import address, signed_out
    home = origin(base)
    login = address(cfg, base)
    pages = []
    for u in urls:
        key = u.split("#")[0]
        if personal(key, home, login) and key not in pages:
            pages.append(key)
    pages = pages[:MAX_PAGES]
    if not pages:
        return ""
    viewport = {"width": 1280, "height": 800}
    browser = await pw.chromium.launch(headless=headless, executable_path=executable_path or None)
    done, found = [], 0
    try:
        # What's on every page for anyone: the second user's (or a visitor's) own start page.
        _, common, start = await view(browser, base.rstrip("/") + "/", second, base, viewport)
        await start.context.close()
        common_set = set(common)
        for url in pages:
            path = urlsplit(url).path or "/"
            _, own_lines, own = await view(browser, url, cfg, base, viewport)
            await own.context.close()
            mine = [l for l in own_lines if l not in common_set and not DENIED.search(l)]
            if not mine:
                continue
            viewers = [("signed out", None)] + ([(f"signed in as {second['username']}", second)] if second else [])
            for who, viewer in viewers:
                at, lines, pg = await view(browser, url, viewer, base, viewport)
                try:
                    if signed_out(at, cfg, base):
                        done.append(f"{path} {who}: sent to sign in")
                        continue
                    if (urlsplit(at).path or "/").rstrip("/") != path.rstrip("/"):
                        done.append(f"{path} {who}: sent to {urlsplit(at).path or '/'}")
                        continue
                    seen = [l for l in mine if l in set(lines)]
                    denied = any(DENIED.search(l) for l in lines if l not in common_set)
                    if not denied and len(seen) >= max(1, (len(mine) + 1) // 2):
                        title = (f"Signed out, {path} still shows the user's own page" if viewer is None
                                 else f"Another user can open {path}: {second['username']} sees {cfg['username']}'s page")
                        problem = schemas.ExploreProblem(
                            title=title[:200], severity="high",
                            expected=f"{path} is {cfg['username']}'s own: anyone else is refused or sent to sign in.",
                            actual=(f"{who.capitalize()}, opening {url} shows what {cfg['username']} sees: "
                                    + "; ".join(f'"{l[:80]}"' for l in seen[:3]))[:600],
                            symptom=schemas.Symptom(kind="text_visible", value=seen[0][:300],
                                                    explanation=f"{cfg['username']}'s page, seen {who}"),
                            same_as=f"access:{path}:{'out' if viewer is None else 'other'}")
                        problem.fault = (f"open it {who}" if viewer is None
                                         else f"sign in as {second['username']} (not {cfg['username']}), then open it")
                        if await draft(problem, pg, [{"type": "goto", "url": url}]):
                            found += 1
                        done.append(f"{path} {who}: shows {len(seen)} of {len(mine)} of the user's own lines")
                    else:
                        done.append(f"{path} {who}: " + ("refused" if denied else "a different page"))
                finally:
                    await pg.context.close()
    finally:
        await browser.close()
    return ("Opened the user's own pages as someone else: " + "; ".join(done)
            + (f". {found} problem{'s' if found != 1 else ''} drafted." if found else ".")) if done else ""
