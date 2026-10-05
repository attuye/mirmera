"""backend/.env, the SQLite database and the evidence folder are found in backend/, whichever
folder the server is started from."""
from pathlib import Path

from app.config import BACKEND_DIR, Settings, in_backend


def test_env_file_is_the_backend_one():
    assert Path(Settings.model_config["env_file"]) == BACKEND_DIR / ".env"


def test_relative_paths_are_taken_inside_backend(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # started from somewhere else, say the repo root
    s = Settings(database_url="sqlite:///./mirmera.db", evidence_dir="./evidence", _env_file=None)
    assert s.database_url == "sqlite:///" + (BACKEND_DIR / "mirmera.db").as_posix()
    assert s.evidence_dir == str(BACKEND_DIR / "evidence")
    assert not (tmp_path / "mirmera.db").exists()


def test_absolute_paths_and_other_databases_are_kept(tmp_path):
    db = (tmp_path / "e2e.db").as_posix()
    s = Settings(database_url=f"sqlite:///{db}", evidence_dir=str(tmp_path / "ev"), _env_file=None)
    assert s.database_url == f"sqlite:///{db}" and s.evidence_dir == str(tmp_path / "ev")
    assert Settings(database_url="sqlite:///:memory:", _env_file=None).database_url == "sqlite:///:memory:"
    pg = "postgresql://mirmera@localhost/mirmera"
    assert Settings(database_url=pg, _env_file=None).database_url == pg
    assert in_backend("../evidence") == str((BACKEND_DIR / ".." / "evidence").resolve())
