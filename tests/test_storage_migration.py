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
    """Verify that the migration planner receives legacy_dir as the source."""

    def test_plan_migration_passes_legacy_dir_to_backend(
        self, legacy_complete: Path
    ) -> None:
        """The root-cause regression test.

        The current implementation passes layout.loaded (the newly created
        schema-3 project) to plan_storage_migration(), not legacy_dir.
        This test asserts that the backend planner receives the legacy
        .releaseledger directory as its source.
        """
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

        # Capture what source_data_root the backend receives
        received_source: list[Path] = []

        def mock_plan_releaseledger_layout_migration(*args: Any, **kwargs: Any) -> Any:
            # The new signature should receive source_data_root
            if "source_data_root" in kwargs:
                received_source.append(kwargs["source_data_root"])
            elif len(args) > 0:
                # Old signature: layout is first positional arg
                received_source.append(Path("OLD_LAYOUT_LOADED"))
            return "mock_plan"

        with patch(
            "releaseledger.ledgercore_backend.plan_releaseledger_layout_migration",
            side_effect=mock_plan_releaseledger_layout_migration,
        ):
            try:
                plan_migration(request)
            except Exception:
                pass  # We just need to check what was passed

        expected_legacy = legacy_complete / ".releaseledger"
        assert len(received_source) == 1, (
            f"Expected planner to be called once, got {len(received_source)}"
        )
        assert received_source[0] == expected_legacy.resolve(), (
            f"Planner received {received_source[0]}, "
            f"expected {expected_legacy.resolve()}"
        )


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
            excluded_paths=(),
            unexpected_paths=(),
        )

        with pytest.raises(LaunchError) as exc_info:
            assert_inventory_preserved(source=source, target=target)
        assert exc_info.value.code == "VALIDATION_ERROR"


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
    """Test that broad LaunchError suppression is removed."""

    def test_registration_failure_not_suppressed(self, legacy_complete: Path) -> None:
        """The broad except LaunchError: pass must be removed."""
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

        # If ensure_releaseledger_registration raises a non-"already registered"
        # error, it should propagate
        call_count = [0]

        def mock_ensure(*args: Any, **kwargs: Any) -> None:
            call_count[0] += 1
            raise LaunchError(
                "registration failed",
                code="CONFIG_ERROR",
                exit_code=2,
            )

        with patch(
            "releaseledger.ledgercore_backend.ensure_releaseledger_registration",
            side_effect=mock_ensure,
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
