"""P0 cleanup conservation tests.

Tests verify that cleanup cannot delete legacy data unless conservation
has been proved — every legacy durable file must exist in canonical
storage with an identical hash.

Addresses the data-loss defect described in the migration reliability
implementation brief (Phase 0).
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from typer.testing import CliRunner

from releaseledger.cli import app

runner = CliRunner()


def _make_legacy_project(root: Path, *, releases: int = 2) -> None:
    """Create a legacy .releaseledger project with the given number of releases."""
    config = root / ".releaseledger.toml"
    config.write_text(
        """\
config_version = 1
releaseledger_dir = ".releaseledger"
ledger_ref = "main"
ledger_parent_ref = ""
ledger_code = ""
release_template = "default"
""",
        encoding="utf-8",
    )

    data = root / ".releaseledger"
    main = data / "ledgers" / "main"
    (main / "events").mkdir(parents=True)
    (main / "indexes").mkdir(parents=True)

    for i in range(1, releases + 1):
        version = f"{i}.0.0"
        version_dir = main / "releases" / version
        (version_dir / "entries").mkdir(parents=True)
        (version_dir / "audit").mkdir(parents=True)
        (version_dir / "release.md").write_text(
            f"---\nrelease_version: \"{version}\"\nledger_ref: main\n"
            f"status: finalized\n---\n\n# Release {version}\n",
            encoding="utf-8",
        )
        (version_dir / "entries" / "entry-0001.md").write_text(
            f"---\nentry_id: \"0001\"\nrelease_version: \"{version}\"\n"
            f"title: \"Entry for {version}\"\n---\n\nContent.\n",
            encoding="utf-8",
        )

    (main / "events" / "events.jsonl").write_text(
        '{"id": 1, "type": "release_created", "version": "1.0.0"}\n',
        encoding="utf-8",
    )


def _make_canonical_shell(root: Path, *, project_name: str = "test") -> str:
    """Create a canonical Releaseledger shell manually (bypassing init guard).
    Returns the project_uuid.
    """
    project_uuid = str(uuid.uuid4())
    ledger_dir = root / ".ledger"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    manifest = ledger_dir / "ledger.toml"
    manifest.write_text(
        f"schema_version = 3\n\n"
        f"[project]\n"
        f'uuid = "{project_uuid}"\n'
        f'name = "{project_name}"\n\n'
        f"[ledgers.releaseledger.mounts.data]\n"
        f'storage = "project"\n\n'
        f"[ledgers.releaseledger.mounts.indexes]\n"
        f'storage = "cache"\n',
        encoding="utf-8",
    )
    rl_dir = ledger_dir / "releaseledger"
    rl_dir.mkdir(parents=True, exist_ok=True)
    (rl_dir / ".ledger-project.toml").write_text(
        f"schema_version = 1\nlayout_version = 3\n"
        f'project_uuid = "{project_uuid}"\n'
        f'tool = "releaseledger"\nmount = "config"\nstorage = "project"\n',
        encoding="utf-8",
    )
    from releaseledger.storage.config import ProjectConfig, write_project_config

    write_project_config(rl_dir / "config.toml", ProjectConfig())
    data_dir = rl_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / ".ledger-project.toml").write_text(
        f"schema_version = 1\nlayout_version = 3\n"
        f'project_uuid = "{project_uuid}"\n'
        f'tool = "releaseledger"\nmount = "data"\nstorage = "project"\n',
        encoding="utf-8",
    )
    (data_dir / "ledgers" / "main" / "events").mkdir(parents=True)
    (data_dir / "ledgers" / "main" / "releases").mkdir(parents=True)
    return project_uuid


def _run(*args: str, root: Path | None = None, json_output: bool = False):
    cmd_args = []
    if root is not None:
        cmd_args.extend(["--root", str(root)])
    if json_output:
        cmd_args.append("--json")
    cmd_args.extend(args)
    return runner.invoke(app, cmd_args)


def _json(result) -> dict:
    return json.loads(result.output)


class TestCleanupRefusesUnmigratedNonemptyLegacyData:
    """P0: cleanup must refuse when legacy data has not been migrated."""

    def test_cleanup_preview_blocked_by_conservation(self, tmp_path: Path) -> None:
        """Cleanup preview reports cleanup_safe=False for unmigrated legacy data."""
        _make_legacy_project(tmp_path, releases=2)

        # Create canonical shell manually (bypassing init guard)
        _make_canonical_shell(tmp_path)

        # Cleanup preview should report unsafe
        result = _run(
            "migrate", "cleanup", "--dry-run",
            root=tmp_path,
            json_output=True,
        )
        assert result.exit_code == 0, f"Preview failed: {result.output}"
        payload = _json(result)
        assert payload["result"]["cleanup_safe"] is False
        assert "missing_paths" in payload["result"] or "blocked_reason" in payload["result"]

    def test_cleanup_exec_blocked_by_conservation(self, tmp_path: Path) -> None:
        """Cleanup with --yes exits 4 when conservation fails."""
        _make_legacy_project(tmp_path, releases=2)

        # Create canonical shell manually (bypassing init guard)
        _make_canonical_shell(tmp_path)

        # Cleanup with --yes should fail with exit code 4
        result = _run(
            "migrate", "cleanup", "--yes",
            "--reason", "Test cleanup",
            root=tmp_path,
            json_output=True,
        )
        assert result.exit_code == 4, f"Expected exit 4, got {result.exit_code}: {result.output}"
        payload = _json(result)
        assert payload["error"]["code"] == "conflict"

        # Verify legacy files still exist
        assert (tmp_path / ".releaseledger.toml").exists()
        assert (tmp_path / ".releaseledger" / "ledgers" / "main" / "releases" / "1.0.0" / "release.md").exists()
        assert (tmp_path / ".releaseledger" / "ledgers" / "main" / "releases" / "2.0.0" / "release.md").exists()

    def test_status_does_not_recommend_cleanup_for_unmigrated(self, tmp_path: Path) -> None:
        """Status never recommends cleanup when legacy data is unmigrated."""
        _make_legacy_project(tmp_path, releases=1)

        # Create canonical shell manually (bypassing init guard)
        _make_canonical_shell(tmp_path)

        # Status should say migration pending, not cleanup ready
        result = _run("migrate", "status", root=tmp_path, json_output=True)
        assert result.exit_code == 0
        payload = _json(result)
        status = payload["result"]
        assert status["state"] == "canonical-with-legacy-artifacts"
        assert status.get("cleanup_safe") is False
        assert status.get("legacy_relation") == "pending-migration"
        assert status.get("next_action") == "migrate apply"


class TestCleanupAllowsVerifiedCompletedMigration:
    """P0: cleanup is allowed after successful migration with proven conservation."""

    def test_cleanup_safe_after_migration(self, tmp_path: Path) -> None:
        """After successful migration, cleanup reports cleanup_safe=True."""
        _make_legacy_project(tmp_path, releases=1)

        # Migrate first
        result = _run(
            "migrate", "apply", "storage-layout",
            "--reason", "Initial migration",
            root=tmp_path,
        )
        assert result.exit_code == 0, f"Migration failed: {result.output}"

        # Cleanup preview should be safe
        result = _run(
            "migrate", "cleanup", "--dry-run",
            root=tmp_path,
            json_output=True,
        )
        assert result.exit_code == 0
        payload = _json(result)
        assert payload["result"]["cleanup_safe"] is True

    def test_cleanup_removes_legacy_after_migration(self, tmp_path: Path) -> None:
        """After migration, cleanup with --yes removes legacy data."""
        _make_legacy_project(tmp_path, releases=1)

        # Migrate
        result = _run(
            "migrate", "apply", "storage-layout",
            "--reason", "Migration for cleanup test",
            root=tmp_path,
        )
        assert result.exit_code == 0

        # Count canonical releases before cleanup
        canonical_releases = tmp_path / ".ledger" / "releaseledger" / "data" / "ledgers" / "main" / "releases"
        assert canonical_releases.is_dir()
        count = sum(1 for d in canonical_releases.iterdir() if d.is_dir() and (d / "release.md").is_file())
        assert count == 1

        # Cleanup
        result = _run(
            "migrate", "cleanup", "--yes",
            "--reason", "Remove verified legacy data after migration",
            root=tmp_path,
            json_output=True,
        )
        assert result.exit_code == 0, f"Cleanup failed: {result.output}"

        # Legacy should be removed
        assert not (tmp_path / ".releaseledger.toml").exists()
        assert not (tmp_path / ".releaseledger").exists()

        # Canonical should be unchanged
        count_after = sum(1 for d in canonical_releases.iterdir() if d.is_dir() and (d / "release.md").is_file())
        assert count_after == 1
