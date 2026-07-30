"""Tests for releaseledger init with existing shared Ledgercore manifests.

Covers the audiosig initialization defect:
- Defect 1: missing registration treated as invalid manifest
- Defect 2: manifest-only registration falsely idempotent
- Defect 3: force can change storage ownership
"""

from __future__ import annotations

from pathlib import Path

import pytest

from releaseledger.errors import LaunchError
from releaseledger.storage import paths


@pytest.fixture()
def isolated_user_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    # Windows platformdirs ignores XDG vars; override the actual env vars.
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))


# Original pre-init audiosig manifest (no Releaseledger registration)
AUDIOSIG_MANIFEST = """\
schema_version = 3

[project]
uuid = "685a04ef-b8a0-4b14-a53c-b405c10547a2"
name = "audiosig"

[ledgers.taskledger.mounts.data]
storage = "external"
root = "../ledger"

[ledgers.taskledger.mounts.indexes]
storage = "cache"

[ledgers.documentledger.mounts.data]
storage = "project"

[ledgers.documentledger.mounts.artifacts]
storage = "cache"
"""

# Supplied manifest-only Releaseledger registration (no config/data dirs)
MANIFEST_ONLY_RELEASELEDGER = """\
schema_version = 3

[project]
uuid = "685a04ef-b8a0-4b14-a53c-b405c10547a2"
name = "audiosig"

[ledgers.taskledger.mounts.data]
storage = "external"
root = "../ledger"

[ledgers.taskledger.mounts.indexes]
storage = "cache"

[ledgers.documentledger.mounts.data]
storage = "project"

[ledgers.documentledger.mounts.artifacts]
storage = "cache"

[ledgers.releaseledger.mounts.data]
storage = "project"

[ledgers.releaseledger.mounts.indexes]
storage = "cache"
"""


def _write_manifest(project_dir: Path, content: str) -> None:
    ledger_dir = project_dir / ".ledger"
    ledger_dir.mkdir(exist_ok=True)
    (ledger_dir / "ledger.toml").write_text(content)


def test_init_registers_releaseledger_in_existing_shared_manifest(
    tmp_path: Path, isolated_user_roots: None
) -> None:
    """Test 1: Add Releaseledger to an existing shared manifest."""
    proj = tmp_path / "audiosig"
    proj.mkdir()
    _write_manifest(proj, AUDIOSIG_MANIFEST)

    result = paths.ensure_canonical_project(proj)

    # Exit code is zero (no exception)
    assert result["kind"] == "project_init"
    assert result["mode"] == "registered"

    # Releaseledger registration exists
    from ledgercore.tomlio import read_ledger_manifest

    from releaseledger.ledgercore_backend import TOOL_NAME

    manifest_path = proj / ".ledger" / "ledger.toml"
    manifest = read_ledger_manifest(manifest_path)
    assert TOOL_NAME in manifest.ledgers

    # taskledger registration is unchanged
    assert "taskledger" in manifest.ledgers
    task_reg = manifest.ledgers["taskledger"]
    assert task_reg.mounts["data"].storage == "external"
    assert task_reg.mounts["data"].external_root == "../ledger"

    # documentledger registration is unchanged
    assert "documentledger" in manifest.ledgers
    doc_reg = manifest.ledgers["documentledger"]
    assert doc_reg.mounts["data"].storage == "project"

    # project UUID remains unchanged
    assert manifest.project_uuid == "685a04ef-b8a0-4b14-a53c-b405c10547a2"

    # project name remains unchanged
    assert manifest.project_name == "audiosig"

    # config is created
    config_path = proj / ".ledger" / "releaseledger" / "config.toml"
    assert config_path.is_file()

    # data and indexes bindings are created
    data_root = proj / ".ledger" / "releaseledger" / "data"
    assert (data_root / ".ledger-project.toml").is_file()

    # domain directories exist
    ledger_dir = data_root / "ledgers" / "main"
    assert ledger_dir.is_dir()
    assert (ledger_dir / "releases").is_dir()
    assert (ledger_dir / "events").is_dir()


