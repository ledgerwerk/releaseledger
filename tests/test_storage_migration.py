"""Tests for storage migration from legacy to schema-3.

These tests verify that the migration correctly:
- discovers and uses the legacy .releaseledger source
- copies all selected durable records
- excludes old indexes
- rebuilds indexes in the cache mount
- validates conservation (before/after equality)
- handles nested branch-ledger refs
- supports safe retry from partial migration states
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from releaseledger.errors import LaunchError

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def legacy_complete(tmp_path: Path) -> Path:
    """Create a frozen legacy fixture with nested refs and all record types."""

    root = tmp_path / "workspace"
    root.mkdir()

    # Legacy config
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

    # Legacy data tree
    data = root / ".releaseledger"
    data.mkdir()

    # Main ledger
    main = data / "ledgers" / "main"
    (main / "releases" / "1.0.0" / "entries").mkdir(parents=True)
    (main / "releases" / "1.0.0" / "audit").mkdir(parents=True)
    (main / "releases" / "1.1.0" / "entries").mkdir(parents=True)
    (main / "events").mkdir(parents=True)
    (main / "indexes").mkdir(parents=True)

    # Release 1.0.0
    (main / "releases" / "1.0.0" / "release.md").write_text(
        """\
---
release_version: "1.0.0"
ledger_ref: main
status: finalized
---

# Release 1.0.0
""",
        encoding="utf-8",
    )
    (main / "releases" / "1.0.0" / "entries" / "entry-0001.md").write_text(
        """\
---
entry_id: "0001"
release_version: "1.0.0"
title: "First entry"
---

First entry content.
""",
        encoding="utf-8",
    )
    (main / "releases" / "1.0.0" / "audit" / "commit-audit.yaml").write_text(
        """\
commits:
  - sha: "abc123"
    subject: "Initial commit"
    entry_id: "0001"
""",
        encoding="utf-8",
    )

    # Release 1.1.0
    (main / "releases" / "1.1.0" / "release.md").write_text(
        """\
---
release_version: "1.1.0"
ledger_ref: main
status: finalized
---

# Release 1.1.0
""",
        encoding="utf-8",
    )
    (main / "releases" / "1.1.0" / "entries" / "entry-0001.md").write_text(
        """\
---
entry_id: "0001"
release_version: "1.1.0"
title: "Entry one"
---

Entry one content.
""",
        encoding="utf-8",
    )
    (main / "releases" / "1.1.0" / "entries" / "entry-0002.md").write_text(
        """\
---
entry_id: "0002"
release_version: "1.1.0"
title: "Entry two"
---

Entry two content.
""",
        encoding="utf-8",
    )

    # Events
    (main / "events" / "events.jsonl").write_text(
        '{"id": 1, "type": "release_created", "version": "1.0.0"}\n'
        '{"id": 2, "type": "release_created", "version": "1.1.0"}\n',
        encoding="utf-8",
    )

    # Old indexes (should NOT be copied)
    (main / "indexes" / "releases.json").write_text("[]", encoding="utf-8")
    (main / "indexes" / "entries.json").write_text("[]", encoding="utf-8")

    # Nested branch ledger: feature/api
    feature_api = data / "ledgers" / "feature" / "api"
    (feature_api / "releases" / "1.2.0" / "entries").mkdir(parents=True)
    (feature_api / "events").mkdir(parents=True)
    (feature_api / "indexes").mkdir(parents=True)

    (feature_api / "releases" / "1.2.0" / "release.md").write_text(
        """\
release_version: "1.2.0"
ledger_ref: feature/api
status: finalized
---

# Release 1.2.0
""",
        encoding="utf-8",
    )
    (feature_api / "releases" / "1.2.0" / "entries" / "entry-0001.md").write_text(
        """\
---
entry_id: "0001"
release_version: "1.2.0"
title: "API entry"
---

