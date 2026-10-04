"""Claude via the Anthropic API."""
import anthropic
from anthropic import AsyncAnthropic

from app.ai.base import LLMProvider, ProviderError, ProviderNotConfigured
from app.config import settings


class AnthropicProvider(LLMProvider):
    name = "claude"

    def __init__(self, api_key: str | None):
        if not api_key:
            raise ProviderNotConfigured("Add a Claude API key under Workspace keys (Account → Keys), "
                                        "or ANTHROPIC_API_KEY in backend/.env, to use the claude provider")
        self.model = settings.anthropic_model
        self.client = AsyncAnthropic(api_key=api_key)

    async def complete(self, system, user, *, max_tokens=2000, schema=None, task=None) -> str:
        try:
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        except anthropic.AuthenticationError as err:
            raise ProviderNotConfigured(f"Anthropic rejected the Claude API key: {err}") from err
        except anthropic.APIError as err:
            raise ProviderError(f"Anthropic: {err}") from err
        return "".join(block.text for block in response.content if block.type == "text")