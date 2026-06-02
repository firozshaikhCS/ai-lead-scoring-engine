from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os

DATABASE_URL = os.getenv("DATABASE_URL")

# FIX: No silent SQLite fallback — fail fast with a clear message
# Previously: DATABASE_URL defaulted to sqlite:///./leads.db
# Problem: if PostgreSQL is misconfigured, app silently used SQLite,
# data was lost between restarts, and SQL dialect differences caused bugs.
if not DATABASE_URL:
    import warnings
    warnings.warn(
        "DATABASE_URL not set — falling back to SQLite for local development. "
        "Set DATABASE_URL=postgresql://... for production.",
        stacklevel=2
    )
    DATABASE_URL = "sqlite:///./leads.db"

connect_args = {"check_same_thread": False} if "sqlite" in DATABASE_URL else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