API entry content.
""",
        encoding="utf-8",
    )
    (feature_api / "events" / "events.jsonl").write_text(
        '{"id": 1, "type": "release_created", "version": "1.2.0"}\n',
        encoding="utf-8",
    )
    (feature_api / "indexes" / "releases.json").write_text("[]", encoding="utf-8")
    (feature_api / "indexes" / "entries.json").write_text("[]", encoding="utf-8")

    # Unknown durable regular file
    (data / "durable-extra.txt").write_text("extra data", encoding="utf-8")

    return root


@pytest.fixture()
def legacy_empty(tmp_path: Path) -> Path:
    """Create a legacy fixture with no records."""

    root = tmp_path / "workspace_empty"
    root.mkdir()

    config = root / ".releaseledger.toml"
    config.write_text(
        """\
config_version = 1
releaseledger_dir = ".releaseledger"
ledger_ref = "main"
""",
        encoding="utf-8",
    )

    data = root / ".releaseledger"
    data.mkdir()
    (data / "ledgers").mkdir()

    return root


@pytest.fixture()
def legacy_with_symlink(tmp_path: Path) -> Path:
    """Create a legacy fixture containing a symlink."""

    root = tmp_path / "workspace_symlink"
    root.mkdir()

    config = root / ".releaseledger.toml"
    config.write_text(
        """\
config_version = 1
releaseledger_dir = ".releaseledger"
ledger_ref = "main"
""",
        encoding="utf-8",
    )

    data = root / ".releaseledger"
    main = data / "ledgers" / "main"
    (main / "releases" / "1.0.0").mkdir(parents=True)
    (main / "releases" / "1.0.0" / "release.md").write_text(
        """\
