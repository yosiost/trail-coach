"""Pytest setup: force a throwaway SQLite DB, isolate each test with a fresh schema."""
import os
import tempfile

# Must run before api.* imports (db.py binds its DB path at import time).
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
os.environ.pop("DATABASE_URL", None)   # -> SQLite
os.environ.pop("SEED_DEMO_DATA", None)  # -> no auto-seed

from pathlib import Path
import pytest


@pytest.fixture(autouse=True)
def fresh_db():
    """Empty DB before every test."""
    from api import db
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db._DB) + suffix)
        if p.exists():
            p.unlink()
    db.init_db()
    yield
