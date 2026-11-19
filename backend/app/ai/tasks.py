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
from app.evidence_text import NOT_USED
from app.schemas import (
    CharterSuggestion, CharterSuggestionList, LLMDebrief,
    LLMBugDraft, LLMDuplicateList, LLMRegressionChecks, LLMRegressionPlan, LLMProductModel, LLMRiskUpdateList, LLMRisk, LLMRiskCharter, LLMRiskCharterList, LLMRiskList, LLMReportSummary, LLMStoryRisks,
)

PROMPTS_DIR = Path(__file__).parent / "prompts"

PROMPT_VERSIONS = {
    "charter_suggestions": "charter-suggestion-v3",
    "debrief": "debrief-proof-v2",
    "artifact_summary": "artifact-summary-v1",
    "product_model": "product-model-v1",
    "risk_analysis": "risk-analysis-v1",
    "risk_charters": "risk-charters-v1",
    "bug_report": "bug-report-v2",
    "risk_update": "risk-update-v1",
    "duplicate_check": "duplicate-check-v1",
    "regression_test": "regression-test-v2",
    "regression_checks": "regression-checks-v1",
    "report_summary": "report-summary-v1",
    "test_strategy": "test-strategy-v1",
    "story_risks": "story-risks-v1",
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
    notes: list[str], bugs: list[str], time_breakdown: dict, browser: str = NOT_USED,
) -> LLMDebrief:
    """PROOF debrief. notes: "+12 min [tag] text" lines. bugs: one line each.
    browser: what the recorded browser captured (app.evidence_text.browser_context)."""
    split = ", ".join(f"{k.replace('_', ' ')} {round(v * 100)}%" for k, v in time_breakdown.items()) or "(not enough notes)"
    user = (
        f"<charter>{charter_mission}</charter>\n\n"
        f"<risks>\n{json.dumps(risks, indent=2, ensure_ascii=False) if risks else '(none linked)'}\n</risks>\n\n"
        f"Session length: {duration_minutes} minutes. Time split: {split}.\n\n"
        f"<notes>\n{chr(10).join(notes) or '(no notes logged)'}\n</notes>\n\n"
        f"<bugs>\n{chr(10).join(bugs) or '(none)'}\n</bugs>\n\n"
        f"<browser>\n{browser}\n</browser>"
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


async def story_risks(
    provider: LLMProvider, *, project_name: str, testing_mission: str | None, story: dict,
    product_model: dict | None, project_risks: list[dict], max_risks: int,
) -> LLMStoryRisks:
    """story: key, title, description. project_risks: ref, title, failure_mode, quality_criterion,
    score, linked (already linked to this story)."""
    user = (
        f"{_context(project_name, testing_mission)}\n\n"
        f"Return at most {max_risks} new risks.\n\n"
        f"<story>\n{json.dumps(story, indent=2, ensure_ascii=False)}\n</story>\n\n"
        f"<product_model>\n{json.dumps(product_model, indent=2, ensure_ascii=False) if product_model else '(none yet)'}\n</product_model>\n\n"
        f"<project_risks>\n{json.dumps(project_risks, indent=2, ensure_ascii=False) if project_risks else '(none)'}\n</project_risks>"
    )
    result = await complete_json(
        provider, load_prompt("story_risks.md"), user, LLMStoryRisks,
        task="story_risks", max_tokens=PLANNING_TOKENS,
    )
    result.new_risks = result.new_risks[:max_risks]
    return result


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
    note: str, trail: list[dict], environment: dict, browser: str = NOT_USED,
) -> LLMBugDraft:
    """note: the tester's quick bug title and description. trail: the session
    notes logged just before it. risks: the risks behind the session's charter.
    browser: what the recorded browser captured just before the bug."""
    trail_text = "\n".join(f"[{n.get('tag')}] {n.get('timestamp', '')} {n.get('content')}" for n in trail)
    user = (
        f"<charter>{charter_mission}</charter>\n\n"
        f"<risks>\n{json.dumps(risks, indent=2, ensure_ascii=False) if risks else '(none linked)'}\n</risks>\n\n"
        f"<note>\n{note}\n</note>\n\n"
        f"<trail>\n{trail_text or '(no notes before this bug)'}\n</trail>\n\n"
        f"<environment>\n{json.dumps(environment, ensure_ascii=False) if environment else '(not recorded)'}\n</environment>\n\n"
        f"<browser>\n{browser}\n</browser>"
    )
    return await complete_json(
        provider, load_prompt("bug_report.md"), user, LLMBugDraft, task="bug_report",
    )


async def suggest_risk_updates(
    provider: LLMProvider, *, charter_mission: str, risks: list[dict], notes: list[str],
    bugs: list[str], browser: str = NOT_USED, debrief: dict | None = None,
) -> LLMRiskUpdateList:
    """After a session: suggested re-scores for the charter's risks, with evidence.
    debrief: the tester's results and obstacles, if the debrief exists."""
    user = (
        f"<charter>{charter_mission}</charter>\n\n"
        f"<risks>\n{json.dumps(risks, indent=2, ensure_ascii=False)}\n</risks>\n\n"
        f"<notes>\n{chr(10).join(notes) or '(no notes logged)'}\n</notes>\n\n"
        f"<bugs>\n{chr(10).join(bugs) or '(none)'}\n</bugs>\n\n"
        f"<browser>\n{browser}\n</browser>\n\n"
        f"<debrief>\n{json.dumps(debrief, indent=2, ensure_ascii=False) if debrief else '(no debrief yet)'}\n</debrief>"
    )
    return await complete_json(
        provider, load_prompt("risk_update.md"), user, LLMRiskUpdateList, task="risk_update",
    )


