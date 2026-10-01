"""Turning the AI's plan (indexes into the recorded actions) into a resolved
plan that carries the actions themselves, so the test no longer depends on
the recording, and checking it against what was actually recorded."""
import re
from urllib.parse import urlsplit

from app import models, schemas
from app.runner import describe_action

MAX_ACTIONS = 80  # most recent actions before the bug given to the AI


def is_same_origin(a: str, b: str) -> bool:
    pa, pb = urlsplit(a), urlsplit(b)
    return (pa.scheme, pa.netloc) == (pb.scheme, pb.netloc)


def origin(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}"


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s[:60].rstrip("-") or "regression")


def action_lines(actions: list[models.Evidence], start) -> list[str]:
    """'[3] +2 min on /upload: fill label "Full name" = "Abebe"' for the prompt."""
    lines = []
    for i, e in enumerate(actions):
        minute = max(0, int((e.captured_at - start).total_seconds() // 60))
        where = urlsplit(e.url).path if e.url else ""
        lines.append(f"[{i}] +{minute} min on {where or '?'}: {e.summary}")
    return lines


def resolve(plan: schemas.LLMRegressionPlan, actions: list[models.Evidence], pages: list[str],
            base_url: str | None) -> tuple[dict, list[str]]:
    """Returns the resolved plan and warnings about anything dropped."""
    warnings: list[str] = []
    steps = []
    for s in plan.steps:
        if s.kind == "goto":
            if s.url not in pages:
                warnings.append(f"Dropped a step that opened {s.url}: that page was not visited in the session.")
                continue
            steps.append({"type": "goto", "url": s.url, "why": s.why})
        else:
            if not 0 <= s.action_index < len(actions):
                warnings.append(f"Dropped a step pointing at recorded action [{s.action_index}], which does not exist.")
                continue
            e = actions[s.action_index]
            steps.append({"type": "action", "action": {**(e.data or {}), "summary": e.summary}, "why": s.why,
                          "recorded": s.action_index, "url": e.url})
    if not steps or steps[0]["type"] != "goto":
        first = next((s.get("url") for s in steps if s.get("url")), None) or (pages[0] if pages else None)
        if first:
            steps.insert(0, {"type": "goto", "url": first, "why": "Open the page where the steps start"})
            warnings.append("Added a first step opening the page where the recorded steps start.")
    return {
        "title": plan.test_title,
        "base_url": base_url,
        "steps": steps,
        "assertions": [a.model_dump(exclude_none=True) for a in plan.assertions],
        "assumptions": plan.assumptions,
    }, warnings


def describe(step: dict) -> str:
    if step["type"] == "goto":
        return f"open {step['url']}"
    return step["action"].get("summary") or describe_action(step["action"])
