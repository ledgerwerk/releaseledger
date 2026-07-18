"""Regression tests for atomic entry writes (fix-doc Section 3B).

The run that motivated the skill audit exposed a partial-write hazard: an
``entry add`` could write an ``entry-*.md`` file and then fail on a stale
release revision, leaving an orphan entry file and an inconsistent
``entry_count``. These tests pin the atomicity guarantee: when the release
save fails, the just-written entry file(s) are rolled back and the release
``entry_count`` is unchanged.

The stale-revision conflict is produced by patching the module-level
``save_release`` reference in ``releaseledger.services.entries`` to raise
``LaunchError`` -- the exception the storage layer raises when
``_validate_revision_transition`` detects a stale revision. A separate sanity
test proves that failure mode is genuinely reachable without patching.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from releaseledger.cli import app
from releaseledger.errors import CODE_VALIDATION_ERROR, LaunchError
from releaseledger.services import entries as entries_module
from releaseledger.services.entries import (
    add_many_release_entries,
    add_release_entry,
    import_release_entry_file,
)
from releaseledger.storage.store import load_entries, load_release

runner = CliRunner()


# --------------------------------------------------------------------------
# Workspace helpers
# --------------------------------------------------------------------------


def _init(tmp_path: Path) -> Path:
    result = runner.invoke(app, ["--cwd", str(tmp_path), "init"])
    assert result.exit_code == 0, result.stdout
    return tmp_path


def _create_release(tmp_path: Path, version: str = "0.5.0") -> None:
    result = runner.invoke(
        app,
        [
            "--cwd",
            str(tmp_path),
            "release",
            "create",
            version,
            "--previous",
            "0.4.0",
            "--released-at",
            "2026-06-14",
        ],
    )
    assert result.exit_code == 0, result.stdout


def _entry_files(tmp_path: Path, version: str = "0.5.0") -> list[Path]:
    from releaseledger.storage.paths import resolve_project_paths

    paths = resolve_project_paths(tmp_path)
    entries_dir = paths.releases_dir / version / "entries"
    if not entries_dir.is_dir():
        return []
    return sorted(entries_dir.glob("entry-*.md"))


def _force_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the entries-module save_release raise a stale-revision conflict."""

    def _boom(*args: object, **kwargs: object) -> None:
        raise LaunchError(
            "simulated stale release revision",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )

    monkeypatch.setattr(entries_module, "save_release", _boom)


# --------------------------------------------------------------------------
# add_release_entry
# --------------------------------------------------------------------------


def test_add_release_entry_rolls_back_on_release_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _init(tmp_path)
    _create_release(workspace)
    before = load_release(workspace, "0.5.0")
    assert before.entry_count == 0
    assert _entry_files(workspace) == []

    _force_conflict(monkeypatch)
    with pytest.raises(LaunchError):
        add_release_entry(
            workspace,
            release_version="0.5.0",
            kind="added",
            summary="Added a feature from reviewed evidence",
            source_refs=("git:abc1234",),
        )

    # No orphan entry file remains.
    assert _entry_files(workspace) == [], "orphan entry file left after conflict"
    # entry_count was never bumped.
    after = load_release(workspace, "0.5.0")
    assert after.entry_count == 0


def test_entry_add_cli_surfaces_conflict_without_orphan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _init(tmp_path)
    _create_release(workspace)
    _force_conflict(monkeypatch)

    result = runner.invoke(
        app,
        [
            "--cwd",
            str(workspace),
            "entry",
            "add",
            "0.5.0",
            "--kind",
            "added",
            "--summary",
            "Added a feature",
        ],
    )
    assert result.exit_code != 0, "CLI should fail on release conflict"
    assert _entry_files(workspace) == [], "no orphan entry file after CLI failure"
    assert load_release(workspace, "0.5.0").entry_count == 0


# --------------------------------------------------------------------------
# import_release_entry_file
# --------------------------------------------------------------------------


def test_import_release_entry_rolls_back_on_release_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _init(tmp_path)
    _create_release(workspace)
    assert _entry_files(workspace) == []

    # Author a source entry file to import.
    source = tmp_path / "entry.yaml"
    source.write_text(
        "object_type: release_entry\n"
        "kind: added\n"
        "summary: Imported feature from reviewed evidence\n"
        "status: accepted\n"
        "source_refs:\n"
        "  - git:deadbee\n",
        encoding="utf-8",
    )

    _force_conflict(monkeypatch)
    with pytest.raises(LaunchError):
        import_release_entry_file(
            workspace,
            release_version="0.5.0",
            source_path=source,
        )

    assert _entry_files(workspace) == [], "orphan entry file left after import conflict"
    assert load_release(workspace, "0.5.0").entry_count == 0


