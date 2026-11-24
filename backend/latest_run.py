"""Writes the latest exploration run as plain text, to read or share: its steps with what the app kept in
the browser, the session's notes (curios included), the bug drafts and the agent's track record.

    cd backend
    python latest_run.py > latest_run.txt          (the latest run)
    python latest_run.py <run id> > latest_run.txt (a given run)

It only reads the database. Values that look like tokens or personal details were masked when stored.
"""
import sys

from app import models
from app.database import SessionLocal


def main(run_id: str | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):  # Windows: "•" and Ge'ez text into a file, not the console's code page
        sys.stdout.reconfigure(encoding="utf-8")
    with SessionLocal() as db:
        q = db.query(models.ExplorationRun)
        run = db.get(models.ExplorationRun, run_id) if run_id else q.order_by(models.ExplorationRun.created_at.desc()).first()
        if not run:
            print("No exploration run found.")
            return
        charter = db.get(models.Charter, run.charter_id)
        print(f"Charter: {charter.mission_statement if charter else run.charter_id}")
        print(f"Run: {run.id} ({run.created_at:%Y-%m-%d %H:%M} UTC), status: {run.status}: {run.detail or ''}".rstrip(": "))
        print(f"Summary: {run.summary or '(none)'}")
        print()
        for s in run.steps or []:
            by = " (by Mirmera)" if s.get("by") == "Mirmera" else ""
            detail = f": {s['detail']}" if s.get("detail") else ""
            print(f"{s['n']}. [{s.get('kind')}] {s.get('action')} -> {s.get('status')}{detail}{by}")
            print(f"   thought: {s.get('thought') or '(no reason given)'}"[:220])
            if s.get("storage"):
                print(f"   storage: {' · '.join(s['storage'])}")
        if run.session_id:
            notes = (db.query(models.Note).filter(models.Note.session_id == run.session_id)
                     .order_by(models.Note.timestamp).all())
            curios = [n for n in notes if n.content.startswith("Curio: ")]
            print(f"\nCurios ({len(curios)}):")
            for n in curios:
                print(f"- {n.content}")
            record = [n for n in notes if n.content.startswith("The agent's drafts in this project")]
            if record:
                print(f"\nTrack record: {record[-1].content}")
        print("\nBug drafts:")
        for bid in run.bug_ids or []:
            b = db.get(models.Bug, bid)
            if b:
                judged = (b.environment or {}).get("judged_by") or "?"
                conf = b.confirmation or {}
                replay = f", replayed {conf['reproduced']} of {conf['runs']}" if conf.get("runs") else ""
                print(f"- [{b.severity}] {b.title} (judged by: {judged}{replay})")
                if b.actual:
                    print(f"  actual: {b.actual[:300]}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
