"""Writes the latest exploration run as plain text, to read or share: its steps with what the app kept in
the browser, the session's notes (curios included), the bug drafts and the agent's track record.

    cd backend
    python latest_run.py            writes latest_run.txt here (the latest run)
    python latest_run.py <run id>   the same, for a given run

It only reads the database. Values that look like tokens or personal details were masked when stored.
"""
import sys
from pathlib import Path

from app import models
from app.database import SessionLocal


OUT = Path(__file__).resolve().parent / "latest_run.txt"


def text(run_id: str | None = None) -> str:
    """The run as plain text ("" when there is none)."""
    lines: list[str] = []
    p = lambda line="": lines.append(str(line))  # noqa: E731
    with SessionLocal() as db:
        q = db.query(models.ExplorationRun)
        run = db.get(models.ExplorationRun, run_id) if run_id else q.order_by(models.ExplorationRun.created_at.desc()).first()
        if not run:
            return ""
        charter = db.get(models.Charter, run.charter_id)
        p(f"Charter: {charter.mission_statement if charter else run.charter_id}")
        p(f"Run: {run.id} ({run.created_at:%Y-%m-%d %H:%M} UTC), status: {run.status}: {run.detail or ''}".rstrip(": "))
        p(f"Summary: {run.summary or '(none)'}")
        p()
        for s in run.steps or []:
            by = " (by Mirmera)" if s.get("by") == "Mirmera" else ""
            detail = f": {s['detail']}" if s.get("detail") else ""
            p(f"{s['n']}. [{s.get('kind')}] {s.get('action')} -> {s.get('status')}{detail}{by}")
            p(f"   thought: {s.get('thought') or '(no reason given)'}"[:220])
            if s.get("storage"):
                p(f"   storage: {' · '.join(s['storage'])}")
        if run.session_id:
            notes = (db.query(models.Note).filter(models.Note.session_id == run.session_id)
                     .order_by(models.Note.timestamp).all())
            curios = [n for n in notes if n.content.startswith("Curio: ")]
            p(f"\nCurios ({len(curios)}):")
            for n in curios:
                p(f"- {n.content}")
            record = [n for n in notes if n.content.startswith("The agent's drafts in this project")]
            if record:
                p(f"\nTrack record: {record[-1].content}")
        p("\nBug drafts:")
        for bid in run.bug_ids or []:
            b = db.get(models.Bug, bid)
            if b:
                judged = (b.environment or {}).get("judged_by") or "?"
                conf = b.confirmation or {}
                replay = f", replayed {conf['reproduced']} of {conf['runs']}" if conf.get("runs") else ""
                p(f"- [{b.severity}] {b.title} (judged by: {judged}{replay})")
                if b.actual:
                    p(f"  actual: {b.actual}")

    return "\n".join(lines) + "\n"


def main(run_id: str | None = None, out: Path = OUT) -> Path | None:
    """Writes the run to latest_run.txt (UTF-8, so Ge'ez text and "•" survive on Windows) and says where."""
    written = text(run_id)
    if not written:
        print("No exploration run found.")
        return None
    out.write_text(written, encoding="utf-8")
    steps = written.count("\n   thought: ")
    print(f"Wrote {out} ({steps} steps). Open it, or attach it to share the run.")
    return out


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
