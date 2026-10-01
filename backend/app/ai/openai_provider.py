"""
Stub — same interface as ClaudeProvider, not yet wired to a real API call.
Fill in _complete() with an OpenAI chat.completions call when you're ready
to add this as a second option; everything else (prompts, JSON contracts,
routers) stays unchanged.
"""
from app.ai.base import AIProvider, ProductContext
from app.schemas import CharterSuggestion, DebriefDraft


class OpenAIProvider(AIProvider):
    async def suggest_charters(self, context: ProductContext) -> list[CharterSuggestion]:
        raise NotImplementedError("OpenAI provider not yet implemented — see claude_provider.py for the pattern")

    async def draft_debrief(self, session_data: dict) -> DebriefDraft:
        raise NotImplementedError("OpenAI provider not yet implemented")

    async def summarize_artifact(self, raw_content: str, artifact_type: str) -> str:
        raise NotImplementedError("OpenAI provider not yet implemented")
