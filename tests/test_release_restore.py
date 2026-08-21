from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from releaseledger.domain.event import EVENT_RELEASE_RESTORED
from releaseledger.errors import LaunchError
from releaseledger.services.events import load_events
from releaseledger.services.releases import (
    create_release,
    finalize_release,
    restore_release,
    update_release,
)
from releaseledger.storage.paths import ensure_canonical_project
from releaseledger.storage.store import load_release


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def test_finalize_already_released_is_compatible_noop(tmp_path: Path) -> None:
    ensure_canonical_project(tmp_path)
    create_release(tmp_path, version="0.1.0", status="released", released_at="2026-01-01")
    before = load_release(tmp_path, "0.1.0")

    result = finalize_release(tmp_path, version="0.1.0", released_at="2026-01-01")

    assert result["already_finalized"] is True
    assert result["written"] is False
    assert result["events"] == []
    assert load_release(tmp_path, "0.1.0").versioning.revision == before.versioning.revision


def test_finalize_already_released_rejects_conflicting_date(tmp_path: Path) -> None:
    ensure_canonical_project(tmp_path)
    create_release(tmp_path, version="0.1.0", status="released", released_at="2026-01-01")

    with pytest.raises(LaunchError):
        finalize_release(tmp_path, version="0.1.0", released_at="2026-01-02")


def test_generic_update_rejects_terminal_status_transition(tmp_path: Path) -> None:
    ensure_canonical_project(tmp_path)
    create_release(tmp_path, version="0.1.0", status="canceled")

    with pytest.raises(LaunchError):
        update_release(tmp_path, version="0.1.0", status="planned")


def test_restore_reopens_canceled_release_and_records_event(tmp_path: Path) -> None:
    ensure_canonical_project(tmp_path)
    create_release(
        tmp_path,
        version="0.1.0",
        status="canceled",
    )
    update_release(
        tmp_path,
        version="0.1.0",
        note="old note",
    )
    restored = restore_release(
        tmp_path,
        version="0.1.0",
        to_status="candidate",
        reason="Development resumed after premature cancellation.",
    )

    record = load_release(tmp_path, "0.1.0")
    assert record.status == "candidate"
    assert record.cancel_reason is None
    assert record.superseded_by is None
    assert restored["events"]
    assert any(event.event == EVENT_RELEASE_RESTORED for event in load_events(tmp_path))


def test_restore_from_tag_sets_release_metadata(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.name", "Tester")
    _git(tmp_path, "config", "user.email", "tester@example.com")
    (tmp_path / "README.md").write_text("history\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-m", "initial")
    _git(tmp_path, "tag", "v0.1.0")
    ensure_canonical_project(tmp_path)
    create_release(tmp_path, version="0.1.0", status="canceled")

    result = restore_release(
        tmp_path,
        version="0.1.0",
        from_tag="v0.1.0",
        reason="The tagged release was shipped after the ledger was canceled.",
    )

    record = load_release(tmp_path, "0.1.0")
    assert record.status == "released"
    assert record.released_at
    assert record.git_head_ref == "v0.1.0"
    assert record.git_head_sha
    assert result["mode"] == "from_tag"
    assert result["tag"] == "v0.1.0"


def test_restore_rejects_mismatched_tag_before_writing(tmp_path: Path) -> None:
    ensure_canonical_project(tmp_path)
    create_release(tmp_path, version="0.1.0", status="canceled")
    before = load_release(tmp_path, "0.1.0")

    with pytest.raises(LaunchError):
        restore_release(
            tmp_path,
            version="0.1.0",
            from_tag="v0.2.0",
            reason="Wrong tag should fail.",
        )

    assert load_release(tmp_path, "0.1.0").versioning.revision == before.versioning.revision
