from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from releaseledger.domain.versioning import bump_versioning
from releaseledger.errors import LaunchError
from releaseledger.services.entries import add_release_entry
from releaseledger.services.releases import create_release, rename_release
from releaseledger.storage.paths import ensure_canonical_project
from releaseledger.storage.store import load_entries, load_release


def _fixture(tmp_path: Path) -> None:
    ensure_canonical_project(tmp_path)
    create_release(tmp_path, version="v0.1.0", status="canceled")
    create_release(tmp_path, version="0.1.0", status="canceled")
    add_release_entry(
        tmp_path,
        release_version="v0.1.0",
        kind="added",
        summary="source one",
    )
    add_release_entry(
        tmp_path,
        release_version="v0.1.0",
        kind="fixed",
        summary="source two",
    )
    add_release_entry(
        tmp_path,
        release_version="0.1.0",
        kind="changed",
        summary="obsolete target",
    )


def test_replace_canceled_target_dry_run_preserves_bundles(tmp_path: Path) -> None:
    _fixture(tmp_path)
    preview = rename_release(
        tmp_path,
        old_version="v0.1.0",
        new_version="0.1.0",
        replace_canceled_target=True,
        reason="Keep the source release bundle.",
        dry_run=True,
    )

    assert preview["replace_canceled_target"] is True
    assert preview["source_entry_count"] == 2
    assert preview["displaced_target_entry_count"] == 1
    assert load_release(tmp_path, "v0.1.0").status == "canceled"
    assert len(load_entries(tmp_path, "v0.1.0")) == 2
    assert len(load_entries(tmp_path, "0.1.0")) == 1


def test_replace_canceled_target_moves_entries_and_removes_source(
    tmp_path: Path,
) -> None:
    _fixture(tmp_path)
    rename_release(
        tmp_path,
        old_version="v0.1.0",
        new_version="0.1.0",
        replace_canceled_target=True,
        reason="Keep the source release bundle.",
    )

    assert (
        not (tmp_path / ".releaseledger")
        .joinpath("ledgers", "main", "releases", "v0.1.0")
        .exists()
    )
    entries = load_entries(tmp_path, "0.1.0")
    assert [entry.summary for entry in entries] == ["source one", "source two"]
    assert all(entry.release_version == "0.1.0" for entry in entries)
    assert load_release(tmp_path, "0.1.0").status == "canceled"


def test_replace_canceled_target_rejects_non_canceled_target(tmp_path: Path) -> None:
    _fixture(tmp_path)
    from releaseledger.storage.store import save_release

    target = load_release(tmp_path, "0.1.0")
    save_release(
        tmp_path,
        replace(
            target, status="released", versioning=bump_versioning(target.versioning)
        ),
        overwrite=True,
    )
    with pytest.raises(LaunchError):
        rename_release(
            tmp_path,
            old_version="v0.1.0",
            new_version="0.1.0",
            replace_canceled_target=True,
            reason="Must reject active target.",
        )


def test_replace_canceled_target_rolls_back_after_source_move_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fixture(tmp_path)
    from releaseledger.storage import store

    original_rename = Path.rename

    def fail_source_move(self: Path, target: Path):
        if self.name == "v0.1.0" and ".source-backup-" in target.name:
            raise RuntimeError("injected source move failure")
        return original_rename(self, target)

    monkeypatch.setattr(store.Path, "rename", fail_source_move)
    with pytest.raises(RuntimeError):
        rename_release(
            tmp_path,
            old_version="v0.1.0",
            new_version="0.1.0",
            replace_canceled_target=True,
            reason="Rollback test.",
        )

    assert len(load_entries(tmp_path, "v0.1.0")) == 2
    assert [entry.summary for entry in load_entries(tmp_path, "0.1.0")] == [
        "obsolete target"
    ]
