"""Index cache binding safety regressions."""

from __future__ import annotations

from pathlib import Path

import pytest

from releaseledger.errors import LaunchError
from releaseledger.ledgercore_backend import (
    classify_releaseledger_index_cache,
    load_releaseledger_ledger_layout,
)
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


def _remove_index_marker(tmp_path: Path) -> Path:
    paths = resolve_project_paths(tmp_path)
    paths.project.indexes_binding_path.unlink()
    return paths.project.indexes_root


def test_missing_index_marker_with_current_generated_tree_is_rebound(
    tmp_path: Path,
) -> None:
    initialize_project(tmp_path)
    create_release(tmp_path, version="1.0.0")
    root = _remove_index_marker(tmp_path)
    assert classify_releaseledger_index_cache(root).state == "generated-current"
    rebuild_indexes(tmp_path)
    assert (root / ".ledger-project.toml").is_file()
    report = load_releaseledger_ledger_layout(tmp_path).validation_report
    assert report is not None and report.valid is True


def test_missing_index_marker_with_multiple_ledger_refs_is_rebound(
    tmp_path: Path,
) -> None:
    initialize_project(tmp_path)
    create_release(tmp_path, version="1.0.0")
    root = _remove_index_marker(tmp_path)
    feature = root / "ledgers" / "feature"
    feature.mkdir()
    (feature / "releases.json").write_text("[]", encoding="utf-8")
    (feature / "entries.json").write_text("[]", encoding="utf-8")
    assert classify_releaseledger_index_cache(root).state == "generated-current"
    rebuild_indexes(tmp_path)
    assert (root / ".ledger-project.toml").is_file()


def test_missing_index_marker_with_legacy_flat_generated_files_is_rebound(
    tmp_path: Path,
) -> None:
    initialize_project(tmp_path)
    create_release(tmp_path, version="1.0.0")
    root = _remove_index_marker(tmp_path)
    current = root / "ledgers" / "main"
    (root / "releases.json").write_bytes((current / "releases.json").read_bytes())
    (root / "entries.json").write_bytes((current / "entries.json").read_bytes())
    import shutil

    shutil.rmtree(root / "ledgers")
    assert classify_releaseledger_index_cache(root).state == "generated-legacy"
    rebuild_indexes(tmp_path)
    assert (root / ".ledger-project.toml").is_file()


def test_missing_index_marker_with_foreign_nested_file_is_rejected(
    tmp_path: Path,
) -> None:
    initialize_project(tmp_path)
    create_release(tmp_path, version="1.0.0")
    root = _remove_index_marker(tmp_path)
    foreign = root / "ledgers" / "main" / "foreign.dat"
    foreign.write_text("foreign", encoding="utf-8")
    classification = classify_releaseledger_index_cache(root)
    assert classification.state == "foreign"
    assert Path("ledgers/main/foreign.dat") in classification.unexpected_paths
    with pytest.raises(LaunchError, match="ledgers/main/foreign.dat"):
        rebuild_indexes(tmp_path)


def test_missing_index_marker_with_symlink_is_rejected(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    create_release(tmp_path, version="1.0.0")
    root = _remove_index_marker(tmp_path)
    target = root / "ledgers" / "main" / "releases.json"
    link = root / "ledgers" / "main" / "alias.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable")
    classification = classify_releaseledger_index_cache(root)
    assert classification.state == "foreign"
    with pytest.raises(LaunchError, match="ledgers/main/alias.json"):
        rebuild_indexes(tmp_path)
