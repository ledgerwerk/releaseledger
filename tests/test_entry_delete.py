from __future__ import annotations

from pathlib import Path

import pytest

from releaseledger.errors import LaunchError
from releaseledger.services.entries import add_release_entry, delete_release_entry
from releaseledger.services.releases import create_release
from releaseledger.storage.paths import ensure_canonical_project
from releaseledger.storage.store import load_entries, load_release


def test_delete_rejected_entry_updates_release_and_supports_dry_run(tmp_path: Path) -> None:
    ensure_canonical_project(tmp_path)
    create_release(tmp_path, version="1.0.0")
    add_release_entry(
        tmp_path,
        release_version="1.0.0",
        kind="added",
        summary="Added a probe",
        status="rejected",
    )
    preview = delete_release_entry(
        tmp_path,
        release_version="1.0.0",
        entry_id="entry-0001",
        reason="Accidental validation probe",
        dry_run=True,
    )
    assert preview["written"] is False
    assert len(load_entries(tmp_path, "1.0.0")) == 1
    result = delete_release_entry(
        tmp_path,
        release_version="1.0.0",
        entry_id="entry-0001",
        reason="Accidental validation probe",
    )
    assert result["deleted"] is True
    assert load_release(tmp_path, "1.0.0").entry_count == 0
    assert load_entries(tmp_path, "1.0.0") == []


def test_delete_accepted_entry_requires_force_and_reason(tmp_path: Path) -> None:
    ensure_canonical_project(tmp_path)
    create_release(tmp_path, version="1.0.0")
    add_release_entry(
        tmp_path,
        release_version="1.0.0",
        kind="added",
        summary="Added accepted behavior",
    )
    with pytest.raises(LaunchError, match="force-accepted"):
        delete_release_entry(
            tmp_path,
            release_version="1.0.0",
            entry_id="entry-0001",
            reason="cleanup",
        )
    with pytest.raises(LaunchError, match="non-empty reason"):
        delete_release_entry(
            tmp_path,
            release_version="1.0.0",
            entry_id="entry-0001",
            reason="",
            force_accepted=True,
        )
