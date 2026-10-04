"""Which AI provider does which task, per project.

Each project has a default provider and, optionally, a provider per task, so
cheap tasks can run on a local model while the ones that need judgement use a
stronger one. A request may still override both (the API's `provider` field).
"""
from app.ai.base import PROVIDER_NAMES, LLMProvider, ProviderNotConfigured, get_provider

# key, label, why it matters: shown on the project's Settings page
AI_TASKS: list[dict] = [
    {"key": "artifact_summary", "label": "Summarise specs and diffs", "hint": "Short summaries; small models do fine"},
    {"key": "product_model", "label": "Model the product", "hint": "Long input, structured output"},
    {"key": "risk_analysis", "label": "Propose risks", "hint": "Judgement: what could fail, for whom"},
    {"key": "risk_charters", "label": "Charters from risks", "hint": "Turns accepted risks into missions"},
    {"key": "charter_suggestions", "label": "Charters from a diff", "hint": "Suggestions from pasted changes"},
    {"key": "debrief", "label": "Draft debriefs", "hint": "Summarises notes; small models do fine"},
    {"key": "bug_report", "label": "Draft bug reports", "hint": "Needs an oracle: judgement"},
    {"key": "risk_update", "label": "Suggest risk re-scores", "hint": "Must quote evidence"},
    {"key": "duplicate_check", "label": "Check tracker duplicates", "hint": "Compares titles and steps"},
    {"key": "regression", "label": "Write regression tests", "hint": "Checks that assert the oracle"},
    {"key": "agent", "label": "Reproduction agent", "hint": "Many quick steps: speed matters"},
    {"key": "report_summary", "label": "Summarise the test report", "hint": "Short; must not invent numbers"},
]
TASK_KEYS = tuple(t["key"] for t in AI_TASKS)


def provider_name_for(project, task: str, override: str | None = None) -> str:
    assert task in TASK_KEYS, task
    return override or (project.ai_task_providers or {}).get(task) or project.default_ai_provider


def provider_for(project, task: str, override: str | None = None) -> LLMProvider:
    """The provider for this task in this project: a request's override, else the
    task's own choice, else the project default."""
    return get_provider(provider_name_for(project, task, override))


def provider_status() -> list[dict]:
    """Every provider, whether it is set up (an API key, for example) and its model.
    A local model that isn't running only shows up when it is called."""
    out = []
    for name in PROVIDER_NAMES:
        try:
            p = get_provider(name)
            out.append({"name": name, "configured": True, "model": p.model, "problem": None})
        except ProviderNotConfigured as err:
            out.append({"name": name, "configured": False, "model": None, "problem": str(err)})
    return out