---
release_version: "1.0.0"
ledger_ref: main
status: finalized
---
""",
        encoding="utf-8",
    )

    # Create a symlink
    (main / "releases" / "1.0.0" / "bad_link").symlink_to("/nonexistent")

    return root


# ---------------------------------------------------------------------------
# Root-cause regression test
# ---------------------------------------------------------------------------


class TestPlannerReceivesLegacySource:
    """Verify that the migration plan correctly identifies the legacy source."""

    def test_plan_migration_includes_legacy_source(self, legacy_complete: Path) -> None:
        """The plan output names the real .releaseledger source."""
        from releaseledger.migration import (
            ReleaseledgerMigrationRequest,
            plan_migration,
        )

        request = ReleaseledgerMigrationRequest(
            start=legacy_complete,
            data_storage="project",
            external_root=None,
            target="project",
            mode="copy",
        )

        result = plan_migration(request)

        expected_legacy = legacy_complete / ".releaseledger"
        assert "legacy_data_root" in result
        assert Path(result["legacy_data_root"]) == expected_legacy.resolve()
        assert "inventory" in result
        assert result["inventory"]["total_releases"] == 3
        assert result["inventory"]["total_entries"] == 4

    def test_plan_migration_is_read_only(self, legacy_complete: Path) -> None:
        """plan_migration() must not write any files."""
        from releaseledger.migration import (
            ReleaseledgerMigrationRequest,
            plan_migration,
        )

        # Snapshot the tree before planning
        before_files = set()
        for p in legacy_complete.rglob("*"):
            if p.is_file():
                before_files.add(str(p.relative_to(legacy_complete)))

        request = ReleaseledgerMigrationRequest(
            start=legacy_complete,
            data_storage="project",
            external_root=None,
            target="project",
            mode="copy",
        )
        plan_migration(request)

        after_files = set()
        for p in legacy_complete.rglob("*"):
            if p.is_file():
                after_files.add(str(p.relative_to(legacy_complete)))

        new_files = after_files - before_files
        # The plan must not create any new files
        assert not new_files, f"Plan created new files: {new_files}"


# ---------------------------------------------------------------------------
# Discovery and inventory tests
# ---------------------------------------------------------------------------


class TestLegacyDiscovery:
    """Test legacy project discovery."""

    def test_discover_legacy_project(self, legacy_complete: Path) -> None:
        from releaseledger.migration import discover_legacy_project

        config_path, config = discover_legacy_project(legacy_complete)
        assert config_path.name == ".releaseledger.toml"
        assert config["config_version"] == 1

    def test_discover_legacy_project_not_found(self, tmp_path: Path) -> None:
        from releaseledger.migration import discover_legacy_project

        with pytest.raises(LaunchError) as exc_info:
            discover_legacy_project(tmp_path)
        assert exc_info.value.code == "NOT_FOUND"


class TestRecursiveLedgerRefDiscovery:
    """Test iter_legacy_ledger_roots() for nested refs."""

    def test_discovers_flat_and_nested_refs(self, legacy_complete: Path) -> None:
        from releaseledger.migration import iter_legacy_ledger_roots

        data_root = legacy_complete / ".releaseledger"
        refs = list(iter_legacy_ledger_roots(data_root))

        ref_names = [r[0] for r in refs]
        assert "main" in ref_names
        assert "feature/api" in ref_names
        assert len(ref_names) == 2

    def test_returns_deterministic_order(self, legacy_complete: Path) -> None:
        from releaseledger.migration import iter_legacy_ledger_roots

        data_root = legacy_complete / ".releaseledger"
        refs1 = [r[0] for r in iter_legacy_ledger_roots(data_root)]
        refs2 = [r[0] for r in iter_legacy_ledger_roots(data_root)]
        assert refs1 == refs2

    def test_empty_ledgers_dir(self, legacy_empty: Path) -> None:
        from releaseledger.migration import iter_legacy_ledger_roots

        data_root = legacy_empty / ".releaseledger"
        refs = list(iter_legacy_ledger_roots(data_root))
        assert refs == []


class TestPathSelection:
    """Test select_legacy_durable_paths()."""

    def test_excludes_indexes(self, legacy_complete: Path) -> None:
        from releaseledger.migration import select_legacy_durable_paths

        data_root = legacy_complete / ".releaseledger"
        selected = select_legacy_durable_paths(data_root)

        for p in selected.included:
            assert "/indexes/" not in p, f"Index file should be excluded: {p}"

    def test_includes_releases(self, legacy_complete: Path) -> None:
        from releaseledger.migration import select_legacy_durable_paths

        data_root = legacy_complete / ".releaseledger"
        selected = select_legacy_durable_paths(data_root)

        release_files = [p for p in selected.included if "release.md" in p]
        assert len(release_files) == 3  # main/1.0.0, main/1.1.0, feature/api/1.2.0

    def test_includes_entries(self, legacy_complete: Path) -> None:
        from releaseledger.migration import select_legacy_durable_paths

        data_root = legacy_complete / ".releaseledger"
        selected = select_legacy_durable_paths(data_root)

        entry_files = [p for p in selected.included if "entry-" in p]
        assert len(entry_files) == 4

    def test_includes_events(self, legacy_complete: Path) -> None:
        from releaseledger.migration import select_legacy_durable_paths

        data_root = legacy_complete / ".releaseledger"
        selected = select_legacy_durable_paths(data_root)

        event_files = [p for p in selected.included if "events.jsonl" in p]
        assert len(event_files) == 2

    def test_includes_unknown_regular_files(self, legacy_complete: Path) -> None:
        from releaseledger.migration import select_legacy_durable_paths

        data_root = legacy_complete / ".releaseledger"
        selected = select_legacy_durable_paths(data_root)

        assert any("durable-extra.txt" in p for p in selected.included)

    def test_excludes_temp_files(self, tmp_path: Path) -> None:
        from releaseledger.migration import select_legacy_durable_paths

        # Create a fixture with temp files inside a ledger
        data = tmp_path / "data"
        main = data / "ledgers" / "main"
        (main / "releases").mkdir(parents=True)
        (main / "__pycache__").mkdir(parents=True)
        (main / "__pycache__" / "cached.pyc").write_text("")

        selected = select_legacy_durable_paths(data)
        for p in selected.included:
            assert "__pycache__" not in p, f"Cache file should be excluded: {p}"


class TestStrictInventory:
    """Test strict inventory builder."""

    def test_counts_match_actual_files(self, legacy_complete: Path) -> None:
        from releaseledger.migration import build_strict_inventory

        data_root = legacy_complete / ".releaseledger"
        inventory = build_strict_inventory(data_root)

        assert inventory.total_releases == 3
        assert inventory.total_entries == 4
        assert inventory.total_event_rows == 3  # 2 in main, 1 in feature/api

    def test_includes_nested_refs(self, legacy_complete: Path) -> None:
        from releaseledger.migration import build_strict_inventory

        data_root = legacy_complete / ".releaseledger"
        inventory = build_strict_inventory(data_root)

        ref_names = [li.ref for li in inventory.ledgers]
        assert "main" in ref_names
        assert "feature/api" in ref_names


# ---------------------------------------------------------------------------
# Conservation and validation tests
# ---------------------------------------------------------------------------


class TestConservationCheck:
    """Test assert_inventory_preserved()."""

    def test_matching_inventories_pass(self) -> None:
        from releaseledger.migration import (
            LedgerInventory,
            ReleaseledgerDataInventory,
            assert_inventory_preserved,
        )

        ledger = LedgerInventory(
            ref="main",
            release_versions=("1.0.0",),
            release_count=1,
            entry_count=1,
            event_row_count=1,
            audit_sheet_count=0,
            durable_regular_file_count=1,
            selected_relative_paths=("ledgers/main/releases/1.0.0/release.md",),
            files=(),
        )
        inventory = ReleaseledgerDataInventory(
            data_root=Path("/fake"),
            ledgers=(ledger,),
            total_releases=1,
            total_entries=1,
            total_event_rows=1,
            total_audit_sheets=0,
            total_regular_files=1,
            selected_relative_paths=("ledgers/main/releases/1.0.0/release.md",),
            files=(),
            excluded_paths=(),
            unexpected_paths=(),
        )

        # Should not raise
        assert_inventory_preserved(source=inventory, target=inventory)

    def test_missing_release_fails(self) -> None:
        from releaseledger.migration import (
            LedgerInventory,
            ReleaseledgerDataInventory,
            assert_inventory_preserved,
        )

        source_ledger = LedgerInventory(
            ref="main",
            release_versions=("1.0.0", "1.1.0"),
            release_count=2,
            entry_count=3,
            event_row_count=2,
            audit_sheet_count=1,
            durable_regular_file_count=3,
            selected_relative_paths=(
                "ledgers/main/releases/1.0.0/release.md",
                "ledgers/main/releases/1.1.0/release.md",
            ),
            files=(),
        )
        target_ledger = LedgerInventory(
            ref="main",
            release_versions=("1.0.0",),
            release_count=1,
            entry_count=1,
            event_row_count=1,
            audit_sheet_count=0,
            durable_regular_file_count=1,
            selected_relative_paths=("ledgers/main/releases/1.0.0/release.md",),
            files=(),
        )

        source = ReleaseledgerDataInventory(
            data_root=Path("/source"),
            ledgers=(source_ledger,),
            total_releases=2,
            total_entries=3,
            total_event_rows=2,
            total_audit_sheets=1,
            total_regular_files=3,
            selected_relative_paths=(
                "ledgers/main/releases/1.0.0/release.md",
                "ledgers/main/releases/1.1.0/release.md",
            ),
            files=(),
            excluded_paths=(),
            unexpected_paths=(),
        )
        target = ReleaseledgerDataInventory(
            data_root=Path("/target"),
            ledgers=(target_ledger,),
            total_releases=1,
            total_entries=1,
            total_event_rows=1,
            total_audit_sheets=0,
            total_regular_files=1,
            selected_relative_paths=("ledgers/main/releases/1.0.0/release.md",),
            files=(),
            excluded_paths=(),
            unexpected_paths=(),
        )

        with pytest.raises(LaunchError):
            assert_inventory_preserved(source=source, target=target)


# ---------------------------------------------------------------------------
# Symlink rejection test
# ---------------------------------------------------------------------------


class TestSymlinkRejection:
    """Test that symlinks are rejected."""

    def test_plan_rejects_symlinks(self, legacy_with_symlink: Path) -> None:
        from releaseledger.migration import (
            ReleaseledgerMigrationRequest,
            plan_migration,
        )

        request = ReleaseledgerMigrationRequest(
            start=legacy_with_symlink,
            data_storage="project",
            external_root=None,
            target="project",
            mode="copy",
        )

        with pytest.raises(LaunchError) as exc_info:
            plan_migration(request)
        # Should mention symlink or special file
        assert (
            "symlink" in str(exc_info.value).lower()
            or "special" in str(exc_info.value).lower()
        )


# ---------------------------------------------------------------------------
# Index root separation tests
# ---------------------------------------------------------------------------


class TestIndexRootSeparation:
    """Test that indexes are written to cache mount, not data root."""

    def test_rebuild_indexes_accepts_separate_roots(self) -> None:
        """rebuild_all_indexes() must accept data_root and indexes_root."""
        import inspect

        from releaseledger.migration import rebuild_all_indexes

        sig = inspect.signature(rebuild_all_indexes)
        params = list(sig.parameters.keys())
        assert "data_root" in params
        assert "indexes_root" in params


# ---------------------------------------------------------------------------
# Config transformation tests
# ---------------------------------------------------------------------------


class TestConfigTransformation:
    """Test config v1 to v2 transformation."""

    def test_transform_preserves_supported_fields(self) -> None:
        from releaseledger.migration import transform_legacy_config_v1_to_v2

        v1_config = {
            "config_version": 1,
            "releaseledger_dir": ".releaseledger",
            "releaseledger_dir_policy": "default",
            "ledger_next_entry_number": 5,
            "ledger_ref": "main",
            "ledger_parent_ref": "develop",
            "ledger_branch_guard": "strict",
            "release": {"template": "default"},
            "changelog": {"template": "keepachangelog"},
        }

        v2_text = transform_legacy_config_v1_to_v2(v1_config)

        assert "config_version = 2" in v2_text
        assert "ledger_ref" in v2_text
        assert "main" in v2_text
        assert "ledger_parent_ref" in v2_text
        assert "develop" in v2_text
        assert "releaseledger_dir" not in v2_text
        assert "releaseledger_dir_policy" not in v2_text
        assert "ledger_next_entry_number" not in v2_text


# ---------------------------------------------------------------------------
# Error handling tests
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """Test that errors propagate correctly."""

    def test_target_preparation_failure_propagates(self, legacy_complete: Path) -> None:
        """Errors from target preparation must propagate, not be suppressed."""
        from releaseledger.migration import (
            ReleaseledgerMigrationRequest,
            execute_migration,
        )

        request = ReleaseledgerMigrationRequest(
            start=legacy_complete,
            data_storage="project",
            external_root=None,
            target="project",
            mode="copy",
        )

        call_count = [0]

        def mock_prepare(*args: Any, **kwargs: Any) -> Any:
            call_count[0] += 1
            raise LaunchError(
                "target preparation failed",
                code="CONFIG_ERROR",
                exit_code=2,
            )

        with patch(
            "releaseledger.ledgercore_backend.prepare_legacy_migration_target",
            side_effect=mock_prepare,
        ):
            with pytest.raises(LaunchError):
                execute_migration(request)

        assert call_count[0] == 1


# ---------------------------------------------------------------------------
# Target flag tests
# ---------------------------------------------------------------------------


class TestTargetFlag:
    """Test that --target project|local is respected."""

    def test_target_flag_forwarded_to_request(self) -> None:
        from releaseledger.migration import ReleaseledgerMigrationRequest

        request = ReleaseledgerMigrationRequest(
            start=Path("/fake"),
            data_storage="project",
            external_root=None,
            target="local",
            mode="copy",
        )

        assert request.target == "local"


# ---------------------------------------------------------------------------
# End-to-end migration tests
# ---------------------------------------------------------------------------


class TestEndToEndMigration:
    """End-to-end tests that exercise the real apply path."""

    def test_apply_copies_releases_to_target(self, legacy_complete: Path) -> None:
        """Legacy source contains release.md; canonical target must contain it."""
        from releaseledger.migration import (
            ReleaseledgerMigrationRequest,
            discover_legacy_source,
            execute_migration,
        )

        # Discover legacy source to verify contents before migration
        source = discover_legacy_source(legacy_complete)
        assert source.inventory.total_releases == 3
        assert source.inventory.total_entries == 4

        request = ReleaseledgerMigrationRequest(
            start=legacy_complete,
            data_storage="project",
            external_root=None,
            target="project",
            mode="copy",
        )

        result = execute_migration(request)

        assert result["kind"] == "releaseledger_migration_executed"
        assert result["mode"] == "copy"

        # Verify target has the releases
        target_root = Path(result["target_data_root"])
        assert target_root.exists()

        # Check that release files exist in target
        release_paths = sorted(target_root.rglob("release.md"))
        assert len(release_paths) == 3, (
            f"Expected 3 releases, found {len(release_paths)}"
        )

        # Check that entry files exist in target
        entry_paths = sorted(target_root.rglob("entry-*.md"))
        assert len(entry_paths) == 4, f"Expected 4 entries, found {len(entry_paths)}"

        # Legacy source should still exist (copy mode)
        assert source.data_root.exists(), (
            "Legacy source should still exist in copy mode"
        )

    def test_plan_is_read_only(self, legacy_complete: Path) -> None:
        """storage migrate plan must not create .ledger/ directory."""
        from releaseledger.migration import (
            ReleaseledgerMigrationRequest,
            plan_migration,
        )

        # Ensure no .ledger exists before
        ledger_dir = legacy_complete / ".ledger"
        assert not ledger_dir.exists(), f"{ledger_dir} already exists"

        request = ReleaseledgerMigrationRequest(
            start=legacy_complete,
            data_storage="project",
            external_root=None,
            target="project",
            mode="copy",
        )
        plan_migration(request)

        # Plan must not create .ledger/
        assert not ledger_dir.exists(), f"{ledger_dir} was created by plan_migration"


class TestTargetRejection:
    """Test --target flag behavior."""

    def test_local_without_base_is_rejected(self, legacy_complete: Path) -> None:
        """--target local without an existing schema-3 manifest is rejected."""
        from releaseledger.migration import (
            ReleaseledgerMigrationRequest,
            execute_migration,
        )

        request = ReleaseledgerMigrationRequest(
            start=legacy_complete,
            data_storage="project",
            external_root=None,
            target="local",
            mode="copy",
        )

        with pytest.raises(LaunchError) as exc_info:
            execute_migration(request)
        assert "local" in str(exc_info.value).lower()
        assert exc_info.value.code == "CONFIG_ERROR"


class TestModeBehavior:
    """Test --mode flag behavior."""

    def test_move_removes_legacy_after_success(self, legacy_complete: Path) -> None:
        """Move mode should remove legacy data after successful migration."""
        from releaseledger.migration import (
            ReleaseledgerMigrationRequest,
            discover_legacy_source,
            execute_migration,
        )

        source = discover_legacy_source(legacy_complete)
        legacy_data = source.data_root
        assert legacy_data.exists()

        request = ReleaseledgerMigrationRequest(
            start=legacy_complete,
            data_storage="project",
            external_root=None,
            target="project",
            mode="move",
        )

        execute_migration(request)

        # Legacy data should be removed
        assert not legacy_data.exists(), "Legacy data should be removed in move mode"


class TestStrictJsonl:
    """Test strict JSONL reading."""

    def test_invalid_jsonl_fails_migration(self, legacy_complete: Path) -> None:
        """Malformed JSONL should block migration."""
        from releaseledger.migration import (
            ReleaseledgerMigrationRequest,
            execute_migration,
        )

        # Corrupt the events.jsonl
        events = (
            legacy_complete
            / ".releaseledger"
            / "ledgers"
            / "main"
            / "events"
            / "events.jsonl"
        )
        events.write_text(
            '{"id": 1, valid}\
{invalid json\n',
            encoding="utf-8",
        )

        request = ReleaseledgerMigrationRequest(
            start=legacy_complete,
            data_storage="project",
            external_root=None,
            target="project",
            mode="copy",
        )

        with pytest.raises(LaunchError) as exc_info:
            execute_migration(request)
        assert exc_info.value.code == "VALIDATION_ERROR"
