from __future__ import annotations
import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base

SQLITE_PATH = os.getenv("SQLITE_PATH", "/data/app.db")
DATABASE_URL = f"sqlite:///{SQLITE_PATH}"

connect_args = {"check_same_thread": False}
engine = create_engine(DATABASE_URL, connect_args=connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


def _ensure_sqlite_column(conn, table: str, column: str, column_def: str) -> bool:
    """Ensure a column exists on a SQLite table.

    Returns True if the column exists (pre-existing or added), False otherwise.
    """
    cols = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
    if column in cols:
        return True
    try:
        print(f"[DB] Adding column {column} to {table} ...")
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {column_def}"))
        return True
    except Exception as e:
        print(f"[WARN] ALTER TABLE add column failed for {table}.{column}: {e}")
        return False


def run_migrations(engine) -> None:
    """Lightweight, in-place SQLite migrations for backward compatibility.

    - Adds missing columns introduced by newer versions without requiring Alembic.
    - Safe to run multiple times.
    """
    try:
        with engine.begin() as conn:
            # Only applicable to SQLite
            if not str(engine.url).startswith("sqlite"):
                return

            # Fetch existing tables
            tables = {row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
            print(f"[DB] Existing tables: {sorted(tables)}")

            # homes: ensure owner_id column exists
            if "homes" in tables:
                ok = _ensure_sqlite_column(conn, "homes", "owner_id", "INTEGER")
                if ok:
                    print("[DB] homes.owner_id present")

            # home_reservations: ensure rrule column exists if table already present
            if "home_reservations" in tables:
                ok = _ensure_sqlite_column(conn, "home_reservations", "rrule", "TEXT DEFAULT ''")
                if ok:
                    print("[DB] home_reservations.rrule present")
    except Exception as e:
        # Do not block startup; log to stdout
        print("[WARN] run_migrations encountered an issue:", e)
