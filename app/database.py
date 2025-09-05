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
                # Ensure owner_id exists and, if legacy owner_user_id is NOT NULL, relax it
                info = list(conn.execute(text("PRAGMA table_info(homes)")))
                cols = {row[1]: row for row in info}
                # Add owner_id if missing and backfill from owner_user_id
                if "owner_id" not in cols:
                    _ensure_sqlite_column(conn, "homes", "owner_id", "INTEGER")
                    try:
                        conn.execute(text("UPDATE homes SET owner_id = owner_user_id WHERE owner_id IS NULL"))
                    except Exception as e:
                        print("[WARN] backfill homes.owner_id from owner_user_id failed:", e)

                # If owner_user_id exists and is NOT NULL constrained, rebuild table to relax it
                legacy = cols.get("owner_user_id")
                legacy_notnull = bool(legacy and legacy[3])
                if legacy_notnull:
                    print("[DB] Rebuilding homes to relax NOT NULL on owner_user_id ...")
                    try:
                        conn.execute(text("CREATE TABLE IF NOT EXISTS homes_mig (id INTEGER PRIMARY KEY, name VARCHAR UNIQUE, owner_id INTEGER, owner_user_id INTEGER)"))
                        conn.execute(text("INSERT INTO homes_mig (id, name, owner_id, owner_user_id) SELECT id, name, COALESCE(owner_id, owner_user_id), owner_user_id FROM homes"))
                        conn.execute(text("DROP TABLE homes"))
                        conn.execute(text("ALTER TABLE homes_mig RENAME TO homes"))
                        print("[DB] homes table rebuilt; NOT NULL on owner_user_id removed")
                    except Exception as e:
                        print("[WARN] homes rebuild failed:", e)

            # home_reservations: ensure rrule column exists if table already present
            if "home_reservations" in tables:
                ok = _ensure_sqlite_column(conn, "home_reservations", "rrule", "TEXT DEFAULT ''")
                if ok:
                    print("[DB] home_reservations.rrule present")
    except Exception as e:
        # Do not block startup; log to stdout
        print("[WARN] run_migrations encountered an issue:", e)