def test_init_completes_manifest_only_releaseledger_registration(
    tmp_path: Path, isolated_user_roots: None
) -> None:
    """Test 2: Complete a manifest-only Releaseledger registration."""
    proj = tmp_path / "audiosig"
    proj.mkdir()
    _write_manifest(proj, MANIFEST_ONLY_RELEASELEDGER)

    # No .ledger/releaseledger/ directory exists yet
    releaseledger_dir = proj / ".ledger" / "releaseledger"
    assert not releaseledger_dir.exists()

    result = paths.ensure_canonical_project(proj)

    # initialization writes the missing config and bindings
    assert result["kind"] == "project_init"
    assert result["mode"] == "repaired"

    # result is not idempotent
    assert result.get("manifest_changed") is False  # registration already existed

    # config is created
    config_path = proj / ".ledger" / "releaseledger" / "config.toml"
    assert config_path.is_file()

    # data and indexes bindings are created
    data_root = proj / ".ledger" / "releaseledger" / "data"
    assert (data_root / ".ledger-project.toml").is_file()


def test_init_second_run_is_idempotent(
    tmp_path: Path, isolated_user_roots: None
) -> None:
    """Test 3: Second run is a true no-op."""
    proj = tmp_path / "proj"
    proj.mkdir()
    paths.ensure_canonical_project(proj, project_name="demo")

    # Read manifest bytes before second run
    manifest_path = proj / ".ledger" / "ledger.toml"
    manifest_before = manifest_path.read_bytes()

    # Read config bytes before second run
    config_path = proj / ".ledger" / "releaseledger" / "config.toml"
    config_before = config_path.read_bytes()

    result = paths.ensure_canonical_project(proj)

    # result is project_init_idempotent
    assert result["kind"] == "project_init_idempotent"
    assert result["mode"] == "unchanged"

    # manifest bytes are unchanged
    assert manifest_path.read_bytes() == manifest_before

    # config bytes are unchanged
    assert config_path.read_bytes() == config_before

    # no backup is created
    backup_path = config_path.with_suffix(".toml.bak")
    assert not backup_path.exists()

    # no storage setting changes
    assert result["manifest_changed"] is False
    assert result["config_created"] is False
    assert result["bindings_created"] == []


def test_init_rejects_malformed_existing_manifest(
    tmp_path: Path, isolated_user_roots: None
) -> None:
    """Test 4: Malformed manifest remains an error."""
    proj = tmp_path / "proj"
    proj.mkdir()
    ledger_dir = proj / ".ledger"
    ledger_dir.mkdir()
    (ledger_dir / "ledger.toml").write_text("not a manifest = [")

    with pytest.raises(LaunchError) as exc_info:
        paths.ensure_canonical_project(proj)

    # nonzero exit
    assert exc_info.value.exit_code != 0

    # CONFIG_ERROR or CONFLICT code
    assert exc_info.value.code in {"CONFLICT", "CONFIG_ERROR"}

    # no Releaseledger directory is created
    assert not (proj / ".ledger" / "releaseledger").exists()

    # existing manifest bytes remain unchanged
    assert (ledger_dir / "ledger.toml").read_text() == "not a manifest = ["


def test_init_shared_manifest_still_refuses_unmigrated_legacy_data(
    tmp_path: Path, isolated_user_roots: None
) -> None:
    """Test 5: Legacy data guard applies to shared manifests."""
    proj = tmp_path / "audiosig"
    proj.mkdir()
    _write_manifest(proj, AUDIOSIG_MANIFEST)

    # Create nonempty legacy .releaseledger data
    legacy_dir = proj / ".releaseledger"
    legacy_dir.mkdir()
    (legacy_dir / "releases.json").write_text("[]")

    # Create legacy config
    (proj / ".releaseledger.toml").write_text('releaseledger_dir = ".releaseledger"\n')

    with pytest.raises(LaunchError) as exc_info:
        paths.ensure_canonical_project(proj)

    # initialization refuses with migration remediation
    assert exc_info.value.code == "CONFLICT"
    assert exc_info.value.exit_code == 4
    assert any("migrate" in r.lower() for r in exc_info.value.remediation)

    # no registration is added
    from ledgercore.tomlio import read_ledger_manifest

    from releaseledger.ledgercore_backend import TOOL_NAME

    manifest = read_ledger_manifest(proj / ".ledger" / "ledger.toml")
    assert TOOL_NAME not in manifest.ledgers


