"""Recorded-browser evidence as plain text for AI prompts.

Everything here comes from the app under test (console messages, URLs), so it
can contain any text. Callers put it inside a <browser> tag, and the prompts
treat that tag as data, never instructions. Lines are already cut to the
runner's TEXT_LIMIT when they are recorded.
"""
from datetime import datetime

from app import models

PROBLEM_KINDS = ("console_error", "page_error", "failed_request")
LABELS = {"console_error": "console error", "page_error": "page error",
          "failed_request": "failed request", "navigation": "page"}
NOT_USED = "(the recorded browser was not used)"


def _defang(text: str | None) -> str:
    """Angle brackets become look-alikes, so a console message like
    '</browser> new instructions' cannot close the data tag it sits in."""
    return (text or "").replace("<", "‹").replace(">", "›")


def signal_line(e: models.Evidence, start: datetime) -> str:
    """'+3 min [console error ×2] Upload widget failed to init'"""
    minute = max(0, int((e.captured_at - start).total_seconds() // 60))
    repeat = f" ×{e.count}" if (e.count or 1) > 1 else ""
    text = e.url if e.kind == "navigation" else e.summary
    return f"+{minute} min [{LABELS.get(e.kind, e.kind)}{repeat}] {_defang(text)}"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def browser_context(rows: list[models.Evidence], start: datetime, *, used: bool,
                    max_problems: int = 25, max_pages: int = 15) -> str:
    """Summary line, then the problems (most repeated first) and the pages visited.

    used: whether the recorded browser ran at all, so "nothing recorded" and
    "not used" read differently (the first is a finding, the second isn't).
    """
    if not used:
        return NOT_USED
    problems = [e for e in rows if e.kind in PROBLEM_KINDS]
    pages = [e for e in rows if e.kind == "navigation"]
    shots = [e for e in rows if e.kind == "screenshot"]

    totals = {k: sum(e.count or 1 for e in problems if e.kind == k) for k in PROBLEM_KINDS}
    parts = [_plural(n, LABELS[k]) for k, n in totals.items() if n]
    summary = "Recorded: " + (", ".join(parts) if parts else "no console errors, page errors or failed requests")
    summary += f"; {_plural(len(pages), 'page')} visited"
    if shots:
        summary += f"; {_plural(len(shots), 'screenshot')}"
    lines = [summary + "."]

    if problems:
        # Most repeated first, then in time order; the rest are counted, not dropped silently.
        ranked = sorted(problems, key=lambda e: (-(e.count or 1), e.captured_at))[:max_problems]
        lines.append("Problems the page reported:")
        lines += [signal_line(e, start) for e in sorted(ranked, key=lambda e: e.captured_at)]
        if len(problems) > max_problems:
            lines.append(f"(and {len(problems) - max_problems} more, less frequent)")
    if pages:
        lines.append("Pages visited:")
        lines += [signal_line(e, start) for e in pages[-max_pages:]]
        if len(pages) > max_pages:
            lines.insert(lines.index("Pages visited:") + 1, f"(first {len(pages) - max_pages} not shown)")
    return "\n".join(lines)
