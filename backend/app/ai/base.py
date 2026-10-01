"""
The provider abstraction. Every model backend implements one method,
`complete()`, which takes instructions plus data and returns text. What to
ask for (charters, debriefs, summaries...) lives in app/ai/tasks.py, so a
new feature never needs touching every provider, and a new provider never
needs touching every feature.

Add a provider by subclassing LLMProvider and registering it in
get_provider() below.
"""
from abc import ABC, abstractmethod


class ProviderError(Exception):
    """The model call failed: network, rate limit, server error..."""


class ProviderNotConfigured(ProviderError):
    """A setting the provider needs (key, model, server URL) is missing or rejected."""


class UnknownProvider(ProviderError):
    """No provider is registered under this name."""


PROVIDER_NAMES = ("mock", "claude", "openai", "local")


class LLMProvider(ABC):
    name: str = "base"
    model: str = ""

    @abstractmethod
    async def complete(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 2000,
        schema: dict | None = None,
        task: str | None = None,
    ) -> str:
        """Return the model's text reply.

        schema: JSON Schema the reply should match. Providers that can enforce
                it (Ollama) do; the others rely on the instructions plus the
                validation and retry in app/ai/llm_json.py.
        task:   which feature is asking. Only the mock provider uses it, to
                return a realistic canned answer.
        """


def get_provider(name: str) -> LLMProvider:
    """Resolve a provider by name: a product's default_ai_provider,
    or a per-request override (see routers/charters.py)."""
    if name == "mock":
        from app.ai.mock_provider import MockProvider
        return MockProvider()
    if name == "claude":
        from app.ai.anthropic_provider import AnthropicProvider
        return AnthropicProvider()
    if name == "openai":
        from app.ai.openai_provider import OpenAIProvider
        return OpenAIProvider()
    if name == "local":
        from app.ai.local_provider import LocalProvider
        return LocalProvider()
    raise UnknownProvider(
        f"Unknown AI provider '{name}'. Use one of: {', '.join(PROVIDER_NAMES)}"
    )