async def check_duplicates(provider: LLMProvider, *, report: dict, candidates: list[dict]) -> LLMDuplicateList:
    """report: the reviewed bug (title, steps, expected, actual, kind).
    candidates: issues from a tracker search (key, title, status, snippet)."""
    user = (
        f"<report>\n{json.dumps(report, indent=2, ensure_ascii=False)}\n</report>\n\n"
        f"<candidates>\n{json.dumps(candidates, indent=2, ensure_ascii=False)}\n</candidates>"
    )
    return await complete_json(
        provider, load_prompt("duplicate_check.md"), user, LLMDuplicateList, task="duplicate_check",
    )


async def write_regression_checks(provider: LLMProvider, *, report: dict, steps: list[str],
                                  browser: str) -> LLMRegressionChecks:
    """The steps are fixed (picked by Mirmera from the recording, or edited by the
    tester); the AI writes only what the test asserts afterwards."""
    user = (
        f"<report>\n{json.dumps(report, indent=2, ensure_ascii=False)}\n</report>\n\n"
        f"<steps>\n{chr(10).join(steps)}\n</steps>\n\n"
        f"<browser>\n{browser}\n</browser>"
    )
    return await complete_json(
        provider, load_prompt("regression_checks.md"), user, LLMRegressionChecks, task="regression_checks",
    )


async def plan_regression_test(provider: LLMProvider, *, report: dict, actions: list[str], pages: list[str],
                               browser: str) -> LLMRegressionPlan:
    """report: the reviewed bug. actions: '[i] +m min on /path: summary' lines.
    pages: URLs visited before the bug. browser: what the page reported."""
    user = (
        f"<report>\n{json.dumps(report, indent=2, ensure_ascii=False)}\n</report>\n\n"
        f"<actions>\n{chr(10).join(actions) or '(no actions were recorded)'}\n</actions>\n\n"
        f"<pages>\n{chr(10).join(pages) or '(none)'}\n</pages>\n\n"
        f"<browser>\n{browser}\n</browser>"
    )
    return await complete_json(
        provider, load_prompt("regression_test.md"), user, LLMRegressionPlan, task="regression_test",
    )


# ---- Report summary -----------------------------------------------------------

def ungrounded_numbers(text: str, facts: list[str]) -> list[str]:
    """Numbers in the summary that appear in none of the facts (the AI made them up)."""
    import re
    known = set(re.findall(r"\d+(?:[.,]\d+)?", "\n".join(facts)))
    return sorted({n for n in re.findall(r"\d+(?:[.,]\d+)?", text) if n not in known})


async def summarize_report(provider: LLMProvider, *, project_name: str, facts: list[str]) -> LLMReportSummary:
    """facts: the report's own lines (period, totals, at a glance, charts, gaps, top bugs).
    The summary may only use numbers found in them; one retry says which ones were invented."""
    from app.ai.llm_json import LLMOutputError
    numbered = "\n".join(f"{i}. {f}" for i, f in enumerate(facts, 1))
    user = f"<project>{project_name}</project>\n\n<facts>\n{numbered}\n</facts>"
    system = load_prompt("report_summary.md")
    result = await complete_json(provider, system, user, LLMReportSummary, task="report_summary", max_tokens=800)
    bad = ungrounded_numbers(result.summary, facts)
    if bad:
        retry = f"{user}\n\nYour summary used numbers that are not in the facts: {', '.join(bad)}. Use only numbers from <facts>."
        result = await complete_json(provider, system, retry, LLMReportSummary, task="report_summary", max_tokens=800)
        bad = ungrounded_numbers(result.summary, facts)
        if bad:
            raise LLMOutputError(
                f"The AI ({provider.name}) kept using numbers that are not in the report ({', '.join(bad)}), so its "
                "summary was not shown. Try again, or choose a stronger model for 'Summarise the test report' in Settings.")
    joined = "\n".join(facts)
    result.basis = [b.strip() for b in result.basis if b.strip() and b.strip() in joined][:8]  # only real lines
    return result


# ---- Test strategy --------------------------------------------------------------

async def write_strategy(provider: LLMProvider, *, project_name: str, focus: str, facts: dict[str, list[str]]):
    """Prose for each strategy section from the facts. Numbers must come from the facts; one retry."""
    from app.ai.llm_json import LLMOutputError
    from app.schemas import LLMStrategy
    lines = [line for section in facts.values() for line in section]
    blocks = "\n\n".join(f"[{key}]\n" + ("\n".join(f"- {l}" for l in rows) or "(nothing recorded)") for key, rows in facts.items())
    user = f"<project>{project_name}</project>\n<focus>{focus}</focus>\n\n<facts>\n{blocks}\n</facts>"
    system = load_prompt("test_strategy.md")
    result = await complete_json(provider, system, user, LLMStrategy, task="test_strategy", max_tokens=3000)
    bad = ungrounded_numbers("\n".join(result.sections.values()), lines)
    if bad:
        retry = f"{user}\n\nYour draft used numbers that are not in the facts: {', '.join(bad)}. Use only numbers from <facts>."
        result = await complete_json(provider, system, retry, LLMStrategy, task="test_strategy", max_tokens=3000)
        bad = ungrounded_numbers("\n".join(result.sections.values()), lines)
        if bad:
            raise LLMOutputError(
                f"The AI ({provider.name}) kept using numbers that are not in the project's facts ({', '.join(bad)}), so its "
                "draft was not shown. Try again, or choose a stronger model for 'Write the test strategy' in Settings.")
    result.sections = {k: v.strip() for k, v in result.sections.items() if k in facts and v.strip()}
    return result
