"""OpenAI, or any server that speaks the OpenAI chat API (LM Studio, vLLM...).

For LM Studio, set OPENAI_BASE_URL=http://localhost:1234/v1 and any non-empty
OPENAI_API_KEY.
"""
import openai
from openai import AsyncOpenAI

from app.ai.base import LLMProvider, ProviderError, ProviderNotConfigured
from app.config import settings


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, api_key: str | None, base_url: str | None = None):
        if not api_key:
            raise ProviderNotConfigured("Add an OpenAI API key under Workspace keys (Account → Keys), "
                                        "or OPENAI_API_KEY in backend/.env, to use the openai provider")
        self.model = settings.openai_model
        # An empty base URL means "use OpenAI itself".
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url or None)

    async def complete(self, system, user, *, max_tokens=2000, schema=None, task=None) -> str:
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
        except openai.AuthenticationError as err:
            raise ProviderNotConfigured(f"The server rejected OPENAI_API_KEY: {err}") from err
        except openai.OpenAIError as err:
            raise ProviderError(f"OpenAI: {err}") from err
        return response.choices[0].message.content or ""