from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base

from app.config import settings

SQLITE = settings.database_url.startswith("sqlite")
BUSY_SECONDS = 30  # how long a write waits for another one to finish before "database is locked"
connect_args = {"check_same_thread": False, "timeout": BUSY_SECONDS} if SQLITE else {}
engine = create_engine(settings.database_url, connect_args=connect_args)

if SQLITE and ":memory:" not in settings.database_url:
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record):
        """Write-ahead logging: readers never block the writer and the writer never blocks
        readers. Without it, any open read (a page polling, an agent waiting) makes every
        write wait, and the Jira app's calls fail with "database is locked"."""
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={BUSY_SECONDS * 1000}")
        cursor.execute("PRAGMA synchronous=NORMAL")  # safe with WAL, and much faster
        cursor.close()
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
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {column_type}{_default_sql(column)}'))
                added.append(f"{table.name}.{column.name}")
    fill_missing_flags(bind)
    return added


def _default_sql(column) -> str:
    """' DEFAULT 0' for a column with a plain default (False, 0, 'medium'), so the rows already
    there get it too. Without it they hold NULL: the model's default only applies to new rows,
    and an API that promises a true/false answer then fails on them."""
    d = column.default
    if d is None or not d.is_scalar or d.arg is None:
        return ""
    v = d.arg
    if isinstance(v, bool):
        return f" DEFAULT {int(v)}"
    if isinstance(v, (int, float)):
        return f" DEFAULT {v}"
    if isinstance(v, str):
        return " DEFAULT '" + v.replace("'", "''") + "'"
    return ""


def fill_missing_flags(bind=None) -> list[str]:
    """True/false columns left NULL by an earlier upgrade (one that added the column without its
    default, such as projects.ethiopia) get their default. NULL never means anything for these.
    Safe on every start: once filled, nothing matches. Returns "table.column" for each one filled."""
    bind = bind or engine
    filled: list[str] = []
    with bind.begin() as conn:
        inspector = inspect(conn)
        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                d = column.default
                if column.name not in existing or d is None or not d.is_scalar or not isinstance(d.arg, bool):
                    continue
                r = conn.execute(text(f'UPDATE "{table.name}" SET "{column.name}" = :v WHERE "{column.name}" IS NULL'),
                                 {"v": d.arg})
                if r.rowcount:
                    filled.append(f"{table.name}.{column.name}")
    return filled


# Renames done by later steps, applied before create_all() so existing data
# moves to the new names instead of an empty table being created next to it.
TABLE_RENAMES = [("products", "projects")]  # projects step
COLUMN_RENAMES = [(table, "product_id", "project_id")  # projects step
                  for table in ("artifacts", "product_models", "risks", "charters")]


def apply_renames(bind=None) -> list[str]:
    """Rename old tables and columns in place, keeping their data. Safe to run
    on every start: anything already renamed (or never created) is skipped.
    Returns what it renamed, as "old -> new"."""
    bind = bind or engine
    done: list[str] = []
    with bind.begin() as conn:
        inspector = inspect(conn)
        for old, new in TABLE_RENAMES:
            if inspector.has_table(old) and not inspector.has_table(new):
                conn.execute(text(f'ALTER TABLE "{old}" RENAME TO "{new}"'))
                done.append(f"{old} -> {new}")
        inspector = inspect(conn)  # fresh: table names may have changed
        for table, old, new in COLUMN_RENAMES:
            if not inspector.has_table(table):
                continue
            columns = {c["name"] for c in inspector.get_columns(table)}
            if old in columns and new not in columns:
                conn.execute(text(f'ALTER TABLE "{table}" RENAME COLUMN "{old}" TO "{new}"'))
                done.append(f"{table}.{old} -> {table}.{new}")
    return done
