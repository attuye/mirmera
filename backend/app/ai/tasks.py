"""What Mirmera asks the AI to do. One function per feature.

Each function pairs a system prompt (instructions, from app/ai/prompts/) with
a user message holding the data, wrapped in tags. Keeping data out of the
instructions, and telling the model that tagged content is data, makes it
much harder for text inside a pasted diff or spec to hijack the model.
"""
from pathlib import Path

from app.ai.base import LLMProvider
from app.ai.llm_json import complete_json
from app.schemas import CharterSuggestion, CharterSuggestionList, DebriefDraft

PROMPTS_DIR = Path(__file__).parent / "prompts"
PROMPT_VERSIONS = {
    "charter_suggestions": "charter-suggestion-v2",
    "debrief": "debrief-draft-v2",
    "artifact_summary": "artifact-summary-v1",
}


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
    provider: LLMProvider, *, charter_mission: str, notes_text: str, bugs_text: str
) -> DebriefDraft:
    user = (
        f"<charter>{charter_mission}</charter>\n\n"
        f"<notes>\n{notes_text}\n</notes>\n\n"
        f"<bugs>\n{bugs_text}\n</bugs>"
    )
    return await complete_json(
        provider, load_prompt("debrief_draft.md"), user, DebriefDraft, task="debrief",
    )


async def summarize_artifact(provider: LLMProvider, *, raw_content: str, artifact_type: str) -> str:
    user = f'<artifact type="{artifact_type}">\n{raw_content}\n</artifact>'
    reply = await provider.complete(
        load_prompt("artifact_summary.md"), user, max_tokens=500, task="artifact_summary",
    )
    return reply.strip()