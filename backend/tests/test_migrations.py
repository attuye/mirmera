"""Upgrading databases made by earlier steps, keeping their data."""
import sqlite3

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import Base, add_missing_columns, apply_renames

NEW_PROJECT_COLUMNS = {"projects.description", "projects.app_url", "projects.environments", "projects.default_tester",
                       "projects.archived_at"}


def upgrade(engine) -> tuple[list[str], list[str]]:
    """What the app does on start."""
    renamed = apply_renames(engine)
    Base.metadata.create_all(bind=engine)
    return renamed, add_missing_columns(engine)


def make_old_database(path) -> None:
    """The products and charters tables as they were before the risk model, with data."""
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE products (
                id VARCHAR PRIMARY KEY, org_id VARCHAR NOT NULL, name VARCHAR NOT NULL,
                created_at DATETIME, default_ai_provider VARCHAR
            );
            CREATE TABLE artifacts (
                id VARCHAR PRIMARY KEY, product_id VARCHAR NOT NULL REFERENCES products(id), type VARCHAR NOT NULL,
                label VARCHAR NOT NULL, raw_content TEXT, file_ref VARCHAR, ai_summary TEXT, created_at DATETIME
            );
            CREATE TABLE charters (
                id VARCHAR PRIMARY KEY, product_id VARCHAR NOT NULL REFERENCES products(id), mission_statement TEXT NOT NULL,
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

    renamed, added = upgrade(engine)

    assert renamed == ["products -> projects", "artifacts.product_id -> artifacts.project_id",
                       "charters.product_id -> charters.project_id"]
    assert set(added) == {"projects.testing_mission", "projects.github_repo", "projects.jira_project_key",
                          "charters.test_ideas", "charters.oracles"} | NEW_PROJECT_COLUMNS
    tables = set(inspect(engine).get_table_names())
    assert {"projects", "risks", "product_models", "charter_risks"} <= tables
    assert "products" not in tables

    with Session(engine) as db:
        charter = db.get(models.Charter, "c1")
        out = schemas.CharterOut.model_validate(charter)
        assert out.mission_statement == "Explore ID upload"  # existing data kept
        assert out.project_id == "p1"
        assert (out.risk_ids, out.test_ideas, out.oracles) == ([], [], [])
        project = db.get(models.Project, "p1")
        assert (project.name, project.testing_mission, project.app_url) == ("AddisVerify KYC", None, None)
        assert [c.id for c in project.charters] == ["c1"]


def test_running_the_upgrade_again_changes_nothing(tmp_path):
    path = tmp_path / "old.db"
    make_old_database(path)
    engine = create_engine(f"sqlite:///{path}")
    upgrade(engine)
    assert upgrade(engine) == ([], [])


def test_a_database_from_before_projects_keeps_its_risks_and_models(tmp_path):
    """The step just before projects: every table existed, keyed by product_id."""
    path = tmp_path / "products.db"
    old = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(bind=old)
    with old.begin() as conn:  # turn today's schema back into the old names
        conn.exec_driver_sql("ALTER TABLE projects RENAME TO products")
        for table in ("artifacts", "product_models", "risks", "charters"):
            conn.exec_driver_sql(f"ALTER TABLE {table} RENAME COLUMN project_id TO product_id")
        for column in ("description", "app_url", "environments", "default_tester", "archived_at"):
            conn.exec_driver_sql(f"ALTER TABLE products DROP COLUMN {column}")
        conn.exec_driver_sql("INSERT INTO products (id, org_id, name, default_ai_provider) VALUES ('p1', 'org', 'KYC', 'mock')")
        conn.exec_driver_sql("INSERT INTO product_models (id, product_id, summary) VALUES ('m1', 'p1', 'Upload flow')")
        conn.exec_driver_sql("INSERT INTO risks (id, product_id, ref, title, failure_mode, quality_criterion, impact,"
                             " likelihood, evidence) VALUES ('r1', 'p1', 'R1', 'Expired IDs pass', 'x', 'capability', 5, 3, 'stated')")
    old.dispose()

    engine = create_engine(f"sqlite:///{path}")
    renamed, added = upgrade(engine)
    assert "products -> projects" in renamed and "risks.product_id -> risks.project_id" in renamed
    assert set(added) == NEW_PROJECT_COLUMNS
    with Session(engine) as db:
        project = db.get(models.Project, "p1")
        assert [r.ref for r in project.risks] == ["R1"]
        assert [m.summary for m in project.product_models] == ["Upload flow"]
