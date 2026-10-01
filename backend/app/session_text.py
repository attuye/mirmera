"""A session's notes and bugs as plain text lines for AI prompts.

Shared by the debrief and the risk-update suggestions so both describe a
session the same way, and so the AI's quoted evidence matches what the tester
sees.
"""
from app import models


def note_line(note: models.Note, start) -> str:
    """'+12 min [test] Applied SAVE10'"""
    minute = max(0, int((note.timestamp - start).total_seconds() // 60))
    return f"+{minute} min [{note.tag}] {note.content}"


def bug_line(bug: models.Bug) -> str:
    """'- [bug, high, oracle: claims] Discount lost'; unreviewed bugs say so."""
    kind = bug.kind or "unreviewed"
    oracle = f", oracle: {bug.oracle_heuristic}" if bug.oracle_heuristic else ""
    return f"- [{kind}, {bug.severity}{oracle}] {bug.title}"
