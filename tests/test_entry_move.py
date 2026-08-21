from __future__ import annotations

from pathlib import Path

import pytest

from releaseledger.errors import LaunchError
from releaseledger.services.entries import add_release_entry, move_release_entry
from releaseledger.services.releases import create_release
from releaseledger.storage.paths import ensure_canonical_project
from releaseledger.storage.store import load_entries, load_release


def _fixture(tmp_path: Path) -> tuple[str, str]:
    ensure_canonical_project(tmp_path)
    create_release(tmp_path, version="0.1.0")
    create_release(tmp_path, version="0.2.0")
    add_release_entry(
        tmp_path,
        release_version="0.1.0",
        kind="added",
        summary="keep me",
    )
    second = add_release_entry(
        tmp_path,
        release_version="0.1.0",
        kind="added",
        summary="move me",
    )
    add_release_entry(
        tmp_path,
        release_version="0.2.0",
        kind="fixed",
        summary="target entry",
    )
    return "entry-0001", str(second["entry"]["entry_id"])


def test_entry_move_dry_run_does_not_write(tmp_path: Path) -> None:
    _, entry_id = _fixture(tmp_path)
    result = move_release_entry(
        tmp_path,
        source_version="0.1.0",
        entry_id=entry_id,
        target_version="0.2.0",
        reason="Consolidate release notes.",
        dry_run=True,
    )

    assert result["written"] is False
    assert len(load_entries(tmp_path, "0.1.0")) == 2
    assert len(load_entries(tmp_path, "0.2.0")) == 1


def test_entry_move_preserves_entry_id_and_updates_revisions(tmp_path: Path) -> None:
    _, entry_id = _fixture(tmp_path)
    result = move_release_entry(
        tmp_path,
        source_version="0.1.0",
        entry_id=entry_id,
        target_version="0.2.0",
        reason="Consolidate release notes.",
    )

    assert result["events"]
    assert len(load_entries(tmp_path, "0.1.0")) == 1
    moved = load_entries(tmp_path, "0.2.0")[1]
    assert moved.entry_id == entry_id
    assert moved.release_version == "0.2.0"
    assert load_release(tmp_path, "0.1.0").entry_count == 1
    assert load_release(tmp_path, "0.2.0").entry_count == 2


def test_entry_move_requires_renumber_for_target_collision(tmp_path: Path) -> None:
    entry_id, _ = _fixture(tmp_path)
    with pytest.raises(LaunchError):
        move_release_entry(
            tmp_path,
            source_version="0.1.0",
            entry_id=entry_id,
            target_version="0.2.0",
            reason="Collision must be explicit.",
        )

    result = move_release_entry(
        tmp_path,
        source_version="0.1.0",
        entry_id=entry_id,
        target_version="0.2.0",
        reason="Allocate a deterministic target ID.",
        renumber=True,
    )
    assert result["renumbered"] is True
    assert len(load_entries(tmp_path, "0.2.0")) == 2


def test_entry_move_rejects_duplicate_source_reference_ownership(
    tmp_path: Path,
) -> None:
    ensure_canonical_project(tmp_path)
    create_release(tmp_path, version="0.1.0")
    create_release(tmp_path, version="0.2.0")
    add_release_entry(
        tmp_path,
        release_version="0.2.0",
        kind="fixed",
        summary="owns ref",
        source_refs=("git:" + "a" * 40,),
    )
    added = add_release_entry(
        tmp_path,
        release_version="0.1.0",
        kind="added",
        summary="duplicate ref",
        source_refs=("git:" + "a" * 40,),
    )
    with pytest.raises(LaunchError):
        move_release_entry(
            tmp_path,
            source_version="0.1.0",
            entry_id=str(added["entry"]["entry_id"]),
            target_version="0.2.0",
            reason="Reject duplicate ownership.",
        )
