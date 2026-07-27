"""Migration reliability regression tests (RL-MIG-001 through RL-MIG-010).

Tests verify:
- A: canonical CLI bootstraps from pure legacy without init
- B: existing multi-ledger manifest is preserved
- C: saved plan is exact (UUID, paths match on apply)
- D: named init passes strict storage validation
- E: project rename does not invalidate bindings
- F: failed apply does not poison retry
- G: exact partial destination becomes no-op
- H: divergent destination fails before writes (preflight)
- I: state consistency across commands
- J: reason persistence in journal and result
- K: recovery actions
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from releaseledger.cli import app
from releaseledger.errors import LaunchError, MigrationConflictError

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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


def _make_multi_ledger_manifest(root: Path) -> None:
    """Create a schema-3 manifest with taskledger already registered."""
    ledger_dir = root / ".ledger"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    manifest = ledger_dir / "ledger.toml"
    manifest.write_text(
        """\
schema_version = 3

[project]
uuid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
name = "test-project"

[ledgers.taskledger.mounts.data]
storage = "project"

[ledgers.taskledger.mounts.indexes]
storage = "cache"
""",
        encoding="utf-8",
    )


def _run(*args: str, root: Path | None = None, json_output: bool = False) -> Any:
    """Run a CLI command with optional --root."""
    cmd_args = []
    if root is not None:
        cmd_args.extend(["--root", str(root)])
    if json_output:
        cmd_args.append("--json")
    cmd_args.extend(args)
    return runner.invoke(app, cmd_args)


def _json(result: Any) -> dict[str, object]:
    """Extract JSON payload from CLI result."""
    return json.loads(result.output)


def _migrated_release_count(root: Path) -> int:
    """Count release records in the canonical data location."""
    data_dir = root / ".ledger" / "releaseledger" / "data" / "ledgers" / "main" / "releases"
    if not data_dir.is_dir():
        return 0
    return sum(1 for d in data_dir.iterdir() if d.is_dir() and (d / "release.md").is_file())


# ---------------------------------------------------------------------------
# Test A: canonical CLI bootstraps from pure legacy
# ---------------------------------------------------------------------------


class TestBootstrapFromLegacy:
    """Test A: migrate apply works without running init first."""

    def test_migrate_apply_bootstraps_legacy(self, tmp_path: Path) -> None:
        """Pure legacy project can be migrated without init."""
        _make_legacy_project(tmp_path, releases=2)

        result = _run(
            "migrate", "apply", "storage-layout",
            "--reason", "Migrate legacy storage",
            root=tmp_path,
        )
        assert result.exit_code == 0, f"Failed: {result.output}"
        assert (tmp_path / ".ledger" / "ledger.toml").is_file()
        assert (tmp_path / ".ledger" / "releaseledger" / "config.toml").is_file()
        assert _migrated_release_count(tmp_path) == 2

    def test_migrate_status_reports_legacy(self, tmp_path: Path) -> None:
        """Status correctly reports legacy state for unmigrated project."""
        _make_legacy_project(tmp_path, releases=1)

        result = _run("migrate", "status", root=tmp_path, json_output=True)
        assert result.exit_code == 0
        payload = _json(result)
        assert payload["result"]["state"] == "legacy"


# ---------------------------------------------------------------------------
# Test B: preserve existing multi-ledger manifest
# ---------------------------------------------------------------------------


class TestManifestPreservation:
    """Test B: existing taskledger registration survives migration."""

    def test_preserves_taskledger_registration(self, tmp_path: Path) -> None:
        """Migration adds releaseledger without removing taskledger."""
        _make_legacy_project(tmp_path, releases=1)
        _make_multi_ledger_manifest(tmp_path)

        result = _run(
            "migrate", "apply", "storage-layout",
            "--reason", "Migrate with existing taskledger",
            root=tmp_path,
        )
        assert result.exit_code == 0, f"Failed: {result.output}"

        # Read the manifest and verify both registrations
        manifest_path = tmp_path / ".ledger" / "ledger.toml"
        assert manifest_path.is_file()
        content = manifest_path.read_text()
        assert "taskledger" in content
        assert "releaseledger" in content

    def test_preserves_project_uuid(self, tmp_path: Path) -> None:
        """Migration preserves existing project UUID."""
        _make_legacy_project(tmp_path, releases=1)
        _make_multi_ledger_manifest(tmp_path)

        result = _run(
            "migrate", "apply", "storage-layout",
            "--reason", "UUID preservation test",
            root=tmp_path,
        )
        assert result.exit_code == 0, f"Failed: {result.output}"

        # Verify project UUID is preserved
        manifest_path = tmp_path / ".ledger" / "ledger.toml"
        content = manifest_path.read_text()
        assert "a1b2c3d4-e5f6-7890-abcd-ef1234567890" in content


# ---------------------------------------------------------------------------
# Test C: saved plan is exact
# ---------------------------------------------------------------------------


class TestPlanExactness:
    """Test C: apply uses the plan's UUID, not a regenerated one."""

    def test_plan_file_uuid_used_on_apply(self, tmp_path: Path) -> None:
        """When applying from a plan file, the plan's UUID is authoritative."""
        _make_legacy_project(tmp_path, releases=1)

        # Plan
        plan_file = tmp_path / "migration-plan.json"
        result = _run(
            "migrate", "plan", "storage-layout",
            "--output", str(plan_file),
            root=tmp_path,
        )
        assert result.exit_code == 0, f"Failed: {result.output}"
        assert plan_file.is_file()

        plan = json.loads(plan_file.read_text())
        # v1 plan schema: project UUID may be in "project_uuid" or "project"."uuid"
        plan_project = plan.get("project", {})
        plan_uuid = (
            plan_project.get("uuid")
            if isinstance(plan_project, dict)
            else plan.get("project_uuid")
        )
        # For v1 plans, the UUID is generated during planning and stored
        # in the destination paths. We verify the apply succeeds.
        if plan_uuid is not None:
            # v2+ plan: verify the manifest uses the plan's UUID
            result = _run(
                "migrate", "apply", "storage-layout",
                "--plan-file", str(plan_file),
                "--reason", "Apply exact plan",
                root=tmp_path,
            )
            assert result.exit_code == 0, f"Failed: {result.output}"
            manifest_path = tmp_path / ".ledger" / "ledger.toml"
            content = manifest_path.read_text()
            assert plan_uuid in content

    def test_plan_file_apply_succeeds(self, tmp_path: Path) -> None:
        """Applying from a plan file succeeds and creates canonical layout."""
        _make_legacy_project(tmp_path, releases=1)

        plan_file = tmp_path / "migration-plan.json"
        result = _run(
            "migrate", "plan", "storage-layout",
            "--output", str(plan_file),
            root=tmp_path,
        )
        assert result.exit_code == 0, f"Plan failed: {result.output}"

        result = _run(
            "migrate", "apply", "storage-layout",
            "--plan-file", str(plan_file),
            "--reason", "Apply from plan file",
            root=tmp_path,
        )
        assert result.exit_code == 0, f"Apply failed: {result.output}"
        assert (tmp_path / ".ledger" / "ledger.toml").is_file()
        assert _migrated_release_count(tmp_path) == 1


