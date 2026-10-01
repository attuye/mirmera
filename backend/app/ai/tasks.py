"""What Mirmera asks the AI to do. One function per feature.

Each function pairs a system prompt (instructions, from app/ai/prompts/) with
a user message holding the data, wrapped in tags. Keeping data out of the
instructions, and telling the model that tagged content is data, makes it
much harder for text inside a pasted diff or spec to hijack the model.
"""
import json
from pathlib import Path

from app.ai.base import LLMProvider
from app.ai.llm_json import complete_json
from app.schemas import (
    CharterSuggestion, CharterSuggestionList, LLMDebrief,
    LLMBugDraft, LLMProductModel, LLMRisk, LLMRiskCharter, LLMRiskCharterList, LLMRiskList,
)

PROMPTS_DIR = Path(__file__).parent / "prompts"
PROMPT_VERSIONS = {
    "charter_suggestions": "charter-suggestion-v2",
    "debrief": "debrief-proof-v1",
    "artifact_summary": "artifact-summary-v1",
    "product_model": "product-model-v1",
    "risk_analysis": "risk-analysis-v1",
    "risk_charters": "risk-charters-v1",
    "bug_report": "bug-report-v1",
}
PLANNING_TOKENS = 4000
MAX_ARTIFACT_CHARS = 15_000
DEFAULT_MISSION = "Find problems that matter to users and the business before release."


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


async def suggest_charters(
    provider: LLMProvider, *, project_name: str, artifact_context: str, recent_charters: str
) -> list[CharterSuggestion]:
    user = (
        f"<project>{project_name}</project>\n\n"
        f"<artifacts>\n{artifact_context}\n</artifacts>\n\n"
        f"<recent_charters>\n{recent_charters}\n</recent_charters>"
    )
    result = await complete_json(
        provider, load_prompt("charter_suggestion.md"), user,
        CharterSuggestionList, task="charter_suggestions",
    )
    return result.suggestions


async def draft_debrief(
    provider: LLMProvider, *, charter_mission: str, risks: list[dict], duration_minutes: int,
    notes: list[str], bugs: list[str], time_breakdown: dict,
) -> LLMDebrief:
    """PROOF debrief. notes: "+12 min [tag] text" lines. bugs: one line each."""
    split = ", ".join(f"{k.replace('_', ' ')} {round(v * 100)}%" for k, v in time_breakdown.items()) or "(not enough notes)"
    user = (
        f"<charter>{charter_mission}</charter>\n\n"
        f"<risks>\n{json.dumps(risks, indent=2, ensure_ascii=False) if risks else '(none linked)'}\n</risks>\n\n"
        f"Session length: {duration_minutes} minutes. Time split: {split}.\n\n"
        f"<notes>\n{chr(10).join(notes) or '(no notes logged)'}\n</notes>\n\n"
        f"<bugs>\n{chr(10).join(bugs) or '(none)'}\n</bugs>"
    )
    return await complete_json(
        provider, load_prompt("debrief_draft.md"), user, LLMDebrief, task="debrief",
    )


async def summarize_artifact(provider: LLMProvider, *, raw_content: str, artifact_type: str) -> str:
    user = f'<artifact type="{artifact_type}">\n{raw_content}\n</artifact>'
    reply = await provider.complete(
        load_prompt("artifact_summary.md"), user, max_tokens=500, task="artifact_summary",
    )
    return reply.strip()

# ---- Risk model ----------------------------------------------------------

def _context(project_name: str, testing_mission: str | None) -> str:
    return (
        f"<project>{project_name}</project>\n"
        f"<mission>{testing_mission or DEFAULT_MISSION}</mission>"
    )


async def build_product_model(
    provider: LLMProvider, *, project_name: str, testing_mission: str | None,
    artifacts: list[tuple[str, str, str]],
) -> tuple[LLMProductModel, list[str]]:
    """artifacts: (type, label, content). Returns the model and the labels of
    artifacts that were cut to fit."""
    blocks, truncated = [], []
    for kind, label, content in artifacts:
        if len(content) > MAX_ARTIFACT_CHARS:
            truncated.append(label)
            content = content[:MAX_ARTIFACT_CHARS] + "\n[...truncated]"
        safe_label = label.replace('"', "'")
        blocks.append(f'<artifact type="{kind}" label="{safe_label}">\n{content}\n</artifact>')
    user = _context(project_name, testing_mission) + "\n\n" + "\n\n".join(blocks)
    model = await complete_json(
        provider, load_prompt("product_model.md"), user, LLMProductModel,
        task="product_model", max_tokens=PLANNING_TOKENS,
    )
    return model, truncated


async def analyze_risks(
    provider: LLMProvider, *, project_name: str, testing_mission: str | None,
    product_model: dict, max_risks: int, existing_titles: list[str],
) -> list[LLMRisk]:
    existing = "\n".join(f"- {t}" for t in existing_titles) or "(none)"
    user = (
        f"{_context(project_name, testing_mission)}\n\n"
        f"Return at most {max_risks} risks.\n\n"
        f"<product_model>\n{json.dumps(product_model, indent=2, ensure_ascii=False)}\n</product_model>\n\n"
        f"<existing_risks>\n{existing}\n</existing_risks>"
    )
    result = await complete_json(
        provider, load_prompt("risk_analysis.md"), user, LLMRiskList,
        task="risk_analysis", max_tokens=PLANNING_TOKENS,
    )
    return result.risks[:max_risks]


async def charters_from_risks(
    provider: LLMProvider, *, project_name: str, testing_mission: str | None,
    product_summary: str, risks: list[dict], max_charters: int,
) -> list[LLMRiskCharter]:
    """risks: dicts with ref, title, failure_mode, quality_criterion, score, evidence, tester_notes."""
    user = (
        f"{_context(project_name, testing_mission)}\n\n"
        f"<product_summary>{product_summary}</product_summary>\n\n"
        f"Return at most {max_charters} charters.\n\n"
        f"<risks>\n{json.dumps(risks, indent=2, ensure_ascii=False)}\n</risks>"
    )
    result = await complete_json(
        provider, load_prompt("risk_charters.md"), user, LLMRiskCharterList,
        task="risk_charters", max_tokens=PLANNING_TOKENS,
    )
    return result.charters[:max_charters]


# ---- Bug reports -----------------------------------------------------------

async def draft_bug_report(
    provider: LLMProvider, *, charter_mission: str, risks: list[dict],
    note: str, trail: list[dict], environment: dict,
) -> LLMBugDraft:
    """note: the tester's quick bug title and description. trail: the session
    notes logged just before it. risks: the risks behind the session's charter."""
    trail_text = "\n".join(f"[{n.get('tag')}] {n.get('timestamp', '')} {n.get('content')}" for n in trail)
    user = (
        f"<charter>{charter_mission}</charter>\n\n"
        f"<risks>\n{json.dumps(risks, indent=2, ensure_ascii=False) if risks else '(none linked)'}\n</risks>\n\n"
        f"<note>\n{note}\n</note>\n\n"
        f"<trail>\n{trail_text or '(no notes before this bug)'}\n</trail>\n\n"
        f"<environment>\n{json.dumps(environment, ensure_ascii=False) if environment else '(not recorded)'}\n</environment>"
    )
    return await complete_json(
        provider, load_prompt("bug_report.md"), user, LLMBugDraft, task="bug_report",
    )
