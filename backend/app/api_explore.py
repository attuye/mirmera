"""API exploration in a session: build a request (typed, from a curl command, or replayed
from what the recorded browser saw), send it from the server, and keep it as evidence.

What is kept is redacted: credentials in headers, password/token fields in bodies and
query strings become "•••". The tester sees the full response; the evidence keeps the
start of it. Requests go out from the Mirmera server, so localhost and internal staging
work; API_BLOCK_PRIVATE=true refuses private and cloud-metadata addresses (for a server
other companies use).
"""
import ipaddress
import json
import re
import shlex
import socket
import time
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit

import httpx

from app.config import settings

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
MASK = "•••"
SENSITIVE_HEADER = re.compile(r"^(authorization|proxy-authorization|cookie|set-cookie)$|token|secret|api[-_]?key|session|(^|-)auth|password|signature", re.I)
SENSITIVE_FIELD = re.compile(r"pass(word|wd)?|secret|token|api[-_]?key|(^|[_-])auth([_-]|$)|authoriz|credential|session|cvv"
                             r"|card[-_]?number|(^|[_-])pin$|(^|[_-])otp$|signature|^sig$|^code$", re.I)
SHOWN_BYTES = 200_000  # the response body shown to the tester
KEPT_BYTES = 20_000  # the response body kept in the evidence
KEPT_REQUEST_BYTES = 10_000
READ_BYTES = 2_000_000  # stop reading a response after this


class ApiProblem(Exception):
    """The request can't be sent as asked. The message says why."""


# ---------- redaction ----------

def redact_headers(headers: list[dict]) -> list[dict]:
    return [{"name": h["name"], "value": MASK if SENSITIVE_HEADER.search(h["name"]) else h["value"]} for h in headers]