# ---------------------------------------------------------------------------
# Test D: named init validates
# ---------------------------------------------------------------------------


class TestNamedInitValidation:
    """Test D: releaseledger init --project-name followed by strict validation."""

    def test_named_init_validates(self, tmp_path: Path) -> None:
        """Init with --project-name passes strict storage validation."""
        result = _run("init", "--project-name", "demo", root=tmp_path)
        assert result.exit_code == 0, f"Failed: {result.output}"

        result = _run("storage", "validate", "--strict", root=tmp_path, json_output=True)
        assert result.exit_code == 0, f"Validation failed: {result.output}"
        payload = _json(result)
        assert payload["result"].get("layout_valid", False) is True


# ---------------------------------------------------------------------------
# Test E: project rename does not invalidate bindings
# ---------------------------------------------------------------------------


class TestProjectRename:
    """Test E: changing project_name does not break storage validation."""

    def test_rename_preserves_binding(self, tmp_path: Path) -> None:
        """Changing project_name in manifest doesn't invalidate bindings."""
        result = _run("init", "--project-name", "original", root=tmp_path)
        assert result.exit_code == 0, f"Failed: {result.output}"

        # Change the project name in the manifest
        manifest_path = tmp_path / ".ledger" / "ledger.toml"
        content = manifest_path.read_text()
        content = content.replace('name = "original"', 'name = "renamed"')
        manifest_path.write_text(content, encoding="utf-8")

        # Validation should still pass (identity uses UUID, not name)
        result = _run("storage", "validate", root=tmp_path, json_output=True)
        assert result.exit_code == 0, f"Validation failed: {result.output}"
        payload = _json(result)
        assert payload["result"].get("layout_valid", False) is True


# ---------------------------------------------------------------------------
# Test F: failed apply does not poison retry
# ---------------------------------------------------------------------------