def test_init_force_preserves_existing_external_data_mount(
    tmp_path: Path, isolated_user_roots: None
) -> None:
    """Test 6: Force never retargets storage."""
    proj = tmp_path / "proj"
    proj.mkdir()

    # First, initialize with external storage
    paths.ensure_canonical_project(
        proj,
        project_name="demo",
        data_storage="external",
        external_root="../shared-release-data",
    )

    # Verify external storage is set
    from ledgercore.tomlio import read_ledger_manifest

    from releaseledger.ledgercore_backend import TOOL_NAME

    manifest_path = proj / ".ledger" / "ledger.toml"
    manifest = read_ledger_manifest(manifest_path)
    data_mount = manifest.ledgers[TOOL_NAME].mounts["data"]
    assert data_mount.storage == "external"
    assert data_mount.external_root == "../shared-release-data"

    # Now run init with force_config (which should replace config but not storage)
    paths.ensure_canonical_project(proj, force=True)

    # external storage and root remain unchanged
    manifest = read_ledger_manifest(manifest_path)
    data_mount = manifest.ledgers[TOOL_NAME].mounts["data"]
    assert data_mount.storage == "external"
    assert data_mount.external_root == "../shared-release-data"


def test_init_force_config_creates_backup(
    tmp_path: Path, isolated_user_roots: None
) -> None:
    """Test that --force-config backs up existing config."""
    proj = tmp_path / "proj"
    proj.mkdir()
    paths.ensure_canonical_project(proj, project_name="demo")

    config_path = proj / ".ledger" / "releaseledger" / "config.toml"
    original_content = config_path.read_bytes()

    result = paths.ensure_canonical_project(proj, force_config=True)

    # backup is created
    backup_path = config_path.with_suffix(".toml.bak")
    assert backup_path.is_file()
    assert backup_path.read_bytes() == original_content

    # result shows it was repaired
    assert result["kind"] == "project_init"
    assert result["config_created"] is True


def test_init_preserves_existing_manifest_comments(
    tmp_path: Path, isolated_user_roots: None
) -> None:
    """Test that existing manifest comments survive registration addition."""
    proj = tmp_path / "audiosig"
    proj.mkdir()
    manifest_with_comments = """\
schema_version = 3

[project]
uuid = "685a04ef-b8a0-4b14-a53c-b405c10547a2"
name = "audiosig"

# Task ledger configuration
[ledgers.taskledger.mounts.data]
storage = "external"
root = "../ledger"

[ledgers.taskledger.mounts.indexes]
storage = "cache"
"""
    _write_manifest(proj, manifest_with_comments)

    paths.ensure_canonical_project(proj)

    # Read back the manifest and verify comments are preserved
    manifest_path = proj / ".ledger" / "ledger.toml"
    content = manifest_path.read_text()
    assert "# Task ledger configuration" in content


def test_init_accepts_external_data_storage_for_new_registration(
    tmp_path: Path, isolated_user_roots: None
) -> None:
    """Test that --data-storage external is accepted when adding registration."""
    proj = tmp_path / "audiosig"
    proj.mkdir()
    _write_manifest(proj, AUDIOSIG_MANIFEST)

    result = paths.ensure_canonical_project(
        proj,
        data_storage="external",
        external_root="../shared-release-data",
    )

    assert result["kind"] == "project_init"
    assert result["mode"] == "registered"
    assert result["data_storage"] == "external"


def test_init_preserves_project_identity_from_manifest(
    tmp_path: Path, isolated_user_roots: None
) -> None:
    """Test that project UUID and name are preserved from existing manifest."""
    proj = tmp_path / "audiosig"
    proj.mkdir()
    _write_manifest(proj, AUDIOSIG_MANIFEST)

    result = paths.ensure_canonical_project(proj)

    assert result["project_uuid"] == "685a04ef-b8a0-4b14-a53c-b405c10547a2"
    assert result["project_name"] == "audiosig"


def test_init_json_envelope_exposes_mode_and_changes(
    tmp_path: Path, isolated_user_roots: None
) -> None:
    """Test that JSON envelope exposes mode and change booleans/lists."""
    proj = tmp_path / "proj"
    proj.mkdir()
    result = paths.ensure_canonical_project(proj, project_name="demo")

    assert "mode" in result
    assert "manifest_changed" in result
    assert "config_created" in result
    assert "bindings_created" in result
    assert "preserved_ledgers" in result
