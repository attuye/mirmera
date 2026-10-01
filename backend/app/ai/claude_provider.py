import json
from pathlib import Path

from anthropic import AsyncAnthropic

from app.ai.base import AIProvider, ProductContext
from app.config import settings
from app.schemas import CharterSuggestion, DebriefDraft

PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load_prompt(name: str, **kwargs) -> str:
    text = (PROMPTS_DIR / name).read_text()
    for key, value in kwargs.items():
        text = text.replace(f"{{{{{key}}}}}", str(value))
    return text


def _extract_json(text: str):
    """Model output should be pure JSON per the prompt, but strip code-fence
    wrapping defensively in case a model adds one anyway."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text)


class ClaudeProvider(AIProvider):
    def __init__(self):
        self.client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        self.model = "claude-sonnet-4-6"

    async def _complete(self, prompt: str) -> str:
        response = await self.client.messages.create(
            model=self.model,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(block.text for block in response.content if block.type == "text")

    async def suggest_charters(self, context: ProductContext) -> list[CharterSuggestion]:
        prompt = _load_prompt(
            "charter_suggestion.md",
            project_name=context.get("project_name", ""),
            artifact_context=context.get("artifact_context", ""),
            recent_charters=context.get("recent_charters", "(none)"),
        )
        raw = await self._complete(prompt)
        items = _extract_json(raw)
        return [CharterSuggestion(**item) for item in items]

    async def draft_debrief(self, session_data: dict) -> DebriefDraft:
        prompt = _load_prompt(
            "debrief_draft.md",
            charter_mission=session_data.get("charter_mission", ""),
            notes=session_data.get("notes_text", ""),
            bugs=session_data.get("bugs_text", "(none)"),
        )
        raw = await self._complete(prompt)
        data = _extract_json(raw)
        return DebriefDraft(**data)

    async def summarize_artifact(self, raw_content: str, artifact_type: str) -> str:
        prompt = (
            f"Summarize this {artifact_type} in 3-5 sentences for a tester "
            f"deciding what to explore. Focus on what changed, what's risky, "
            f"or what's ambiguous — not a restatement of the whole document.\n\n{raw_content}"
        )
        return await self._complete(prompt)
