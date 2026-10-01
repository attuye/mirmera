"""Upgrading a database created before the risk model existed."""
import sqlite3

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import Base, add_missing_columns


def make_old_database(path) -> None:
    """The products and charters tables as they were before this step, with data."""
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE products (
                id VARCHAR PRIMARY KEY, org_id VARCHAR NOT NULL, name VARCHAR NOT NULL,
                created_at DATETIME, default_ai_provider VARCHAR
            );
            CREATE TABLE artifacts (
                id VARCHAR PRIMARY KEY, product_id VARCHAR NOT NULL, type VARCHAR NOT NULL,
                label VARCHAR NOT NULL, raw_content TEXT, file_ref VARCHAR, ai_summary TEXT, created_at DATETIME
            );
            CREATE TABLE charters (
                id VARCHAR PRIMARY KEY, product_id VARCHAR NOT NULL, mission_statement TEXT NOT NULL,
                rationale TEXT, risk_areas JSON, priority VARCHAR, source VARCHAR,
                source_artifact_id VARCHAR, status VARCHAR, created_at DATETIME
            );
            INSERT INTO products VALUES ('p1', 'org', 'AddisVerify KYC', '2026-09-30 10:00:00', 'mock');
            INSERT INTO charters VALUES ('c1', 'p1', 'Explore ID upload', NULL, '["upload"]',
                                         'high', 'manual', NULL, 'done', '2026-09-30 10:05:00');
        """)


def test_old_database_is_upgraded_in_place(tmp_path):
    path = tmp_path / "old.db"
    make_old_database(path)
    engine = create_engine(f"sqlite:///{path}")

    Base.metadata.create_all(bind=engine)
    added = add_missing_columns(engine)

    assert set(added) == {"products.testing_mission", "charters.test_ideas", "charters.oracles"}
    tables = set(inspect(engine).get_table_names())
    assert {"risks", "product_models", "charter_risks"} <= tables

    with Session(engine) as db:
        charter = db.get(models.Charter, "c1")
        out = schemas.CharterOut.model_validate(charter)
        assert out.mission_statement == "Explore ID upload"  # existing data kept
        assert (out.risk_ids, out.test_ideas, out.oracles) == ([], [], [])
        assert db.get(models.Product, "p1").testing_mission is None


def test_running_the_upgrade_again_changes_nothing(tmp_path):
    path = tmp_path / "old.db"
    make_old_database(path)
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(bind=engine)
    add_missing_columns(engine)
    assert add_missing_columns(engine) == []
