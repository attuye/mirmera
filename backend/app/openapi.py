"""OpenAPI (3.x) and Swagger (2.0) specs: the endpoints, with a ready request for each.

A spec is imported as a project artifact (type "openapi"): its text is kept, and its
endpoint list stands in for the AI summary, so charter suggestions see every endpoint
without an AI reading a huge file. For the API tab each operation becomes a draft
request: method, path, required query and header parameters, a JSON body built from
the schema (examples first), and which auth it needs.
"""
import json
import re
from functools import lru_cache

import yaml

MAX_SPEC_BYTES = 5_000_000
METHODS = ("get", "post", "put", "patch", "delete", "head", "options")
DEPTH = 5  # nested schemas deeper than this become {}


class SpecProblem(Exception):
    """The text isn't an OpenAPI or Swagger spec Mirmera can read. The message says why."""


def load(text: str) -> dict:
    if len(text.encode()) > MAX_SPEC_BYTES:
        raise SpecProblem("The spec is larger than 5 MB.")
    try:
        doc = json.loads(text) if text.lstrip().startswith("{") else yaml.safe_load(text)
    except (ValueError, yaml.YAMLError) as err:
        raise SpecProblem(f"Couldn't read it as JSON or YAML: {str(err).splitlines()[0]}")
    if not isinstance(doc, dict) or not (str(doc.get("openapi", "")).startswith("3") or str(doc.get("swagger", "")) == "2.0"):
        raise SpecProblem("That isn't an OpenAPI 3 or Swagger 2.0 document (no \"openapi: 3…\" or \"swagger: 2.0\" at the top).")
    if not isinstance(doc.get("paths"), dict) or not doc["paths"]:
        raise SpecProblem("The spec has no paths, so there are no endpoints to try.")
    return doc


def _ref(doc: dict, ref: str):
    if not ref.startswith("#/"):
        return {}  # references to other files aren't followed
    node = doc
    for part in ref[2:].split("/"):
        node = node.get(part.replace("~1", "/").replace("~0", "~"), {}) if isinstance(node, dict) else {}
    return node


def _resolve(doc: dict, node, seen: tuple = ()):
    if isinstance(node, dict) and "$ref" in node:
        ref = node["$ref"]
        if ref in seen:
            return {}
        return _resolve(doc, _ref(doc, ref), seen + (ref,))
    return node


FORMAT_EXAMPLES = {"email": "tester@example.com", "date": "2026-01-31", "date-time": "2026-01-31T09:00:00Z",
                   "uuid": "00000000-0000-4000-8000-000000000000", "uri": "https://example.com", "url": "https://example.com",
                   "ipv4": "192.0.2.10", "phone": "+251900000000", "password": "change-me-123", "byte": "aGVsbG8=", "binary": ""}


def example(doc: dict, schema, depth: int = 0, seen: tuple = ()):
    """A value that fits the schema: its example or default, else the first enum, else by type and format."""
    if isinstance(schema, dict) and "$ref" in schema:
        ref = schema["$ref"]
        if ref in seen or depth > DEPTH:
            return {}
        return example(doc, _ref(doc, ref), depth, seen + (ref,))
    if not isinstance(schema, dict):
        return None
    for key in ("example", "default"):
        if key in schema:
            return schema[key]
    if isinstance(schema.get("examples"), list) and schema["examples"]:
        return schema["examples"][0]
    if schema.get("enum"):
        return schema["enum"][0]
    for combo in ("allOf", "oneOf", "anyOf"):
        if isinstance(schema.get(combo), list) and schema[combo]:
            if combo == "allOf":
                merged: dict = {}
                for part in schema["allOf"]:
                    value = example(doc, part, depth + 1, seen)
                    if isinstance(value, dict):
                        merged.update(value)
                return merged
            return example(doc, schema[combo][0], depth + 1, seen)
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), "string")
    if kind == "object" or "properties" in schema:
        if depth > DEPTH:
            return {}
        props = schema.get("properties") or {}
        return {name: example(doc, sub, depth + 1, seen) for name, sub in props.items()}
    if kind == "array":
        return [] if depth > DEPTH else [example(doc, schema.get("items") or {}, depth + 1, seen)]
    if kind == "integer":
        return schema.get("minimum", 1)
    if kind == "number":
        return schema.get("minimum", 1.5)
    if kind == "boolean":
        return True
    fmt = schema.get("format", "")
    if fmt in FORMAT_EXAMPLES:
        return FORMAT_EXAMPLES[fmt]
    return "string" if not schema.get("minLength") else "s" * int(schema["minLength"])


