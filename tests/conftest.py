"""Session-level isolation for tests that exercise the schema-3 layout.

Releaseledger 0.4 stores durable data under the project ``.ledger/`` tree
but writes indexes into ``$XDG_CACHE_HOME/ledgerwerk/releaseledger/...``
and user-data mounts under ``$XDG_DATA_HOME/ledgerwerk/...``. Without
isolating those roots, every test would read and mutate the real user
cache and data directory. This conftest redirects both to a session-level
temporary directory that is wiped on teardown.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def _isolated_ledger_user_roots() -> None:
    """Redirect Ledgercore's user-data and user-cache roots for the test session.

    Sets ``XDG_DATA_HOME`` and ``XDG_CACHE_HOME`` to a temporary directory
    before the first test runs and removes the directory on session end.
    Individual tests may still override these through ``monkeypatch``.
    """

    base = Path(tempfile.mkdtemp(prefix="releaseledger-session-roots-"))
    data = base / "data"
    cache = base / "cache"
    data.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    os.environ["XDG_DATA_HOME"] = str(data)
    os.environ["XDG_CACHE_HOME"] = str(cache)
    os.environ.setdefault("HOME", str(base / "home"))
    try:
        yield
    finally:
        shutil.rmtree(base, ignore_errors=True)
