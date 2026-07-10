"""Point the app at a throwaway SQLite file so tests never touch the real DB.

platform/.env sets a relative DATABASE_URL that resolves to the live
app/data/capstack.db when pytest runs from platform/backend, and app.core.db
builds its engine at import time from the lru_cached settings — so the override
must be a real env var (env vars outrank .env values in pydantic-settings) set
here, at conftest import, which pytest guarantees happens before any test
module (and therefore any `app` module) is imported.
"""
import os
import tempfile
from pathlib import Path

os.environ["DATABASE_URL"] = (
    "sqlite:///" + (Path(tempfile.mkdtemp(prefix="capstack-test-")) / "test.db").as_posix()
)