def _redact_json(value):
    if isinstance(value, dict):
        return {k: (MASK if SENSITIVE_FIELD.search(str(k)) and not isinstance(v, (dict, list)) else _redact_json(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_json(v) for v in value]
    return value


def redact_body(body: str | None, content_type: str | None = "") -> str | None:
    """Password and token fields blanked in JSON and form bodies; other text kept as is."""
    if not body:
        return body
    text = body.strip()
    if text[:1] in "{[":
        try:
            return json.dumps(_redact_json(json.loads(text)), ensure_ascii=False)
        except ValueError:
            pass
    if "form-urlencoded" in (content_type or "") or (re.fullmatch(r"[\w.\-%+]+=[^&\s]*(&[\w.\-%+]+=[^&\s]*)*", text) is not None):
        pairs = parse_qsl(text, keep_blank_values=True)
        return urlencode([(k, MASK if SENSITIVE_FIELD.search(k) else v) for k, v in pairs])
    return body


def redact_url(url: str) -> str:
    """Query values kept (they matter when exploring), except password/token-like ones."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.query:
        return url
    query = "&".join(f"{quote(k, safe='')}={MASK if SENSITIVE_FIELD.search(k) else quote(v, safe='')}"
                     for k, v in parse_qsl(parts.query, keep_blank_values=True))
    return urlunsplit(parts._replace(query=query))


def _cut(text: str | None, limit: int) -> str | None:
    if text is None or len(text) <= limit:
        return text
    return text[:limit] + f"\n[… {len(text) - limit} more characters not kept]"


# ---------- curl ----------

def parse_curl(command: str) -> dict:
    """A curl command (from DevTools "Copy as cURL", docs, a teammate) as a request:
    method, url, headers, body. Bash and Windows cmd line breaks are both understood."""
    text = re.sub(r"\\\r?\n|\^\r?\n|`\r?\n", " ", command.strip())
    if text.startswith("curl.exe"):
        text = "curl" + text[len("curl.exe"):]
    if not text.startswith("curl"):
        raise ApiProblem("That doesn't look like a curl command (it should start with curl).")
    try:
        args = shlex.split(text[4:].replace("^\"", '"'), posix=True)
    except ValueError as err:
        raise ApiProblem(f"Couldn't read the command: {err}.")
    method, url, headers, body, get_params = None, None, [], None, False
    data_flags = {"-d", "--data", "--data-raw", "--data-binary", "--data-ascii", "--data-urlencode"}
    skip_one = {"-o", "--output", "-w", "--write-out", "--connect-timeout", "-m", "--max-time", "--retry", "-x", "--proxy", "-e", "--referer"}
    i = 0
    while i < len(args):
        a = args[i]
        nxt = args[i + 1] if i + 1 < len(args) else None
        if a in ("-X", "--request") and nxt:
            method, i = nxt.upper(), i + 2
            continue
        if a in ("-H", "--header") and nxt:
            name, _, value = nxt.partition(":")
            headers.append({"name": name.strip(), "value": value.strip()})
            i += 2
            continue
        if a in data_flags and nxt is not None:
            body = nxt if body is None else f"{body}&{nxt}"
            i += 2
            continue
        if a == "--json" and nxt is not None:
            body = nxt
            headers += [{"name": "Content-Type", "value": "application/json"}, {"name": "Accept", "value": "application/json"}]
            i += 2
            continue
        if a in ("-u", "--user") and nxt:
            import base64
            headers.append({"name": "Authorization", "value": "Basic " + base64.b64encode(nxt.encode()).decode()})
            i += 2
            continue
        if a in ("-b", "--cookie") and nxt:
            headers.append({"name": "Cookie", "value": nxt})
            i += 2
            continue
        if a in ("-A", "--user-agent") and nxt:
            headers.append({"name": "User-Agent", "value": nxt})
            i += 2
            continue
        if a == "--url" and nxt:
            url, i = nxt, i + 2
            continue
        if a in ("-I", "--head"):
            method, i = "HEAD", i + 1
            continue
        if a in ("-G", "--get"):
            get_params, i = True, i + 1
            continue
        if a in skip_one:
            i += 2
            continue
        if a.startswith("-"):
            i += 1  # --compressed, -k, -L, -s, -v...: nothing to change in the request
            continue
        url, i = url or a, i + 1
    if not url:
        raise ApiProblem("The curl command has no address in it.")
    if get_params and body:
        url, body = url + ("&" if "?" in url else "?") + body, None
    method = method or ("POST" if body is not None else "GET")
    if method not in METHODS:
        raise ApiProblem(f"Mirmera can send {', '.join(METHODS)}, not {method}.")
    return {"method": method, "url": url, "headers": headers, "body": body}


# ---------- what the browser recorded ----------

RECORDED = re.compile(r"^([A-Z]+) (https?://\S+)")


def recorded_requests(evidence: list) -> list[dict]:
    """Requests the recorded browser saw fail or go slow in this session, newest first, once each."""
    out, seen = [], set()
    for e in sorted(evidence, key=lambda e: e.last_seen_at or e.captured_at, reverse=True):
        if e.kind not in ("failed_request", "slow_request"):
            continue
        m = RECORDED.match(e.summary or "")
        if not m or m.group(1) not in METHODS:
            continue  # "Page … took longer" or "Could not open …": not an API call to replay
        key = (m.group(1), m.group(2))
        if key in seen:
            continue
        seen.add(key)
        data = e.data or {}
        headers = [{"name": "Content-Type", "value": data["content_type"]}] if data.get("content_type") else []
        out.append({"method": m.group(1), "url": m.group(2), "headers": headers, "body": data.get("body"),
                    "seen": e.summary, "count": e.count or 1, "kind": e.kind})
    return out


# ---------- sending ----------

def resolve(url: str, base: str | None) -> str:
    url = url.strip()
    if url.startswith("/") and base:
        return urljoin(base.rstrip("/") + "/", url.lstrip("/"))
    return url


def check_target(url: str) -> None:
    try:
        parts = urlsplit(url)
    except ValueError:
        raise ApiProblem("That address can't be read.")
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ApiProblem("Write a full address starting with http:// or https://, or a path like /api/… when the project has an app URL.")
    if not settings.api_block_private:
        return
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
    except socket.gaierror:
        raise ApiProblem(f"Can't find the host {parts.hostname}.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise ApiProblem(f"{parts.hostname} is a private address ({ip}); this server only sends to public ones (API_BLOCK_PRIVATE).")


def send(method: str, url: str, headers: list[dict], body: str | None, transport=None) -> dict:
    """Send once, without following redirects (a redirect is something to see), and measure it."""
    if method not in METHODS:
        raise ApiProblem(f"Use one of {', '.join(METHODS)}.")
    check_target(url)
    sent_headers = [(h["name"].strip(), h["value"]) for h in headers if h.get("name", "").strip()]
    started = time.monotonic()
    try:
        with httpx.Client(timeout=settings.api_timeout_seconds, follow_redirects=False, transport=transport) as client:
            with client.stream(method, url, headers=sent_headers,
                               content=body.encode() if body and method not in ("GET", "HEAD") else None) as r:
                raw = b""
                for chunk in r.iter_bytes():
                    raw += chunk
                    if len(raw) > READ_BYTES:
                        break
                ms = round((time.monotonic() - started) * 1000)
                text = raw.decode(r.encoding or "utf-8", errors="replace")
                return {"status": r.status_code, "reason": r.reason_phrase, "ms": ms, "size": len(raw),
                        "headers": [{"name": k, "value": v} for k, v in r.headers.items()],
                        "content_type": r.headers.get("content-type", ""), "body": _cut(text, SHOWN_BYTES),
                        "truncated": len(raw) > READ_BYTES, "error": None}
    except httpx.TimeoutException:
        error = f"No answer within {settings.api_timeout_seconds} seconds"
    except httpx.HTTPError as err:
        error = f"Could not connect: {str(err) or type(err).__name__}"
    return {"status": None, "reason": None, "ms": round((time.monotonic() - started) * 1000), "size": 0, "headers": [],
            "content_type": "", "body": None, "truncated": False, "error": error}


def evidence_fields(method: str, url: str, headers: list[dict], body: str | None, result: dict, source: str) -> dict:
    """What is kept: redacted request, response start, timing."""
    shown_url = redact_url(url)
    outcome = f"{result['status']} ({result['ms']} ms)" if result["status"] is not None else result["error"]
    content_type = next((h["value"] for h in headers if h["name"].lower() == "content-type"), "")
    return {
        "kind": "api_call", "url": shown_url, "summary": f"{method} {shown_url} → {outcome}",
        "data": {
            "method": method, "source": source, "request_headers": redact_headers(headers),
            "request_body": _cut(redact_body(body, content_type), KEPT_REQUEST_BYTES),
            "status": result["status"], "ms": result["ms"], "size": result["size"], "error": result["error"],
            "response_headers": redact_headers(result["headers"]), "content_type": result["content_type"],
            "response_body": _cut(result["body"], KEPT_BYTES),
        },
    }
