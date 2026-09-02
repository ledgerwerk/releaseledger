"""Index cache binding safety regressions."""

from __future__ import annotations

from pathlib import Path

import pytest

from releaseledger.errors import LaunchError
from releaseledger.ledgercore_backend import load_releaseledger_ledger_layout
from releaseledger.services.releases import create_release
from releaseledger.storage.paths import initialize_project, resolve_project_paths
from releaseledger.storage.store import rebuild_indexes


def test_rebuilding_deleted_index_cache_restores_binding(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    create_release(tmp_path, version="1.0.0")
    paths = resolve_project_paths(tmp_path)
    import shutil

    shutil.rmtree(paths.project.indexes_root)
    rebuild_indexes(tmp_path)

    assert paths.project.indexes_binding_path.is_file()
    layout = load_releaseledger_ledger_layout(tmp_path, validate_storage=True)
    assert layout.validation_report is not None
    assert layout.validation_report.valid is True


def test_rebuild_refuses_unexpected_unbound_cache_contents(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    create_release(tmp_path, version="1.0.0")
    paths = resolve_project_paths(tmp_path)
    import shutil

    shutil.rmtree(paths.project.indexes_root)
    paths.project.indexes_root.mkdir(parents=True)
    (paths.project.indexes_root / "foreign-cache.dat").write_text(
        "not releaseledger", encoding="utf-8"
    )

    with pytest.raises(LaunchError, match="unexpected files"):
        rebuild_indexes(tmp_path)
