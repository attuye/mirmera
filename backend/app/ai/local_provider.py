"""
Local model via Ollama: runs entirely on your machine, no API key, no billing,
no data leaving your laptop. Uses Ollama's structured-output mode (the
`format` parameter takes a JSON Schema) so the model is constrained to emit
JSON in exactly the shape we validate against.

Setup:
  1. Install Ollama: https://ollama.com/download
  2. Pull a model:    ollama pull qwen3:8b
  3. In backend/.env: DEFAULT_AI_PROVIDER=local
"""
import re

import httpx

from app.ai.base import LLMProvider, ProviderError, ProviderNotConfigured
from app.config import settings


class LocalProvider(LLMProvider):
    name = "local"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = settings.local_model_base_url.rstrip("/")
        self.model = settings.local_model_name
        self._transport = transport  # tests inject a fake server here

    async def complete(self, system, user, *, max_tokens=2000, schema=None, task=None) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": {"temperature": 0.4, "num_predict": max_tokens},
        }
        if schema:
            payload["format"] = schema  # Ollama constrains generation to this schema
        # Thinking models (qwen3, deepseek-r1…) reason out loud before answering. That
        # takes a minute or more per call and can use the whole num_predict budget,
        # leaving no answer at all. Mirmera asks for a structured reply, not reasoning.
        if not settings.local_model_think:
            payload["think"] = False

        async with httpx.AsyncClient(timeout=180, transport=self._transport) as client:
            try:
                response = await client.post(f"{self.base_url}/api/chat", json=payload)
                if response.status_code == 400 and "think" in response.text.lower() and "think" in payload:
                    # An older Ollama, or a model that has no thinking switch: ask again without it.
                    payload.pop("think")
                    response = await client.post(f"{self.base_url}/api/chat", json=payload)
            except httpx.ConnectError as err:
                raise ProviderNotConfigured(
                    f"Couldn't reach Ollama at {self.base_url}. Is it running? "
                    "Start it with `ollama serve` or open the Ollama app. If Mirmera runs on a server and "
                    "the model on your computer, start the tunnel there (scripts/qwen-tunnel)."
                ) from err
            except httpx.HTTPError as err:
                raise ProviderError(f"Ollama: {err}") from err

        if response.status_code == 404:
            raise ProviderNotConfigured(
                f"Ollama has no model '{self.model}'. Run: ollama pull {self.model}"
            )
        if response.status_code >= 400:
            raise ProviderError(f"Ollama {response.status_code}: {response.text[:300]}")
        content = response.json()["message"]["content"]
        # Older Ollama versions put the reasoning inline: drop it, keep the answer.
        return re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()