def _auth(doc: dict, op: dict) -> list[str]:
    """What the operation needs, in words: "bearer token", "API key in header X-Api-Key"..."""
    requirements = op.get("security", doc.get("security")) or []
    schemes = (doc.get("components") or {}).get("securitySchemes") or doc.get("securityDefinitions") or {}
    out = []
    for req in requirements:
        for name in req or {}:
            s = schemes.get(name) or {}
            if s.get("type") == "http":
                out.append(f"{s.get('scheme', 'http')} auth".replace("bearer auth", "bearer token"))
            elif s.get("type") == "apiKey":
                out.append(f"API key in {s.get('in', 'header')} {s.get('name', '')}".strip())
            elif s.get("type") in ("oauth2", "openIdConnect"):
                out.append("OAuth token (bearer)")
            elif s.get("type") == "basic":
                out.append("basic auth")
            else:
                out.append(name)
    return sorted(set(out))


def servers(doc: dict) -> list[str]:
    if "swagger" in doc:
        host = doc.get("host")
        base = doc.get("basePath", "")
        if not host:
            return [base] if base else []
        return [f"{(doc.get('schemes') or ['https'])[0]}://{host}{base}"]
    out = []
    for s in doc.get("servers") or []:
        url = s.get("url", "")
        for var, spec in (s.get("variables") or {}).items():
            url = url.replace("{" + var + "}", str(spec.get("default", "")))
        if url and url != "/":
            out.append(url.rstrip("/"))
    return out


def operations(doc: dict) -> list[dict]:
    out = []
    for path, item in doc["paths"].items():
        item = _resolve(doc, item) or {}
        shared = item.get("parameters") or []
        for method in METHODS:
            op = item.get(method)
            if not isinstance(op, dict):
                continue
            params = {}
            for p in shared + (op.get("parameters") or []):
                p = _resolve(doc, p)
                if isinstance(p, dict) and p.get("name"):
                    params[(p.get("in"), p["name"])] = p  # the operation's own override the path's
            path_params, query, headers, body, content_type = {}, [], [], None, None
            for (where, name), p in params.items():
                value = p.get("example", example(doc, p.get("schema") or {k: v for k, v in p.items() if k in ("type", "format", "enum", "default")}))
                if where == "path":
                    path_params[name] = value
                elif where == "query" and p.get("required"):
                    query.append((name, value))
                elif where == "header" and p.get("required"):
                    headers.append({"name": name, "value": str(value)})
                elif where == "body":  # Swagger 2.0
                    body, content_type = example(doc, p.get("schema") or {}), "application/json"
            request_body = _resolve(doc, op.get("requestBody")) or {}
            content = request_body.get("content") or {}
            if content:
                content_type = next((t for t in content if "json" in t), next(iter(content)))
                media = content[content_type] or {}
                body = media.get("example", example(doc, media.get("schema") or {}))
            filled = re.sub(r"\{([^}]+)\}", lambda m: str(path_params[m.group(1)]) if path_params.get(m.group(1)) not in (None, "", "string") else m.group(0), path)
            if query:
                filled += "?" + "&".join(f"{k}={v}" for k, v in query)
            if content_type:
                headers.append({"name": "Content-Type", "value": content_type})
            out.append({
                "key": f"{method.upper()} {path}", "method": method.upper(), "path": path, "url": filled,
                "summary": (op.get("summary") or op.get("operationId") or "").strip()[:200],
                "tag": (op.get("tags") or ["Other"])[0], "deprecated": bool(op.get("deprecated")),
                "auth": _auth(doc, op), "headers": headers,
                "body": json.dumps(body, indent=2, ensure_ascii=False) if body is not None and "json" in (content_type or "") else (str(body) if body is not None else None),
                "unfilled": re.findall(r"\{([^}]+)\}", filled.split("?")[0]),
            })
    return out


@lru_cache(maxsize=8)
def parsed(text: str) -> tuple[dict, list[dict], list[str]]:
    """(info, operations, servers) for a spec's text, cached: specs are read on every pick."""
    doc = load(text)
    info = doc.get("info") or {}
    return ({"title": str(info.get("title") or "API")[:120], "version": str(info.get("version") or "")[:40]},
            operations(doc), servers(doc))


def endpoint_list(text: str) -> str:
    """The artifact's summary: what charter suggestions and the product model read."""
    info, ops, srv = parsed(text)
    lines = [f"OpenAPI: {info['title']} {info['version']}".strip() + f", {len(ops)} endpoints" + (f", server {srv[0]}" if srv else "")]
    for o in ops:
        extra = (" (needs " + ", ".join(o["auth"]) + ")") if o["auth"] else ""
        lines.append(f"- {o['key']}: {o['summary']}{extra}{' [deprecated]' if o['deprecated'] else ''}")
    return "\n".join(lines)[:60_000]


def path_pattern(path: str) -> re.Pattern:
    """/documents/{id} matches /documents/42 (and a server prefix before it)."""
    return re.compile(r"(^|/)" + re.sub(r"\\\{[^}]+\\\}", r"[^/]+", re.escape(path.strip("/"))) + r"/?$")
