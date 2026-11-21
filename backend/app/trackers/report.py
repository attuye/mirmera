"""The issue body, built once from the tester's reviewed report and rendered
as Markdown (GitHub, and the preview) or as Atlassian Document Format (Jira),
so both trackers carry the same content."""
from dataclasses import dataclass, field
from typing import Literal

from app import models
from app.config import settings
from app.evidence_text import LABELS, PROBLEM_KINDS

ORACLE_NAMES = {
    "claims": "Claims", "user_expectations": "User expectations", "product": "Product (consistency within the product)",
    "comparable_products": "Comparable products", "history": "History", "image": "Image",
    "familiarity": "Familiar problems", "explainability": "Explainability", "world": "World",
    "statutes": "Statutes and standards", "purpose": "Purpose",
}


@dataclass
class Block:
    kind: Literal["heading", "paragraph", "numbered", "bullets", "code"]
    text: str = ""
    items: list[str] = field(default_factory=list)


def _signal(e: models.Evidence) -> str:
    repeat = f" x{e.count}" if (e.count or 1) > 1 else ""
    text = e.url if e.kind == "navigation" else e.summary
    return f"{e.captured_at:%H:%M:%S} UTC [{LABELS.get(e.kind, e.kind)}{repeat}] {text}"


def build_report(bug: models.Bug, evidence: list[models.Evidence], *, attaches_files: bool,
                 note: str | None = None, occurrence: bool = False) -> list[Block]:
    """occurrence: the report is a comment adding a new occurrence to an existing issue."""
    b: list[Block] = []
    if occurrence:
        b.append(Block("paragraph", "Another occurrence, found in an exploratory testing session."))
    if note and note.strip():
        b.append(Block("paragraph", f"Tester's note: {note.strip()}"))
    if bug.kind == "question":
        b.append(Block("paragraph", "This is a question, not a confirmed bug: the tester is not sure the behaviour is wrong."))
    if bug.description:
        b.append(Block("paragraph", bug.description))

    if bug.steps_to_reproduce:
        b += [Block("heading", "Steps to reproduce"), Block("numbered", items=list(bug.steps_to_reproduce))]
    if bug.expected:
        b += [Block("heading", "Expected"), Block("paragraph", bug.expected)]
    if bug.actual:
        b += [Block("heading", "Actual"), Block("paragraph", bug.actual)]
    if bug.reproduce:
        b += [Block("heading", "Reproduce without the UI"),
              Block("paragraph", "The failing request as the browser sent it. Cookies and secrets are masked: use your own session."),
              Block("code", bug.reproduce)]
    if bug.oracle_heuristic:
        name = ORACLE_NAMES.get(bug.oracle_heuristic, bug.oracle_heuristic)
        b += [Block("heading", "Why it's a problem"),
              Block("paragraph", f"Oracle: {name}. {bug.oracle_explanation or ''}".strip())]
    severity = f"Severity: {bug.severity}" + (f". {bug.severity_rationale}" if bug.severity_rationale else "")
    b += [Block("heading", "Severity"), Block("paragraph", severity)]
    if bug.reproducibility:
        b.append(Block("paragraph", f"Reproducibility: {bug.reproducibility}"))
    if bug.open_questions:
        b += [Block("heading", "Open questions"), Block("bullets", items=list(bug.open_questions))]
    if bug.environment:
        b += [Block("heading", "Environment"), Block("bullets", items=[f"{k}: {v}" for k, v in bug.environment.items()])]

    signals = [e for e in evidence if e.kind in PROBLEM_KINDS or e.kind == "navigation"]
    shots = [e for e in evidence if e.kind == "screenshot" and e.file_path]
    if signals or shots:
        b.append(Block("heading", "Recorded in the browser"))
        if signals:
            b.append(Block("paragraph", "What the page reported in the two minutes before the bug was logged:"))
            b.append(Block("code", "\n".join(_signal(e) for e in signals)))
        if shots:
            where = "attached to this issue" if attaches_files else "kept in Mirmera (this tracker's API cannot receive attachments)"
            b.append(Block("paragraph", f"{len(shots)} screenshot{'s' if len(shots) > 1 else ''} taken when the bug was logged: {where}."))

    footer = f"Found in an exploratory testing session. Charter: {bug.charter.mission_statement}"
    if settings.public_app_url:
        footer += f". Full evidence: {settings.public_app_url.rstrip('/')}/bugs"
    from app.trackers.fingerprint import fingerprint
    b.append(Block("paragraph", footer + f". Filed from Mirmera. Mirmera fingerprint: {fingerprint(bug)}"))
    return b


# ---------- renderers ----------

def to_markdown(blocks: list[Block]) -> str:
    out: list[str] = []
    for blk in blocks:
        if blk.kind == "heading":
            out.append(f"### {blk.text}")
        elif blk.kind == "paragraph":
            out.append(blk.text)
        elif blk.kind == "numbered":
            out.append("\n".join(f"{i}. {item}" for i, item in enumerate(blk.items, 1)))
        elif blk.kind == "bullets":
            out.append("\n".join(f"- {item}" for item in blk.items))
        elif blk.kind == "code":
            fence = "~~~~" if "```" in blk.text else "```"  # page text could contain a fence
            out.append(f"{fence}text\n{blk.text}\n{fence}")
    return "\n\n".join(out)


def _text(t: str) -> list[dict]:
    return [{"type": "text", "text": t}] if t else []


def _para(t: str) -> dict:
    return {"type": "paragraph", "content": _text(t)}


def to_adf(blocks: list[Block]) -> dict:
    """Atlassian Document Format, the rich-text JSON that Jira Cloud's v3 API takes."""
    content: list[dict] = []
    for blk in blocks:
        if blk.kind == "heading":
            content.append({"type": "heading", "attrs": {"level": 3}, "content": _text(blk.text)})
        elif blk.kind == "paragraph":
            content.append(_para(blk.text))
        elif blk.kind in ("numbered", "bullets"):
            content.append({
                "type": "orderedList" if blk.kind == "numbered" else "bulletList",
                "content": [{"type": "listItem", "content": [_para(item)]} for item in blk.items if item],
            })
        elif blk.kind == "code":
            content.append({"type": "codeBlock", "attrs": {"language": "text"}, "content": _text(blk.text)})
    return {"type": "doc", "version": 1, "content": content}


def adf_text(node) -> str:
    """Plain text of an ADF document (Jira search results), for snippets."""
    if isinstance(node, dict):
        if node.get("type") == "text":
            return node.get("text", "")
        sep = "\n" if node.get("type") in ("paragraph", "heading", "listItem", "codeBlock") else ""
        return "".join(adf_text(c) for c in node.get("content", [])) + sep
    if isinstance(node, list):
        return "".join(adf_text(c) for c in node)
    return str(node or "")
