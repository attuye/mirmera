from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base

from app.config import settings

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def add_missing_columns(bind=None) -> list[str]:
    """Add columns that exist in the models but not yet in the database.

    `Base.metadata.create_all()` creates missing *tables* but never alters
    existing ones, so a column added to a model would be missing from an
    existing database and every query on that table would fail. This adds
    them in place, keeping existing data.

    Only nullable columns can be added this way; a column that must be NOT NULL
    needs a proper migration (we'll switch to Alembic before moving to Postgres).
    Returns the columns it added, as "table.column".
    """
    bind = bind or engine
    inspector = inspect(bind)
    added: list[str] = []
    with bind.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue  # create_all() creates whole new tables
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                if column.primary_key or not column.nullable:
                    raise RuntimeError(
                        f"Cannot auto-add NOT NULL column {table.name}.{column.name}; "
                        "it needs a real migration."
                    )
                column_type = column.type.compile(dialect=bind.dialect)
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {column_type}'))
                added.append(f"{table.name}.{column.name}")
    return added
