"""Ask a provider for JSON and get back a validated Pydantic object.

Models sometimes wrap JSON in prose or code fences, or return something that
doesn't match the schema. This finds the JSON object, validates it, and on
failure retries once, telling the model exactly what was wrong.
"""
import ast
import json
import re
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.ai.base import LLMProvider, ProviderError

T = TypeVar("T", bound=BaseModel)


class LLMOutputError(ProviderError):
    """The model's reply could not be turned into the expected shape."""


def schema_instructions(model_cls: type[BaseModel]) -> str:
    return (
        "Reply with a single JSON object matching this JSON Schema, and nothing else:\n"
        + json.dumps(model_cls.model_json_schema())
    )


def _objects(raw: str) -> list[str]:
    """Every top-level {...} in the reply, in order (strings and escapes respected).
    Reasoning models think aloud first (<think>…</think>), sometimes with braces in it."""
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.S | re.I)
    out, depth, start, quote, escaped = [], 0, -1, "", False
    for i, ch in enumerate(raw):
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = ""
        elif ch in "\"'" and depth:
            quote = ch
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0:
                out.append(raw[start: i + 1])
    return out


def _without_comments(text: str) -> str:
    """Removes // and /* */ comments outside strings."""
    out, i, quote = [], 0, ""
    while i < len(text):
        ch = text[i]
        if quote:
            out.append(ch)
            if ch == "\\" and i + 1 < len(text):
                out.append(text[i + 1])
                i += 1
            elif ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
            out.append(ch)
        elif text.startswith("//", i):
            while i < len(text) and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = len(text) if end == -1 else end + 2
            continue
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _loads(text: str):
    """JSON, or the near-JSON small models write: comments, trailing commas, single quotes."""
    try:
        return json.loads(text)
    except json.JSONDecodeError as first:
        fixed = re.sub(r",\s*([}\]])", r"\1", _without_comments(text))
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass
        try:  # single-quoted: read it as a Python literal
            value = ast.literal_eval(re.sub(r"\btrue\b", "True", re.sub(r"\bfalse\b", "False", re.sub(r"\bnull\b", "None", fixed))))
            if isinstance(value, dict):
                return value
        except (ValueError, SyntaxError):
            pass
        raise ValueError(f"Invalid JSON: {first}") from first


def _parse(raw: str, model_cls: type[T]) -> T:
    """The reply's JSON object as model_cls. When a reply holds several objects (a plan, then
    the answer), the last one that fits wins."""
    candidates = _objects(raw)
    if not candidates:
        raise ValueError("No JSON object found in the reply")
    errors: list[Exception] = []
    for text in reversed(candidates):
        try:
            return model_cls.model_validate(_loads(text))
        except (ValidationError, ValueError) as err:
            errors.append(err)
    raise errors[0]


async def complete_json(
    provider: LLMProvider,
    system: str,
    user: str,
    model_cls: type[T],
    *,
    task: str,
    max_tokens: int = 2000,
) -> T:
    system = f"{system}\n\n{schema_instructions(model_cls)}"
    schema = model_cls.model_json_schema()

    raw = await provider.complete(system, user, max_tokens=max_tokens, schema=schema, task=task)
    try:
        return _parse(raw, model_cls)
    except (ValidationError, ValueError) as err:
        retry_user = (
            f"{user}\n\nYour previous reply was invalid:\n{err}\n"
            "Return only a corrected JSON object."
        )

    raw = await provider.complete(system, retry_user, max_tokens=max_tokens, schema=schema, task=task)
    try:
        return _parse(raw, model_cls)
    except (ValidationError, ValueError) as err:
        raise LLMOutputError(
            f"The AI ({provider.name}) returned an unusable reply twice. Try again; if it keeps happening, "
            f"a larger model or another AI provider (project Settings) usually helps. Details: {err}"
        ) from err