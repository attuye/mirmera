"""
A free, zero-latency provider for development and tests, no API key needed.
It returns realistic, deterministic answers shaped by whatever was sent, not
random lorem ipsum, so the whole app (charter review, session flow, debrief
editing) can be built and tested end to end before spending anything.

Switch to a real model by setting DEFAULT_AI_PROVIDER in backend/.env
(claude, openai or local). Nothing else changes.
"""
import json
import re

from app.ai.base import LLMProvider


def _between(text: str, tag: str) -> str:
    match = re.search(rf"<{tag}[^>]*>\n?(.*?)\n?</{tag}>", text, re.S)
    return match.group(1).strip() if match else ""


def _first_line(text: str, fallback: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith(("+++", "---", "@@", "[")):
            return line[:80]
    return fallback


class MockProvider(LLMProvider):
    name = "mock"
    model = "mock"

    async def complete(self, system, user, *, max_tokens=2000, schema=None, task=None) -> str:
        if task == "charter_suggestions":
            return json.dumps(self._charters(user))
        if task == "debrief":
            return json.dumps(self._debrief(user))
        if task == "artifact_summary":
            return self._summary(user)
        return json.dumps({"note": f"mock provider has no canned answer for task '{task}'"})

    @staticmethod
    def _charters(user: str) -> dict:
        artifacts = _between(user, "artifacts")
        project = _between(user, "project") or "this product"
        hook = _first_line(artifacts, project)
        # Anything that looks like a changed function/file name keeps the mock
        # grounded in what was actually pasted, not generic filler.
        touched = re.findall(r"(?:def |class |\+\+\+ b/|@@.*)([a-zA-Z_][\w./]*)", artifacts)
        focus = touched[0] if touched else "the changed area"
        return {"suggestions": [
            {
                "mission_statement": f"Explore {focus} for behavior changes implied by: {hook}",
                "rationale": (
                    "Mock suggestion: stands in for the AI call so you can build and test "
                    "the charter review flow without API billing. Real suggestions read the "
                    "full artifact content and reference specific risk patterns."
                ),
                "risk_areas": ["needs review"],
                "priority": "high",
            },
            {
                "mission_statement": f"Regression-check areas adjacent to {focus}",
                "rationale": "Mock suggestion: switch to a real provider for context-grounded rationale.",
                "risk_areas": ["regression"],
                "priority": "medium",
            },
            {
                "mission_statement": f"Probe edge cases and boundary conditions around {focus}",
                "rationale": "Mock suggestion: placeholder priority and rationale until a real provider is used.",
                "risk_areas": ["boundary value"],
                "priority": "medium",
            },
        ]}

    @staticmethod
    def _debrief(user: str) -> dict:
        charter = _between(user, "charter") or "the session charter"
        notes = [line for line in _between(user, "notes").splitlines() if line.strip()]
        note_count = 0 if notes == ["(no notes logged)"] else len(notes)
        bugs = _between(user, "bugs") or "(none)"
        return {
            "coverage_summary": (
                f'Mock debrief for charter: "{charter}". '
                f"{note_count} note(s) logged this session. Bugs found: {bugs}. "
                "This is a placeholder summary; switch DEFAULT_AI_PROVIDER to a real "
                "provider for a synthesized narrative."
            ),
            "open_questions": ["(mock provider: no AI-extracted open questions yet)"],
        }

    @staticmethod
    def _summary(user: str) -> str:
        match = re.search(r'<artifact type="([^"]+)">\n?(.*?)\n?</artifact>', user, re.S)
        kind, content = (match.group(1), match.group(2)) if match else ("artifact", user)
        first = _first_line(content, "(empty artifact)")
        return f"Mock summary of this {kind}: starts with \u201c{first}\u201d ({len(content)} chars total)."