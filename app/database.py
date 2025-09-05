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

            # homes: ensure owner_id column exists
            if "homes" in tables:
                cols = {row[1] for row in conn.execute(text("PRAGMA table_info(homes)"))}
                if "owner_id" not in cols:
                    conn.execute(text("ALTER TABLE homes ADD COLUMN owner_id INTEGER"))

            # home_reservations: ensure rrule column exists if table already present
            if "home_reservations" in tables:
                cols = {row[1] for row in conn.execute(text("PRAGMA table_info(home_reservations)"))}
                if "rrule" not in cols:
                    conn.execute(text("ALTER TABLE home_reservations ADD COLUMN rrule TEXT DEFAULT ''"))
    except Exception as e:
        # Do not block startup; log to stdout
        print("[WARN] run_migrations encountered an issue:", e)
