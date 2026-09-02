from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import ledgercore
import pytest

from releaseledger.domain.entry import ReleaseEntryRecord
from releaseledger.domain.event import event_from_dict
from releaseledger.domain.release import ReleaseRecord
from releaseledger.domain.versioning import (
    RecordVersioning,
    bump_versioning,
    versioning_from_dict,
)
from releaseledger.errors import LaunchError
from releaseledger.services.entries import add_release_entry, update_release_entry
from releaseledger.services.events import load_events
from releaseledger.services.releases import create_release
from releaseledger.storage.paths import initialize_project, resolve_project_paths
from releaseledger.storage.store import load_release, save_entry, save_release


def test_versioning_parses_and_bumps() -> None:
    value = versioning_from_dict({"schema_version": 1, "revision": 2})
    assert value == RecordVersioning(revision=2)
    assert bump_versioning(value).revision == 3
    for invalid in (None, {}, {"schema_version": 1, "revision": 0}):
        with pytest.raises(LaunchError):
            versioning_from_dict(invalid)


def test_storage_rejects_invalid_release_revision_transitions(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    with pytest.raises(LaunchError, match="must start at 1"):
        save_release(
            tmp_path,
            ReleaseRecord(version="1.0.0", versioning=RecordVersioning(revision=2)),
        )

    record = ReleaseRecord(version="1.0.0")
    save_release(tmp_path, record)
    with pytest.raises(LaunchError, match="revision must be 2"):
        save_release(tmp_path, replace(record, title="Changed"), overwrite=True)
    with pytest.raises(LaunchError, match="revision must be 2"):
        save_release(
            tmp_path,
            replace(
                record,
                title="Changed",
                versioning=RecordVersioning(revision=3),
            ),
            overwrite=True,
        )
    with pytest.raises(LaunchError, match="revision must be 1"):
        save_release(
            tmp_path,
            replace(record, versioning=RecordVersioning(revision=2)),
            overwrite=True,
        )


def test_entry_mutations_increment_expected_records(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    create_release(tmp_path, version="1.0.0")
    add_release_entry(
        tmp_path,
        release_version="1.0.0",
        kind="added",
        summary="Added deterministic storage",
    )
    paths = resolve_project_paths(tmp_path)
    release_data, _ = ledgercore.read_front_matter_document(
        paths.releases_dir / "1.0.0" / "release.md"
    )
    entry_path = paths.releases_dir / "1.0.0" / "entries" / "entry-0001.md"
    entry_data, _ = ledgercore.read_front_matter_document(entry_path)
    assert release_data["versioning"]["revision"] == 2
    assert entry_data["versioning"]["revision"] == 1

    update_release_entry(
        tmp_path,
        release_version="1.0.0",
        entry_id="entry-0001",
        summary="Added validated deterministic storage",
    )
    assert load_release(tmp_path, "1.0.0").versioning.revision == 2
    entry_data, _ = ledgercore.read_front_matter_document(entry_path)
    assert entry_data["versioning"]["revision"] == 2


def test_event_rows_reject_deltas_and_report_revisions(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    create_release(tmp_path, version="1.0.0")
    add_release_entry(
        tmp_path,
        release_version="1.0.0",
        kind="added",
        summary="Added event revisions",
    )
    for event in load_events(tmp_path):
        row = event.to_dict()
        assert not {"ts", "to", "to_version", "from", "from_version"} & row.keys()
        assert all(value > 0 for value in event.record_revisions.values())
    with pytest.raises(LaunchError, match="forbidden"):
        event_from_dict(
            {
                "schema_version": 2,
                "event_id": "event-9999",
                "event": "release.renamed",
                "to_version": "2.0.0",
            }
        )


def test_entry_storage_requires_revision_bump(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    save_release(tmp_path, ReleaseRecord(version="1.0.0"))
    entry = ReleaseEntryRecord(
        entry_id="entry-0001",
        release_version="1.0.0",
        kind="added",
        summary="Added entry",
    )
    save_entry(tmp_path, entry)
    with pytest.raises(LaunchError, match="revision must be 2"):
        save_entry(tmp_path, replace(entry, summary="Changed entry"))
