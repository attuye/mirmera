"""
The provider abstraction. Every model backend (Claude, OpenAI, a local model)
implements this same interface and returns the same typed contracts
(app.schemas.CharterSuggestion, DebriefDraft) — so nothing above this layer,
routers included, ever needs to know which model actually ran.

Add a new provider by subclassing AIProvider and registering it in
get_provider() below. Nothing else changes.
"""
from abc import ABC, abstractmethod

from app.schemas import CharterSuggestion, DebriefDraft


class ProductContext(dict):
    """Loosely-typed bag of context passed to a provider: artifact summaries,
    past charters, project name, etc. Kept as a dict (not a rigid schema) since
    what's useful to hand the model will grow over time."""


class AIProvider(ABC):
    @abstractmethod
    async def suggest_charters(self, context: ProductContext) -> list[CharterSuggestion]:
        ...

    @abstractmethod
    async def draft_debrief(self, session_data: dict) -> DebriefDraft:
        ...

    @abstractmethod
    async def summarize_artifact(self, raw_content: str, artifact_type: str) -> str:
        ...


def get_provider(name: str) -> AIProvider:
    """Resolve a provider by name. `name` comes from a product's
    default_ai_provider, or a per-request override — see routers/charters.py."""
    if name == "mock":
        from app.ai.mock_provider import MockProvider
        return MockProvider()
    if name == "claude":
        from app.ai.claude_provider import ClaudeProvider
        return ClaudeProvider()
    if name == "openai":
        from app.ai.openai_provider import OpenAIProvider
        return OpenAIProvider()
    if name == "local":
        from app.ai.local_provider import LocalProvider
        return LocalProvider()
    raise ValueError(f"Unknown AI provider: {name}")
