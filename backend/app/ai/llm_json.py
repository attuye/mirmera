"""Ask a provider for JSON and get back a validated Pydantic object.

Models sometimes wrap JSON in prose or code fences, or return something that
doesn't match the schema. This finds the JSON object, validates it, and on
failure retries once, telling the model exactly what was wrong.
"""
import json
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


def _parse(raw: str, model_cls: type[T]) -> T:
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object found in the reply")
    return model_cls.model_validate_json(raw[start: end + 1])


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