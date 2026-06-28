import os
from pathlib import Path
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from db.models import Base

DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

DB_PATH = DATA_DIR / "investments.db"
DATABASE_URL = f"sqlite:///{DB_PATH}"


def get_engine():
    return create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


def get_session_factory(engine):
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db(engine):
    Base.metadata.create_all(engine)


def run_migrations(engine):
    """Apply incremental schema changes to an existing database."""
    with engine.connect() as conn:
        for stmt in [
            "ALTER TABLE companies ADD COLUMN is_listed BOOLEAN NOT NULL DEFAULT 1",
        ]:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception:
                pass  # column already exists
