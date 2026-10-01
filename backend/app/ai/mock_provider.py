"""
A free, zero-latency provider for local development without API billing set up.
Returns realistic, deterministic responses shaped by whatever context is passed
in — not random lorem ipsum — so the rest of the app (charter review, session
flow, debrief editing) can be built and tested end-to-end before you spend a
cent on the real thing.

Swap it out for real: set DEFAULT_AI_PROVIDER=claude in backend/.env (or pass
provider="claude" per request) once billing is set up. Nothing else changes —
that's the point of the AIProvider interface.
"""
import re

from app.ai.base import AIProvider, ProductContext
from app.schemas import CharterSuggestion, DebriefDraft


def _first_line(text: str, fallback: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith(("+++", "---", "@@")):
            return line[:80]
    return fallback


class MockProvider(AIProvider):
    async def suggest_charters(self, context: ProductContext) -> list[CharterSuggestion]:
        artifact_text = context.get("artifact_context", "")
        project = context.get("project_name", "this product")
        hook = _first_line(artifact_text, project)

        # Pull out anything that looks like a changed function/file name to make
        # the mock feel grounded in what was actually pasted, not generic filler.
        touched = re.findall(r"(?:def |class |\+\+\+ b/|@@.*)([a-zA-Z_][\w./]*)", artifact_text)
        focus = touched[0] if touched else "the changed area"

        return [
            CharterSuggestion(
                mission_statement=f"Explore {focus} for behavior changes implied by: {hook}",
                rationale=(
                    "Mock suggestion — stands in for the AI call so you can build and test "
                    "the charter review flow without API billing set up. Real suggestions "
                    "will read the full artifact content and reference specific risk patterns."
                ),
                risk_areas=["needs review"],
                priority="high",
            ),
            CharterSuggestion(
                mission_statement=f"Regression-check areas adjacent to {focus}",
                rationale="Mock suggestion — swap to the claude provider for real, context-grounded rationale.",
                risk_areas=["regression"],
                priority="medium",
            ),
            CharterSuggestion(
                mission_statement=f"Probe edge cases and boundary conditions around {focus}",
                rationale="Mock suggestion — placeholder priority/rationale until a real provider is wired in.",
                risk_areas=["boundary value"],
                priority="medium",
            ),
        ]

    async def draft_debrief(self, session_data: dict) -> DebriefDraft:
        charter = session_data.get("charter_mission", "the session charter")
        notes_text = session_data.get("notes_text", "")
        note_count = len([l for l in notes_text.splitlines() if l.strip()])
        bugs_text = session_data.get("bugs_text", "(none)")

        summary = (
            f"Mock debrief for charter: \"{charter}\". "
            f"{note_count} note(s) logged this session. Bugs found: {bugs_text}. "
            "This is a placeholder summary — switch DEFAULT_AI_PROVIDER to 'claude' "
            "once billing is set up for a real synthesized narrative."
        )
        return DebriefDraft(
            coverage_summary=summary,
            open_questions=["(mock provider — no AI-extracted open questions yet)"],
        )

    async def summarize_artifact(self, raw_content: str, artifact_type: str) -> str:
        first = _first_line(raw_content, "(empty artifact)")
        return f"Mock summary of this {artifact_type}: starts with \u201c{first}\u201d ({len(raw_content)} chars total)."
