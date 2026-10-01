# """
# Stub for a locally-hosted model (e.g. via Ollama at settings.local_model_base_url).
# Same shape as OpenAIProvider — implement when needed.
# """
# from app.ai.base import AIProvider, ProductContext
# from app.schemas import CharterSuggestion, DebriefDraft


# class LocalProvider(AIProvider):
#     async def suggest_charters(self, context: ProductContext) -> list[CharterSuggestion]:
#         raise NotImplementedError("Local provider not yet implemented — see claude_provider.py for the pattern")

#     async def draft_debrief(self, session_data: dict) -> DebriefDraft:
#         raise NotImplementedError("Local provider not yet implemented")

#     async def summarize_artifact(self, raw_content: str, artifact_type: str) -> str:
#         raise NotImplementedError("Local provider not yet implemented")
"""
Local model provider via Ollama — runs entirely on your machine, no API key,
no billing, no data leaving your laptop. Uses Ollama's structured-output mode
(the `format` parameter, JSON Schema) so the model is constrained to emit only
valid JSON matching our exact Pydantic schemas — no parsing failures.

Setup:
  1. Install Ollama: https://ollama.com/download
  2. Pull a model:    ollama pull qwen3:8b
  3. In backend/.env: DEFAULT_AI_PROVIDER=local
"""
import json

import httpx

from app.ai.base import AIProvider, ProductContext
from app.config import settings
from app.schemas import CharterSuggestion, DebriefDraft
from app.ai.claude_provider import _load_prompt  # reuses the same prompt templates


class LocalProvider(AIProvider):
    def __init__(self):
        self.base_url = settings.local_model_base_url.rstrip("/")
        self.model = settings.local_model_name

    async def _chat(self, prompt: str, schema: dict | None = None) -> str:
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {"temperature": 0.4},
        }
        if schema:
            payload["format"] = schema  # Ollama constrains generation to this JSON schema

        async with httpx.AsyncClient(timeout=180) as client:
            try:
                response = await client.post(f"{self.base_url}/api/chat", json=payload)
                response.raise_for_status()
            except httpx.ConnectError:
                raise RuntimeError(
                    f"Couldn't reach Ollama at {self.base_url} — is it running? "
                    f"Start it with `ollama serve` or open the Ollama app."
                )
            return response.json()["message"]["content"]

    async def suggest_charters(self, context: ProductContext) -> list[CharterSuggestion]:
        prompt = _load_prompt(
            "charter_suggestion.md",
            project_name=context.get("project_name", ""),
            artifact_context=context.get("artifact_context", ""),
            recent_charters=context.get("recent_charters", "(none)"),
        )
        schema = {"type": "array", "items": CharterSuggestion.model_json_schema()}
        raw = await self._chat(prompt, schema)
        items = json.loads(raw)
        return [CharterSuggestion(**item) for item in items]

    async def draft_debrief(self, session_data: dict) -> DebriefDraft:
        prompt = _load_prompt(
            "debrief_draft.md",
            charter_mission=session_data.get("charter_mission", ""),
            notes=session_data.get("notes_text", ""),
            bugs=session_data.get("bugs_text", "(none)"),
        )
        raw = await self._chat(prompt, DebriefDraft.model_json_schema())
        return DebriefDraft(**json.loads(raw))

    async def summarize_artifact(self, raw_content: str, artifact_type: str) -> str:
        prompt = (
            f"Summarize this {artifact_type} in 3-5 sentences for a tester "
            f"deciding what to explore. Focus on what changed, what's risky, "
            f"or what's ambiguous — not a restatement of the whole document.\n\n{raw_content}"
        )
        return await self._chat(prompt)