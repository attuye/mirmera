"""The network log of an agent run, as a HAR file a developer can open (Chrome DevTools → Network →
import, Firefox, Charles, Fiddler, har viewers), without handing out anyone's access.

Playwright writes everything the browser sent and received. Before it's kept, this takes out what
would let a reader act as the user: cookies and authorization headers (in requests and responses),
and passwords, tokens, PINs and OTPs in addresses, form and JSON bodies, and JSON answers; and any
known secret (the sign-in's password, test data marked secret) anywhere in the text. Pictures and
other files the app sent back are left out (only their size and type stay): in KYC they are often
someone's ID or face, and a log is no place to keep them.
"""
import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.curl import MASK, SECRET_HEADERS, SECRET_KEYS, _mask_body

SECRET_RESPONSE_HEADERS = SECRET_HEADERS | {"set-cookie", "www-authenticate"}
TEXT_TYPES = re.compile(r"^(text/|application/(json|[\w.+-]*\+json|xml|[\w.+-]*\+xml|javascript|x-www-form-urlencoded))", re.I)
MAX_TEXT = 200_000  # an answer longer than this is cut: the log stays small enough to open and attach


def _secret_name(name: str) -> bool:
    return bool(SECRET_KEYS.search(name or ""))


def _replace_known(text: str, secrets: list[str]) -> str:
    for s in secrets:
        if s and len(s) >= 4:
            text = text.replace(s, MASK)
    return text


def _clean_url(url: str, secrets: list[str]) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return _replace_known(url, secrets)
    if parts.query:
        query = urlencode([(k, MASK if _secret_name(k) else v) for k, v in parse_qsl(parts.query, keep_blank_values=True)],
                          safe="•")
        parts = parts._replace(query=query)
    if parts.username or parts.password:
        parts = parts._replace(netloc=parts.hostname + (f":{parts.port}" if parts.port else ""))
    return _replace_known(urlunsplit(parts), secrets)


def _clean_headers(headers: list[dict], secret_names: set[str], secrets: list[str]) -> list[dict]:
    return [{**h, "value": MASK if h.get("name", "").lower() in secret_names else _replace_known(str(h.get("value", "")), secrets)}
            for h in headers or []]


def _clean_cookies(cookies: list[dict]) -> list[dict]:
    return [{**c, "value": MASK} for c in cookies or []]


def _clean_params(params: list[dict], secrets: list[str]) -> list[dict]:
    return [{**p, "value": MASK if _secret_name(p.get("name", "")) else _replace_known(str(p.get("value", "")), secrets)}
            for p in params or []]


def _clean_content(content: dict, secrets: list[str]) -> dict:
    content = dict(content or {})
    mime = content.get("mimeType", "") or ""
    text = content.get("text")
    if text is None:
        return content
    if not TEXT_TYPES.match(mime) or content.get("encoding") == "base64":
        content.pop("text", None)
        content.pop("encoding", None)
        content["comment"] = "Left out by Mirmera: not text (a picture or file can hold someone's ID or face)."
        return content
    if "json" in mime.lower():
        text = _mask_body(text, secrets)  # also masks token/password keys in the answer
    else:
        text = _replace_known(text, secrets)
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT]
        content["comment"] = f"Cut by Mirmera at {MAX_TEXT:,} characters."
    content["text"] = text
    return content


def clean_har(har: dict, secrets: list[str] | None = None) -> dict:
    """The same HAR with cookies, authorization, passwords, tokens and pictures taken out."""
    secrets = [s for s in (secrets or []) if s]
    log = dict(har.get("log") or {})
    log["creator"] = {"name": "Mirmera", "version": "1", "comment": "Secrets, cookies and pictures taken out"}
    entries = []
    for entry in log.get("entries") or []:
        entry = dict(entry)
        req = dict(entry.get("request") or {})
        req["url"] = _clean_url(req.get("url", ""), secrets)
        req["headers"] = _clean_headers(req.get("headers"), SECRET_HEADERS, secrets)
        req["cookies"] = _clean_cookies(req.get("cookies"))
        req["queryString"] = _clean_params(req.get("queryString"), secrets)
        if req.get("postData"):
            post = dict(req["postData"])
            if post.get("text") is not None:
                post["text"] = _mask_body(post["text"], secrets)
            if post.get("params"):
                post["params"] = _clean_params(post["params"], secrets)
            req["postData"] = post
        res = dict(entry.get("response") or {})
        res["headers"] = _clean_headers(res.get("headers"), SECRET_RESPONSE_HEADERS, secrets)
        res["cookies"] = _clean_cookies(res.get("cookies"))
        if res.get("redirectURL"):
            res["redirectURL"] = _clean_url(res["redirectURL"], secrets)
        if res.get("content"):
            res["content"] = _clean_content(res["content"], secrets)
        entry["request"], entry["response"] = req, res
        entries.append(entry)
    log["entries"] = entries
    return {**har, "log": log}


def clean_har_file(source, target, secrets: list[str] | None = None) -> int:
    """Reads Playwright's HAR, writes the cleaned one; the number of requests in it."""
    har = json.loads(source.read_text(encoding="utf-8"))
    cleaned = clean_har(har, secrets)
    target.write_text(json.dumps(cleaned, ensure_ascii=False, indent=1), encoding="utf-8")
    return len(cleaned["log"]["entries"])
