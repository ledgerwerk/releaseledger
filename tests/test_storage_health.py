"""Normalized storage health and derived index-cache diagnostics."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from releaseledger.errors import LaunchError
from releaseledger.ledgercore_backend import (
    classify_releaseledger_index_cache,
    load_releaseledger_ledger_layout,
)
from releaseledger.migration import migration_status
from releaseledger.protocol import SKILL_PROTOCOL_VERSION
from releaseledger.services.config import storage_where
from releaseledger.services.entries import add_release_entry
from releaseledger.services.project_state import (
    next_action,
    project_doctor,
    project_status,
)
from releaseledger.services.releases import create_release
from releaseledger.services.storage_health import (
    assess_storage_layout,
    storage_health_to_dict,
)
from releaseledger.services.storage_repair import repair_index
from releaseledger.storage.paths import initialize_project, resolve_project_paths


def _initialized_project(tmp_path: Path) -> Path:
    initialize_project(tmp_path)
    create_release(tmp_path, version="1.0.0")
    return resolve_project_paths(tmp_path).project.indexes_root


def _install_matching_skill(root: Path) -> None:
    skill = root / ".agents" / "skills" / "releaseledger" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\n"
        "name: releaseledger\n"
        "description: Test Releaseledger skill.\n"
        f"protocol: {SKILL_PROTOCOL_VERSION}\n"
        "---\n",
        encoding="utf-8",
    )


def _where(tmp_path: Path) -> dict[str, object]:
    layout = load_releaseledger_ledger_layout(tmp_path, validate_storage=True)
    return storage_health_to_dict(assess_storage_layout(layout))


@pytest.mark.parametrize("manifest_kind", ["invalid", "directory"])
def test_existing_but_unloadable_canonical_manifest_stays_visible(
    tmp_path: Path, manifest_kind: str
) -> None:
    indexes = _initialized_project(tmp_path)
    manifest = tmp_path / ".ledger" / "ledger.toml"
    if manifest_kind == "invalid":
        manifest.write_text("not = [valid toml", encoding="utf-8")
    else:
        manifest.unlink()
        manifest.mkdir()

    result = storage_where(tmp_path)
    assert result["discovered"] is True
    assert result["canonical"] is True
    assert result["project_root"] == str(tmp_path)
    assert result["manifest_path"] == str(manifest)
    assert result["migration_state"] == "canonical-invalid"
    assert result["canonical_error"]
    assert result["legacy_detected"] is False
    assert result["bindings"] == {}
    assert indexes.exists()


def _snapshot_files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }


def test_generated_unbound_cache_keeps_indexes_identity_and_is_effectively_valid(
    tmp_path: Path,
) -> None:
    indexes = _initialized_project(tmp_path)
    (indexes / ".ledger-project.toml").unlink()

    result = _where(tmp_path)
    bindings = result["bindings"]
    assert isinstance(bindings, dict)
    assert "unknown" not in bindings
    mount = bindings["indexes"]
    assert isinstance(mount, dict)
    assert mount["classification"] == "generated-current"
    assert mount["repairable"] is True
    assert mount["raw_valid"] is False
    assert result["raw_layout_valid"] is False
    assert result["layout_valid"] is True
    assert result["indexes_repairable"] is True


def test_foreign_unbound_cache_is_blocked_and_reports_unexpected_path(
    tmp_path: Path,
) -> None:
    indexes = _initialized_project(tmp_path)
    (indexes / ".ledger-project.toml").unlink()
    (indexes / "foreign-cache.dat").write_text("foreign", encoding="utf-8")

    result = _where(tmp_path)
    bindings = result["bindings"]
    assert isinstance(bindings, dict)
    mount = bindings["indexes"]
    assert isinstance(mount, dict)
    assert mount["classification"] == "foreign"
    assert mount["valid"] is False
    assert mount["repairable"] is False
    assert result["indexes_repairable"] is False
    assert result["layout_valid"] is False
    assert "foreign-cache.dat" in mount["unexpected_paths"]


def test_missing_indexes_path_remains_named_and_valid(tmp_path: Path) -> None:
    indexes = _initialized_project(tmp_path)
    shutil.rmtree(indexes)

    result = _where(tmp_path)
    bindings = result["bindings"]
    assert isinstance(bindings, dict)
    assert "unknown" not in bindings
    mount = bindings["indexes"]
    assert isinstance(mount, dict)
    assert mount["classification"] == "missing"
    assert mount["valid"] is True
    assert result["layout_valid"] is True


def test_generated_legacy_cache_is_effectively_valid_and_repairable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _install_matching_skill(tmp_path)
    indexes = _initialized_project(tmp_path)
    current = indexes / "ledgers" / "main"
    legacy_releases = indexes / "releases.json"
    legacy_entries = indexes / "entries.json"
    legacy_releases.write_bytes((current / "releases.json").read_bytes())
    legacy_entries.write_bytes((current / "entries.json").read_bytes())
    (indexes / ".ledger-project.toml").unlink()
    shutil.rmtree(indexes / "ledgers")

    where = storage_where(tmp_path)
    assert where["bindings"]["indexes"]["classification"] == "generated-legacy"
    assert where["bindings"]["indexes"]["valid"] is True
    assert where["bindings"]["indexes"]["raw_valid"] is False
    assert where["layout_valid"] is True
    assert where["indexes_repairable"] is True
    assert where["migration_state"] == "canonical-ready"
    status = project_status(tmp_path)
    doctor = project_doctor(tmp_path)
    recommendation = next_action(tmp_path)
    checks = {str(item["code"]): item for item in doctor["checks"]}
    assert status["state"] == "ready"
    assert status["health"] == "ok"
    assert checks["storage_layout"]["status"] == "pass"
    assert recommendation["command"] not in {
        "storage validate --strict",
        "repair index --dry-run",
    }

    preview = repair_index(tmp_path, apply=False)
    assert preview["action"] == "rebind-and-rebuild"
    applied = repair_index(tmp_path, apply=True)
    assert applied["raw_layout_valid_after"] is True
    assert (indexes / ".ledger-project.toml").is_file()
    assert (indexes / "ledgers" / "main" / "releases.json").is_file()


def test_empty_indexes_path_is_healthy_and_rebuildable(tmp_path: Path) -> None:
    indexes = _initialized_project(tmp_path)
    shutil.rmtree(indexes)
    indexes.mkdir()

    where = storage_where(tmp_path)
    mount = where["bindings"]["indexes"]
    assert mount["classification"] == "empty"
    assert mount["valid"] is True
    assert mount["raw_valid"] is True
    assert where["layout_valid"] is True
    assert where["indexes_repairable"] is False

    repaired = repair_index(tmp_path, apply=True)
    assert repaired["action"] == "rebind-and-rebuild"
    assert (indexes / ".ledger-project.toml").is_file()


def test_nested_foreign_content_reports_exact_relative_path(tmp_path: Path) -> None:
    indexes = _initialized_project(tmp_path)
    (indexes / ".ledger-project.toml").unlink()
    foreign = indexes / "ledgers" / "main" / "foreign.dat"
    foreign.write_text("foreign", encoding="utf-8")

    where = storage_where(tmp_path)
    mount = where["bindings"]["indexes"]
    assert mount["classification"] == "foreign"
    assert mount["unexpected_paths"] == ["ledgers/main/foreign.dat"]
    assert next_action(tmp_path)["command"] == "repair index --dry-run"


def test_indexes_root_symlink_is_blocked_without_following_target(
    tmp_path: Path,
) -> None:
    indexes = _initialized_project(tmp_path)
    target = indexes.with_name("cache-target")
    shutil.copytree(indexes, target)
    shutil.rmtree(indexes)
    try:
        indexes.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable")

    result = _where(tmp_path)
    bindings = result["bindings"]
    assert isinstance(bindings, dict)
    mount = bindings["indexes"]
    assert isinstance(mount, dict)
    assert mount["valid"] is False
    assert mount["repairable"] is False
    assert "symlink" in mount["reason"]
    assert classify_releaseledger_index_cache(indexes).state == "foreign"


def test_generated_cache_health_is_consistent_across_state_surfaces(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _install_matching_skill(tmp_path)
    indexes = _initialized_project(tmp_path)
    (indexes / ".ledger-project.toml").unlink()

    where = storage_where(tmp_path)
    migration = migration_status(tmp_path)
    status = project_status(tmp_path)
    doctor = project_doctor(tmp_path)
    recommendation = next_action(tmp_path)
    checks = {str(item["code"]): item for item in doctor["checks"]}

    assert where["bindings"].get("unknown") is None
    assert where["bindings"]["indexes"]["classification"] == "generated-current"
    assert where["raw_layout_valid"] is False
    assert where["layout_valid"] is True
    assert where["indexes_repairable"] is True
    assert where["migration_state"] == "canonical-ready"
    assert migration["state"] == where["migration_state"]
    assert migration["raw_layout_valid"] is False
    assert migration["layout_valid"] is True
    assert status["migration_state"] == where["migration_state"]
    assert status["state"] == "ready"
    assert status["health"] == "ok"
    assert status["warnings"]
    assert checks["storage_layout"]["status"] == "pass"
    assert "unbound" in checks["storage_layout"]["message"]
    assert recommendation["command"] not in {
        "storage validate --strict",
        "repair index --dry-run",
    }


def test_blocked_cache_states_agree_and_recommend_repair(tmp_path: Path) -> None:
    indexes = _initialized_project(tmp_path)
    (indexes / ".ledger-project.toml").unlink()
    (indexes / "foreign-cache.dat").write_text("foreign", encoding="utf-8")

    where = storage_where(tmp_path)
    migration = migration_status(tmp_path)
    status = project_status(tmp_path)
    doctor = project_doctor(tmp_path)
    recommendation = next_action(tmp_path)
    checks = {str(item["code"]): item for item in doctor["checks"]}

    assert where["migration_state"] == "canonical-invalid"
    assert migration["state"] == where["migration_state"]
    assert status["migration_state"] == where["migration_state"]
    assert status["next_action"]["command"] == "repair index --dry-run"
    assert recommendation["command"] == "repair index --dry-run"
    assert "repair index --dry-run" in migration["remediation"]
    assert checks["storage_layout"]["status"] == "fail"
    assert checks["storage_layout"]["remediation"] == [
        "Run `releaseledger repair index --dry-run`."
    ]


def test_repair_generated_cache_is_read_only_then_rebuilds_all_ledger_refs(
    tmp_path: Path,
) -> None:
    indexes = _initialized_project(tmp_path)
    add_release_entry(
        tmp_path,
        release_version="1.0.0",
        kind="added",
        summary="Preserved release entry",
    )
    feature = indexes / "ledgers" / "feature"
    feature.mkdir()
    (feature / "releases.json").write_text("[]", encoding="utf-8")
    (feature / "entries.json").write_text("[]", encoding="utf-8")
    paths = resolve_project_paths(tmp_path)
    data_before = _snapshot_files(paths.project.data_root)
    (indexes / ".ledger-project.toml").unlink()
    cache_before = _snapshot_files(indexes)

    preview = repair_index(tmp_path, apply=False)
    assert preview["action"] == "rebind-and-rebuild"
    assert preview["classification_before"] == "generated-current"
    assert _snapshot_files(indexes) == cache_before

    applied = repair_index(tmp_path, apply=True)
    assert applied["binding_restored"] is True
    assert applied["indexes_rebuilt"] is True
    assert applied["layout_valid_after"] is True
    assert applied["raw_layout_valid_after"] is True
    assert applied["release_count"] == 1
    assert applied["entry_count"] == 1
    assert _snapshot_files(paths.project.data_root) == data_before
    assert (indexes / ".ledger-project.toml").is_file()
    assert (indexes / "ledgers" / "feature" / "releases.json").read_text() == "[]\n"
    assert (indexes / "ledgers" / "feature" / "entries.json").read_text() == "[]\n"

    repeated = repair_index(tmp_path, apply=True)
    assert repeated["action"] == "no-op"
    assert repeated["indexes_rebuilt"] is False


def test_foreign_cache_requires_quarantine_and_preserves_old_cache(
    tmp_path: Path,
) -> None:
    indexes = _initialized_project(tmp_path)
    paths = resolve_project_paths(tmp_path)
    data_before = _snapshot_files(paths.project.data_root)
    (indexes / ".ledger-project.toml").unlink()
    (indexes / "foreign-cache.dat").write_text("preserve me", encoding="utf-8")
    old_cache = _snapshot_files(indexes)
    collision = indexes.with_name("indexes.quarantine")
    collision.mkdir()
    (collision / "keep.txt").write_text("existing", encoding="utf-8")

    preview = repair_index(tmp_path, apply=False, quarantine_foreign=True)
    assert preview["action"] == "quarantine-and-rebuild"
    assert preview["unexpected_paths"] == ["foreign-cache.dat"]
    assert preview["quarantine_path"] == str(
        collision.with_name("indexes.quarantine-2") / "indexes"
    )
    assert _snapshot_files(indexes) == old_cache

    with pytest.raises(LaunchError, match="No files were changed") as exc:
        repair_index(tmp_path, apply=True)
    assert exc.value.data["applied"] is False
    assert _snapshot_files(indexes) == old_cache

    applied = repair_index(tmp_path, apply=True, quarantine_foreign=True)
    quarantine = Path(str(applied["quarantine_path"]))
    assert applied["action"] == "quarantine-and-rebuild"
    assert _snapshot_files(quarantine) == old_cache
    assert (collision / "keep.txt").read_text(encoding="utf-8") == "existing"
    assert (indexes / ".ledger-project.toml").is_file()
    assert storage_where(tmp_path)["bindings"]["indexes"]["classification"] == "bound"
    assert _snapshot_files(paths.project.data_root) == data_before


def test_mismatched_binding_requires_and_survives_quarantine(tmp_path: Path) -> None:
    indexes = _initialized_project(tmp_path)
    other_root = tmp_path / "other-project"
    initialize_project(other_root)
    other_indexes = resolve_project_paths(other_root).project.indexes_root
    (indexes / ".ledger-project.toml").write_bytes(
        (other_indexes / ".ledger-project.toml").read_bytes()
    )

    mount = storage_where(tmp_path)["bindings"]["indexes"]
    assert mount["mismatch"] is True
    actual = mount["actual_binding"]
    assert (
        actual["project_uuid"] == resolve_project_paths(other_root).project.project_uuid
    )
    assert (
        actual["project_uuid"] != resolve_project_paths(tmp_path).project.project_uuid
    )
    assert mount["unexpected_paths"] == [".ledger-project.toml"]
    preview = repair_index(tmp_path, apply=False)
    assert preview["action"] == "blocked"
    assert preview["mismatch"] is True
    mismatched_marker = (indexes / ".ledger-project.toml").read_bytes()
    with pytest.raises(LaunchError, match="foreign, mismatched"):
        repair_index(tmp_path, apply=True)
    assert (indexes / ".ledger-project.toml").read_bytes() == mismatched_marker

    applied = repair_index(tmp_path, apply=True, quarantine_foreign=True)
    assert applied["action"] == "quarantine-and-rebuild"
    old_marker = Path(str(applied["quarantine_path"])) / ".ledger-project.toml"
    assert (
        old_marker.read_bytes() == (other_indexes / ".ledger-project.toml").read_bytes()
    )
    assert (indexes / ".ledger-project.toml").is_file()


def test_symlink_cache_is_quarantined_without_following_target(tmp_path: Path) -> None:
    indexes = _initialized_project(tmp_path)
    target = tmp_path / "outside-cache-target"
    shutil.copytree(indexes, target)
    shutil.rmtree(indexes)
    try:
        indexes.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable")
    target_before = _snapshot_files(target)

    preview = repair_index(tmp_path, apply=False)
    assert preview["action"] == "blocked"
    assert preview["classification_before"] == "foreign"

    applied = repair_index(tmp_path, apply=True, quarantine_foreign=True)
    quarantine = Path(str(applied["quarantine_path"]))
    assert quarantine.is_symlink()
    assert quarantine.resolve() == target.resolve()
    assert _snapshot_files(target) == target_before
    assert indexes.is_dir() and not indexes.is_symlink()
    assert (indexes / ".ledger-project.toml").is_file()


def test_symlink_binding_marker_is_preserved_by_quarantine(tmp_path: Path) -> None:
    indexes = _initialized_project(tmp_path)
    marker = indexes / ".ledger-project.toml"
    target = tmp_path / "binding-marker-target.toml"
    target.write_bytes(marker.read_bytes())
    marker.unlink()
    try:
        marker.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable")

    preview = repair_index(tmp_path, apply=False)
    assert preview["action"] == "blocked"
    assert preview["unexpected_paths"] == [".ledger-project.toml"]
    target_before = target.read_bytes()

    applied = repair_index(tmp_path, apply=True, quarantine_foreign=True)
    quarantine_marker = Path(str(applied["quarantine_path"])) / ".ledger-project.toml"
    assert quarantine_marker.is_symlink()
    assert quarantine_marker.resolve() == target.resolve()
    assert target.read_bytes() == target_before
    assert (indexes / ".ledger-project.toml").is_file()