# --------------------------------------------------------------------------
# add_many_release_entries
# --------------------------------------------------------------------------


def test_add_many_rolls_back_all_entries_on_release_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _init(tmp_path)
    _create_release(workspace)
    assert _entry_files(workspace) == []

    batch: list[dict[str, object]] = [
        {
            "kind": "added",
            "summary": "First reviewed change",
            "source_refs": ["git:1111111"],
        },
        {
            "kind": "fixed",
            "summary": "Second reviewed change",
            "source_refs": ["git:2222222"],
        },
    ]

    _force_conflict(monkeypatch)
    with pytest.raises(LaunchError):
        add_many_release_entries(
            workspace,
            release_version="0.5.0",
            entries=batch,
        )

    assert _entry_files(workspace) == [], "orphan entry files left after batch conflict"
    assert load_release(workspace, "0.5.0").entry_count == 0


def test_add_many_strict_warning_returns_preview_without_writing(
    tmp_path: Path,
) -> None:
    workspace = _init(tmp_path)
    _create_release(workspace)
    result = add_many_release_entries(
        workspace,
        release_version="0.5.0",
        entries=[
            {
                "kind": "added",
                "summary": "Added a reviewed change.",
                "source_refs": ["git:1111111111111111111111111111111111111111"],
            }
        ],
        fail_on_warning=True,
    )
    assert result["written"] is False
    assert result["issues"], "strict warning should block the batch before writes"
    assert _entry_files(workspace) == []
    assert load_release(workspace, "0.5.0").entry_count == 0


def test_add_many_rejects_duplicate_source_refs_before_write(tmp_path: Path) -> None:
    workspace = _init(tmp_path)
    _create_release(workspace)
    add_release_entry(
        workspace,
        release_version="0.5.0",
        kind="added",
        summary="Added baseline reviewed change",
        source_refs=("git:1111111111111111111111111111111111111111",),
    )
    result = add_many_release_entries(
        workspace,
        release_version="0.5.0",
        entries=[
            {
                "kind": "fixed",
                "summary": "Fixed duplicate source coverage",
                "source_refs": ["git:1111111111111111111111111111111111111111"],
            }
        ],
    )
    assert result["written"] is False
    issues = result["issues"]
    assert isinstance(issues, list)
    assert any(issue.get("code") == "duplicate_source_ref" for issue in issues)
    assert len(load_entries(workspace, "0.5.0")) == 1


# --------------------------------------------------------------------------
# Sanity: the stale-revision failure mode is real (no patching)
# --------------------------------------------------------------------------


def test_release_revision_conflict_is_genuinely_reachable(tmp_path: Path) -> None:
    """Two stale in-memory updates must collide on the storage validator.

    A concurrent writer lands a content change first, bumping the on-disk
    revision. Our stale in-memory view then mismatches and the storage
    validator rejects it with CODE_VALIDATION_ERROR -- the real failure mode
    the rollback tests simulate by patching save_release.
    """
    from dataclasses import replace

    from releaseledger.domain.versioning import bump_versioning
    from releaseledger.storage.store import save_release

    workspace = _init(tmp_path)
    _create_release(workspace)
    loaded = load_release(workspace, "0.5.0")

    # A concurrent writer changes real content and bumps the on-disk revision.
    concurrent = replace(
        loaded, note="concurrent update", versioning=bump_versioning(loaded.versioning)
    )
    save_release(workspace, concurrent, overwrite=True)

    # Our stale in-memory view (still at the original revision) now mismatches.
    stale = replace(
        loaded, note="stale note", versioning=bump_versioning(loaded.versioning)
    )
    with pytest.raises(LaunchError) as exc_info:
        save_release(workspace, stale, overwrite=True)
    assert exc_info.value.code == CODE_VALIDATION_ERROR


# --------------------------------------------------------------------------
# Happy paths still work (rollback did not break normal writes)
# --------------------------------------------------------------------------


def test_add_release_entry_normal_path_still_writes(tmp_path: Path) -> None:
    workspace = _init(tmp_path)
    _create_release(workspace)
    add_release_entry(
        workspace,
        release_version="0.5.0",
        kind="added",
        summary="Added a feature from reviewed evidence",
        source_refs=("git:abc1234",),
    )
    files = _entry_files(workspace)
    assert len(files) == 1
    assert load_release(workspace, "0.5.0").entry_count == 1
    assert len(load_entries(workspace, "0.5.0")) == 1
