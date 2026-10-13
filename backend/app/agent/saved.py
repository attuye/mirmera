"""What was typed comes back as typed: the saved-text check.

An app can accept "አበበ ከበደ" and show "???? ????", "&#4768;&#4704;…", "áŠ á‰ á‰ " (UTF-8 read as
Latin-1), a name cut to 20 characters, or "O\\'Brien". Every one is a real, common bug, and the
oracle needs no AI: the value the app accepted, against what its pages show afterwards.

After every accepted submit Mirmera looks for a mangled copy of what was sent on the answer page.
Its flow tour also sends the form once with Ge'ez text and once with an apostrophe, a hyphen and an
ampersand, and then opens the page where entries are listed to check they come back exactly.
"""
import html
import re

ETHIOPIC = re.compile(r"[ሀ-፿]")
FREE_TEXT = re.compile(r"name|title|description|address|note|comment|message|city|street|company|subject|about", re.I)
# Values for free-text fields: what a user here types, that apps mangle.
TRICKY = (
    ("Ge'ez letters", "አበበ ከበደ ተሰማ"),
    ("an apostrophe, a hyphen and an ampersand", "Mary O'Brien-Tesfaye & Co"),
)


def free_text(fields: list[dict]) -> list[dict]:
    """The fields a user writes words into (not emails, phones, numbers, dates or choices)."""
    return [f for f in fields if f["type"] in ("text", "textarea", "search") and FREE_TEXT.search(f["label"])]


def _entities(v: str) -> list[str]:
    return [html.escape(v, quote=True), "".join(f"&#{ord(c)};" if ord(c) > 127 or c in "'&" else c for c in v),
            v.encode("ascii", "xmlcharrefreplace").decode()]