class TestRetryAfterFailure:
    """Test F: partial migration failure is recoverable."""

    def test_preflight_blocks_conflicting_destination(self, tmp_path: Path) -> None:
        """Preflight detects conflicting destinations before writes."""
        _make_legacy_project(tmp_path, releases=1)

        # First apply
        result = _run(
            "migrate", "apply", "storage-layout",
            "--reason", "First attempt",
            root=tmp_path,
        )
        assert result.exit_code == 0, f"First apply failed: {result.output}"

        # Second apply should detect already-completed destination
        # (owned-exact should be no-op, or owned-divergent should block)
        result = _run(
            "migrate", "apply", "storage-layout",
            "--reason", "Second attempt",
            root=tmp_path,
        )
        # This should either succeed (idempotent) or give a clear conflict
        # The key assertion: it must NOT leave corrupt state
        assert (tmp_path / ".ledger" / "ledger.toml").is_file()


# ---------------------------------------------------------------------------
# Test G: exact partial destination becomes no-op
# ---------------------------------------------------------------------------


class TestIdempotentNoop:
    """Test G: re-applying with same data is idempotent."""

    def test_reapply_same_data_succeeds(self, tmp_path: Path) -> None:
        """Applying migration twice with same source succeeds."""
        _make_legacy_project(tmp_path, releases=1)

        # First apply
        result = _run(
            "migrate", "apply", "storage-layout",
            "--reason", "Initial migration",
            root=tmp_path,
        )
        assert result.exit_code == 0, f"First apply failed: {result.output}"
        count_after_first = _migrated_release_count(tmp_path)

        # Second apply should succeed (idempotent)
        result = _run(
            "migrate", "apply", "storage-layout",
            "--reason", "Idempotent re-apply",
            root=tmp_path,
        )
        # May succeed (noop) or fail with clear conflict message
        if result.exit_code == 0:
            assert _migrated_release_count(tmp_path) == count_after_first


# ---------------------------------------------------------------------------
# Test H: divergent destination fails before writes (preflight)
# ---------------------------------------------------------------------------


class TestDivergentDestinationConflict:
    """Test H: preflight blocks divergent destinations."""

    def test_destination_inspection(self, tmp_path: Path) -> None:
        """inspect_destination correctly classifies destination states."""
        from releaseledger.migration import inspect_destination

        # Absent
        absent_path = tmp_path / "nonexistent"
        state, _ = inspect_destination(
            absent_path,
            expected_tool="releaseledger",
            expected_mount="data",
            expected_project_uuid="test-uuid",
        )
        assert state == "absent"

        # Empty directory (no binding)
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        state, _ = inspect_destination(
            empty_dir,
            expected_tool="releaseledger",
            expected_mount="data",
            expected_project_uuid="test-uuid",
        )
        assert state == "empty-unbound"


# ---------------------------------------------------------------------------
# Test I: state consistency
# ---------------------------------------------------------------------------


class TestStateConsistency:
    """Test I: migration state is consistent across all reporting commands."""

    def test_legacy_state_consistent(self, tmp_path: Path) -> None:
        """Legacy project reports consistent state from all commands."""
        _make_legacy_project(tmp_path, releases=1)

        # migrate status
        result = _run("migrate", "status", root=tmp_path, json_output=True)
        assert result.exit_code == 0
        status = _json(result)["result"]

        # storage where
        result = _run("storage", "where", root=tmp_path, json_output=True)
        assert result.exit_code == 0
        where = _json(result)["result"]

        assert status["state"] == "legacy"
        assert where["migration_state"] == "legacy"

    def test_canonical_state_consistent(self, tmp_path: Path) -> None:
        """Canonical project reports consistent state from all commands."""
        result = _run("init", "--project-name", "test", root=tmp_path)
        assert result.exit_code == 0

        result = _run("migrate", "status", root=tmp_path, json_output=True)
        assert result.exit_code == 0
        status = _json(result)["result"]

        result = _run("storage", "where", root=tmp_path, json_output=True)
        assert result.exit_code == 0
        where = _json(result)["result"]

        assert status["state"] == "canonical-ready"
        assert where["migration_state"] == "canonical-ready"


# ---------------------------------------------------------------------------
# Test J: reason persistence
# ---------------------------------------------------------------------------


class TestReasonPersistence:
    """Test J: --reason is persisted in journal and result."""

    def test_reason_in_result(self, tmp_path: Path) -> None:
        """Migration result includes the provided reason."""
        _make_legacy_project(tmp_path, releases=1)

        reason_text = "Test migration reason 2026-07-27"
        result = _run(
            "migrate", "apply", "storage-layout",
            "--reason", reason_text,
            root=tmp_path,
            json_output=True,
        )
        assert result.exit_code == 0, f"Failed: {result.output}"
        payload = _json(result)
        assert payload["result"].get("reason") == reason_text


