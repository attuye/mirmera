"""What the app keeps in the browser: local storage, session storage and cookies, after every agent step.

From the Bachs' Mecha-Testing: watch local storage along with the network. In KYC the browser is a place
where a customer's details and their session can linger: on a shared phone at an agent's shop, after they
sign out, for the next person. Plain code reads it after each step and keeps:

- the step's **changes** (``+ local: kyc_draft``), with any value that looks like a token or a personal
  detail masked, so the evidence never holds what it warns about;
- **curios** for the tester to judge: personal details kept in storage, a token readable by any script,
  a session cookie without HttpOnly or Secure, and above all what is still there after signing out.
"""
import json
import re

SNAPSHOT_JS = """() => {
  const read = (s) => { try { return Object.fromEntries(Object.keys(s).map((k) => [k, s.getItem(k)])) } catch (e) { return {} } }
  return { local: read(window.localStorage), session: read(window.sessionStorage) }
}"""

JWT = re.compile(r"\beyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,}")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE = re.compile(r"(\+?251|\b0)\s?[79]\d{2}\s?\d{3}\s?\d{3}\b")
TOKEN_KEYS = re.compile(r"token|jwt|auth|session|sess|sid|bearer|refresh|access", re.I)
PERSONAL_KEYS = re.compile(r"full_?name|first_?name|last_?name|father|surname|birth|dob|passport|national_?id|fayda|"
                           r"document_?(number|no)|id_?number|phone|mobile|email|address|kebele|woreda|selfie|photo|"
                           r"\bssn\b|tin\b|nationality|gender", re.I)
SECRET_KEYS = re.compile(r"password|passwd|\bpin\b|otp|secret|cvv", re.I)
OUT_WORDS = re.compile(r"log\s?out|sign\s?out|signout|logout|ውጣ", re.I)


def looks_like(key: str, value: str) -> list[str]:
    """What a stored value seems to hold: a token, a password, personal details (by their names)."""
    found = []
    text = value or ""
    if SECRET_KEYS.search(key) or re.search(r'"(password|pin|otp)"\s*:', text, re.I):
        found.append("a password or PIN")
    if JWT.search(text):
        found.append("a token (JWT)")
    elif TOKEN_KEYS.search(key) and len(text) >= 16:
        found.append("a token")
    names = set()
    if PERSONAL_KEYS.search(key):
        names.add(key)
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        data = None
    for k in _keys(data):
        if PERSONAL_KEYS.search(k):
            names.add(k)
        if SECRET_KEYS.search(k) and "a password or PIN" not in found:
            found.append("a password or PIN")
    if names:
        found.append("personal details (" + ", ".join(sorted(names)[:5]) + ")")
    elif EMAIL.search(text) or PHONE.search(text):
        found.append("personal details (an email or phone number)")
    return found


def _keys(data, depth=0):
    if depth > 4:
        return
    if isinstance(data, dict):
        for k, v in data.items():
            yield k
            yield from _keys(v, depth + 1)
    elif isinstance(data, list):
        for v in data[:20]:
            yield from _keys(v, depth + 1)


def shown(key: str, value: str) -> str:
    """The value as the evidence keeps it: masked when it looks like something to protect."""
    value = value or ""
    if looks_like(key, value):
        return f"••• ({len(value)} characters)"
    return value if len(value) <= 40 else value[:37] + "…"


async def snapshot(page) -> dict:
    """Local storage, session storage and the page's cookies, right now."""
    try:
        stores = await page.evaluate(SNAPSHOT_JS)
    except Exception:  # a page mid-navigation, or one that forbids storage
        stores = {"local": {}, "session": {}}
    try:
        cookies = await page.context.cookies([page.url]) if page.url.startswith("http") else []
    except Exception:
        cookies = []
    return {"local": stores.get("local") or {}, "session": stores.get("session") or {},
            "cookies": {c["name"]: c for c in cookies}}


def _items(snap: dict):
    for where in ("local", "session"):
        for k, v in snap.get(where, {}).items():
            yield where, k, v if isinstance(v, str) else json.dumps(v)
    for name, c in snap.get("cookies", {}).items():
        yield "cookie", name, c.get("value", "")


def changes(before: dict, after: dict) -> list[str]:
    """What this step added, changed or removed, values masked: "+ local: kyc_draft = ••• (212 characters)"."""
    old = {(w, k): v for w, k, v in _items(before)}
    new = {(w, k): v for w, k, v in _items(after)}
    out = []
    for (w, k), v in new.items():
        if (w, k) not in old:
            out.append(f"+ {w}: {k} = {shown(k, v)}")
        elif old[(w, k)] != v:
            out.append(f"~ {w}: {k} = {shown(k, v)}")
    out += [f"- {w}: {k}" for (w, k) in old if (w, k) not in new]
    return out[:12]


def curios(snap: dict, secure: bool) -> list[dict]:
    """{what, key} for what's worth a look in storage as it is now."""
    out = []
    for where, k, v in _items(snap):
        if where == "cookie":
            continue
        place = "local storage (kept after the browser closes)" if where == "local" else "session storage"
        for what in looks_like(k, v):
            out.append({"what": f"{place} holds {what} under \"{k}\"", "key": f"{where}:{k}:{what.split(' (')[0]}"})
    for name, c in snap.get("cookies", {}).items():
        if not TOKEN_KEYS.search(name):
            continue
        missing = [f for f, ok in (("HttpOnly", c.get("httpOnly")), ("Secure", c.get("secure") or not secure)) if not ok]
        if missing:
            out.append({"what": f"the session cookie \"{name}\" has no {' or '.join(missing)}"
                                + (" (any script on the page can read it)" if "HttpOnly" in missing else ""),
                        "key": f"cookie:{name}:flags"})
    return out


def after_sign_out(before: dict, after: dict) -> list[dict]:
    """Curios when signing out left the customer's session or details in the browser."""
    out = []
    for where, k, v in _items(after):
        kept = looks_like(k, v) if where != "cookie" else (["a session cookie"] if TOKEN_KEYS.search(k) else [])
        if not kept:
            continue
        was = {(w, key) for w, key, _ in _items(before)}
        if (where, k) in was:
            what = (f"after signing out, the session cookie \"{k}\" is still set" if where == "cookie" else
                    f"after signing out, {where} storage still holds {', '.join(kept)} under \"{k}\"")
            out.append({"what": what, "key": f"signout:{where}:{k}"})
    return out


def is_sign_out(action_text: str, url: str) -> bool:
    return bool(OUT_WORDS.search(action_text or "") or OUT_WORDS.search(url or ""))
