"""Evidence paths after moving a database from Windows to a server."""
import sqlite3

from app.move_evidence import moved, run


def test_paths_move_under_the_new_folder():
    assert moved(r"E:\addis-verify-projects\mirmera\backend\evidence\s1\shot-2.png", "/data/evidence") == "/data/evidence/s1/shot-2.png"
    assert moved("/home/attu/mirmera/backend/evidence/s1/repro/final.png", "/data/evidence") == "/data/evidence/s1/repro/final.png"
    # the last "evidence" folder counts (a project folder may be called evidence too)
    assert moved(r"C:\evidence\mirmera\backend\evidence\s2\a.png", "/data/evidence") == "/data/evidence/s2/a.png"
    assert moved("/data/evidence/s1/a.png", "/data/evidence") is None  # already there
    assert moved(r"D:\shots\a.png", "/data/evidence") is None and moved(None, "/data/evidence") is None


def test_the_database_is_changed_only_with_apply(tmp_path):
    db = tmp_path / "m.db"
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE evidence (id TEXT, file_path TEXT)")
        con.execute("CREATE TABLE reproduction_runs (id TEXT, screenshot_path TEXT)")
        con.execute(r"INSERT INTO evidence VALUES ('e1', 'E:\m\backend\evidence\s1\a.png'), ('e2', NULL)")
        con.execute(r"INSERT INTO reproduction_runs VALUES ('r1', 'E:\m\backend\evidence\s1\r1\final.png')")
    root = str(tmp_path / "ev")
    assert len(run(str(db), root, apply=False)) == 2
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT file_path FROM evidence WHERE id='e1'").fetchone()[0].startswith("E:")
    run(str(db), root, apply=True)
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT file_path FROM evidence WHERE id='e1'").fetchone()[0] == f"{root}/s1/a.png"
        assert con.execute("SELECT screenshot_path FROM reproduction_runs").fetchone()[0] == f"{root}/s1/r1/final.png"
    assert run(str(db), root, apply=True) == []  # nothing left to move