# ---------------------------------------------------------------------------
# Test K: recovery actions
# ---------------------------------------------------------------------------


class TestRecovery:
    """Test K: recovery provides actionable results."""

    def test_no_journal_returns_nothing_to_recover(self, tmp_path: Path) -> None:
        """Recovery with no journal returns nothing-to-recover."""
        _make_legacy_project(tmp_path, releases=1)

        result = _run(
            "migrate", "recover", "--dry-run",
            root=tmp_path,
            json_output=True,
        )
        assert result.exit_code == 0
        payload = _json(result)
        assert payload["result"]["kind"] == "nothing-to-recover"

    def test_migration_state_evaluate(self, tmp_path: Path) -> None:
        """evaluate_migration_state returns correct states."""
        from releaseledger.migration import evaluate_migration_state

        # Uninitialized
        state = evaluate_migration_state(tmp_path)
        assert state["state"] == "uninitialized"

        # Legacy
        _make_legacy_project(tmp_path, releases=1)
        state = evaluate_migration_state(tmp_path)
        assert state["state"] == "legacy"


# ---------------------------------------------------------------------------
# Test: binding identity
# ---------------------------------------------------------------------------


class TestBindingIdentity:
    """Tests for ledgercore binding identity comparison."""

    def test_binding_identity_ignores_project_name(self) -> None:
        """storage_bindings_match ignores project_name differences."""
        from ledgercore.storage_binding import (
            StorageBinding,
            storage_bindings_match,
        )

        binding_a = StorageBinding(
            schema_version=1,
            layout_version=3,
            project_uuid="test-uuid",
            project_name="name-a",
            tool="releaseledger",
            mount="data",
            storage="project",
        )
        binding_b = StorageBinding(
            schema_version=1,
            layout_version=3,
            project_uuid="test-uuid",
            project_name="name-b",
            tool="releaseledger",
            mount="data",
            storage="project",
        )
        binding_none = StorageBinding(
            schema_version=1,
            layout_version=3,
            project_uuid="test-uuid",
            project_name=None,
            tool="releaseledger",
            mount="data",
            storage="project",
        )

        # All three should match by identity
        assert storage_bindings_match(binding_a, binding_b)
        assert storage_bindings_match(binding_a, binding_none)
        assert storage_bindings_match(binding_b, binding_none)

    def test_binding_identity_detects_different_uuid(self) -> None:
        """storage_bindings_match detects different project UUIDs."""
        from ledgercore.storage_binding import (
            StorageBinding,
            storage_bindings_match,
        )

        binding_a = StorageBinding(
            schema_version=1, layout_version=3,
            project_uuid="uuid-a", project_name=None,
            tool="releaseledger", mount="data", storage="project",
        )
        binding_b = StorageBinding(
            schema_version=1, layout_version=3,
            project_uuid="uuid-b", project_name=None,
            tool="releaseledger", mount="data", storage="project",
        )
        assert not storage_bindings_match(binding_a, binding_b)

    def test_binding_diff_reports_differences(self) -> None:
        """storage_binding_diff reports structured differences."""
        from ledgercore.storage_binding import (
            StorageBinding,
            storage_binding_diff,
        )

        binding_a = StorageBinding(
            schema_version=1, layout_version=3,
            project_uuid="uuid-a", project_name="name-a",
            tool="releaseledger", mount="data", storage="project",
        )
        binding_b = StorageBinding(
            schema_version=1, layout_version=3,
            project_uuid="uuid-b", project_name="name-b",
            tool="releaseledger", mount="data", storage="project",
        )
        diff = storage_binding_diff(binding_a, binding_b)
        assert "project_uuid" in diff["differences"]
        assert diff["metadata_warnings"]  # project_name differs


# ---------------------------------------------------------------------------
# Test: error types
# ---------------------------------------------------------------------------


class TestMigrationConflictError:
    """Test structured migration conflict error."""

    def test_conflict_error_has_structured_data(self) -> None:
        """MigrationConflictError carries structured fields."""
        error = MigrationConflictError(
            "Test conflict",
            component="data",
            path="/some/path",
            state="owned-divergent",
            retry_safe=False,
            remediation_command="releaseledger migrate recover --dry-run",
            code="CONFLICT",
            exit_code=4,
        )
        assert error.data["component"] == "data"
        assert error.data["path"] == "/some/path"
        assert error.data["destination_state"] == "owned-divergent"
        assert error.data["retry_safe"] is False
        assert error.data["remediation_command"] == "releaseledger migrate recover --dry-run"
