"""A failed request as a command a developer can run: "copy as curl", in a bug report.

From the request the browser sent (method, address, headers, body), without what would hand out
someone's access: no cookies or authorization headers (a placeholder says to use your own
session), and passwords, tokens and PINs in the body masked. The headers a browser adds on its own
(user agent, sec-*, accept-language…) are left out: they rarely matter and make it hard to read.
"""
import json
import re
import shlex

SECRET_HEADERS = {"cookie", "authorization", "proxy-authorization", "x-csrf-token", "x-xsrf-token", "x-api-key"}
NOISE = re.compile(r"^(sec-|user-agent$|accept-language$|accept-encoding$|referer$|connection$|content-length$|host$|"
                   r"origin$|pragma$|cache-control$|priority$|dnt$|upgrade-insecure-requests$)", re.I)
SECRET_KEYS = re.compile(r"pass(word)?|secret|token|pin\b|otp|cvv|api[_-]?key", re.I)
MASK = "•••"


def _mask_body(body: str, secrets: list[str]) -> str:
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        data = None
    if isinstance(data, (dict, list)):
        def walk(x):
            if isinstance(x, dict):
                return {k: (MASK if SECRET_KEYS.search(k) and isinstance(v, (str, int)) else walk(v)) for k, v in x.items()}
            if isinstance(x, list):
                return [walk(v) for v in x]
            return x
        body = json.dumps(walk(data), ensure_ascii=False)
    else:  # a form: name=value&…
        body = re.sub(r"((?:^|&)[^=&]*(?:pass|secret|token|pin|otp)[^=&]*=)[^&]*", rf"\1{MASK}", body, flags=re.I)
    for s in secrets:
        if s and len(s) >= 4:
            body = body.replace(s, MASK)
    return body


def curl_of(method: str, url: str, headers: dict[str, str] | None = None, body: str | None = None,
            secrets: list[str] | None = None) -> str:
    """One curl command, one option per line (bash, macOS, Linux; in PowerShell use curl.exe)."""
    first = "curl " + (f"-X {method.upper()} " if method.upper() != "GET" or body else "") + shlex.quote(url)
    options: list[str] = []
    used_secret = False
    for k, v in sorted((headers or {}).items()):
        if k.lower() in SECRET_HEADERS:
            used_secret = True
            continue
        if NOISE.match(k) or k.startswith(":"):
            continue
        options.append("-H " + shlex.quote(f"{k}: {v}"))
    if used_secret:
        options.append("-H " + shlex.quote("Cookie: <your session: copy it from your browser>"))
    if body:
        options.append("--data-raw " + shlex.quote(_mask_body(body, secrets or [])))
    lines = [first] + options
    return " \\\n  ".join(lines)