def _plain(text: str) -> str:
    """Text as a page shows it: no invisible control characters, no-break spaces as spaces."""
    return re.sub(r"[ \t\u00a0]+", " ", re.sub(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u00ad\ufffd]", "", text))


INVISIBLE = "[\\x00-\\x08\\x0b-\\x1f\\x7f-\\x9f\\u00ad\\ufffd]*"


def _raw_prefix(plain_bad: str, text: str) -> str | None:
    """The longest start (8+ characters) of the mangled value as it really is in the page text,
    invisible characters and odd spaces included, so the page can be checked for it again."""
    for k in range(len(plain_bad), 7, -1):
        parts = []
        for c in plain_bad[:k]:
            parts.append("[ \\t\\u00a0]+" if c == " " else re.escape(c))
        m = re.search(INVISIBLE.join(parts), text)
        if m:
            return m.group(0).strip()
    return None


def garbled(sent: dict[str, str], lines: list[str]) -> list[dict]:
    """Copies of the sent values shown mangled. sent: field label -> the value the app accepted.
    [{"field", "value", "shown", "why"}]; a value shown exactly anywhere is fine."""
    text = "\n".join(lines)
    out = []
    for label, v in sent.items():
        v = (v or "").strip()
        if len(v) < 4 or v in text:
            continue
        found = None
        for enc in _entities(v):
            if enc != v and enc in text:
                found = (enc, "shown as HTML codes instead of the characters")
                break
        if not found and ETHIOPIC.search(v):
            for bad, why in ((v.encode("utf-8").decode("cp1252", errors="replace"), "garbled: UTF-8 read as Windows-1252"),
                             (v.encode("utf-8").decode("latin-1", errors="replace"), "garbled: UTF-8 read as Latin-1"),
                             (ETHIOPIC.sub("?", v), "its Ge'ez letters became question marks")):
                if bad == v:
                    continue
                shown = _raw_prefix(_plain(bad), text)
                if shown:
                    found = (shown, why)
                    break
        if not found and "'" in v:
            for bad in (v.replace("'", "\\'"), v.replace("'", "''"), v.replace("'", "&#39;"), v.replace("'", "")):
                if bad in text:
                    found = (bad, "its apostrophe was changed")
                    break
        if not found and len(v) >= 10:
            # Cut short in the middle of a word (showing only the first name is a choice; "Tesf" is a cut).
            for n in range(len(v) - 1, 5, -1):
                if not (v[n - 1].isalpha() and v[n].isalpha()):
                    continue
                m = re.search(re.escape(v[:n]) + r"(?=$|[\s,.;:)\]\"'…]|\n)", text)
                if m:
                    found = (v[:n], f"cut to {n} of its {len(v)} characters")
                    break
        if found:
            shown, why = found
            out.append({"field": label, "value": v, "shown": shown[:200], "why": why})
    return out


def problem(g: dict, where: str):
    """The bug draft for one mangled value."""
    from app import schemas
    return schemas.ExploreProblem(
        title=f'{g["field"]} "{g["value"][:40]}" comes back as "{g["shown"][:40]}" ({g["why"].split(":")[0]})'[:200],
        severity="high" if "Ge'ez" in g["why"] or "Latin" in g["why"] or "Windows" in g["why"] or "HTML" in g["why"] else "medium",
        expected=f'What the app accepted is shown as it was typed: "{g["value"][:150]}".',
        actual=f'{where} shows "{g["shown"][:150]}": {g["why"]}.'[:600],
        symptom=schemas.Symptom(kind="text_visible", value=g["shown"][:300], explanation=f"Saved text {g['why']}"[:600]))


async def saved_text_tour(page, signals, form_url: str, sent, to_here: list[dict], rules: dict[str, str], keep, *,
                          fill) -> tuple[str, int]:
    """The form sent with tricky text in its free-text fields, then the answer and the listing page
    checked for each value as typed. fill(page, overrides) fills the form as sent, with overrides
    (label -> value), and returns the recorded steps. Returns (what it did, drafts)."""
    from urllib.parse import urlsplit
    from app.agent.explore import form_state, listing_place, outcome_of
    fields = free_text(sent.fields)
    if not fields or not sent.submit:
        return "", 0
    found, said = 0, []
    path = urlsplit(form_url).path or "/"
    for what, value in TRICKY:
        await page.goto(form_url, wait_until="domcontentloaded", timeout=30_000)
        await signals.settle()
        overrides = {f["label"]: value for f in fields}
        rec = to_here[:1] + [{"type": "goto", "url": form_url}] + await fill(page, overrides)
        before = await form_state(page)
        await page.get_by_role("button", name=sent.submit, exact=True).first.click(timeout=5_000)
        rec.append({"type": "action", "url": form_url,
                    "action": {"action": "click", "locator": {"method": "role", "role": "button", "name": sent.submit}}})
        await signals.settle(timeout_ms=10_000)
        after = await form_state(page)
        new = [m for m in after.messages if m not in before.messages] or [l for l in after.lines if l not in before.lines]
        moved = after.url.split("#")[0] != before.url.split("#")[0]
        if not (moved or outcome_of(new, []) == "accepted"):
            said.append(f"{what}: not accepted")  # refused (a rule may say so): the rules check judges that
            continue
        bad = 0
        # 1. The answer: checked while it is on screen (a draft's symptom is checked on the page as it is).
        for g in garbled(overrides, after.lines):
            bad += 1
            if await keep(problem(g, f"The answer to {path}"), ("saved", g["field"], g["why"]), (page, signals, list(rec))):
                found += 1
        # 2. Where entries are listed ("My orders"): the same values, as typed.
        place, _ = listing_place(before.links or after.links, rules)
        if place:
            await page.goto(place["href"], wait_until="domcontentloaded", timeout=30_000)
            await signals.settle(timeout_ms=5_000)
            listed = await form_state(page)
            for g in garbled(overrides, listed.lines):
                bad += 1
                steps = rec + [{"type": "goto", "url": page.url}]
                if await keep(problem(g, place["text"]), ("saved", g["field"], g["why"]), (page, signals, steps)):
                    found += 1
        said.append(f"{what}: " + ("mangled" if bad else "came back as typed" + (f" on {place['text']}" if place else "")))
    return "the saved text (" + "; ".join(said) + ")", found
