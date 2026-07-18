"""Tests for the schema-3 path adapter in ``releaseledger.storage.paths``."""

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


def _init(tmp_path: Path) -> Path:
    proj = tmp_path / "proj"
    proj.mkdir()
    paths.ensure_canonical_project(proj, project_name="demo")
    return proj


def test_initialize_project_creates_schema3_layout(
    tmp_path: Path, isolated_user_roots: None
) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    result = paths.ensure_canonical_project(proj, project_name="demo")
    assert (proj / ".ledger" / "ledger.toml").is_file()
    assert (proj / ".ledger" / "releaseledger" / "config.toml").is_file()
    assert result["config_version"] == 2
    assert Path(result["data_root"]).as_posix().endswith(".ledger/releaseledger/data")
    assert "indexes" in result["indexes_root"]


def test_initialize_project_is_idempotent(
    tmp_path: Path, isolated_user_roots: None
) -> None:
    proj = _init(tmp_path)
    first = paths.ensure_canonical_project(proj)
    second = paths.ensure_canonical_project(proj)
    assert second["kind"] == "project_init_idempotent"
    assert first["manifest_path"] == second["manifest_path"]


def test_initialize_project_rejects_invalid_existing(
    tmp_path: Path, isolated_user_roots: None
) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    ledg = proj / ".ledger"
    ledg.mkdir()
    (ledg / "ledger.toml").write_text("not a manifest = [")
    with pytest.raises(LaunchError) as exc:
        paths.ensure_canonical_project(proj)
    assert exc.value.code in {"CONFLICT", "CONFIG_ERROR"}


def test_resolve_project_paths_returns_active_ref(
    tmp_path: Path, isolated_user_roots: None
) -> None:
    proj = _init(tmp_path)
    p = paths.resolve_project_paths(proj)
    assert p.ledger_ref == "main"
    assert p.ledger_dir == p.data_root / "ledgers" / "main"
    assert p.releases_dir == p.ledger_dir / "releases"
    assert p.events_dir == p.ledger_dir / "events"
    assert p.indexes_dir == p.indexes_root / "ledgers" / "main"
    assert p.releases_index_path == p.indexes_dir / "releases.json"
    assert p.entries_index_path == p.indexes_dir / "entries.json"
    assert p.events_path == p.events_dir / "events.jsonl"


def test_resolve_project_paths_accepts_explicit_ref(
    tmp_path: Path, isolated_user_roots: None
) -> None:
    proj = _init(tmp_path)
    p = paths.resolve_project_paths(proj, ledger_ref="feature")
    assert p.ledger_ref == "feature"
    assert p.ledger_dir == p.data_root / "ledgers" / "feature"


def test_paths_for_ledger_does_not_mutate(
    tmp_path: Path, isolated_user_roots: None
) -> None:
    proj = _init(tmp_path)
    p = paths.resolve_project_paths(proj)
    alt = p.paths_for_ledger("feature")
    assert alt.ledger_ref == "feature"
    assert p.ledger_ref == "main"
    assert p is not alt
    # Same project is shared.
    assert alt.project is p.project


def test_paths_for_ledger_rejects_empty(
    tmp_path: Path, isolated_user_roots: None
) -> None:
    proj = _init(tmp_path)
    p = paths.resolve_project_paths(proj)
    with pytest.raises(LaunchError) as exc:
        p.paths_for_ledger("")
    assert exc.value.code == "USAGE_ERROR"


def test_deprecated_aliases(tmp_path: Path, isolated_user_roots: None) -> None:
    proj = _init(tmp_path)
    p = paths.resolve_project_paths(proj)
    assert p.workspace_root == p.project_root == proj
    assert p.releaseledger_dir == p.data_root


def test_require_project_raises_not_found(
    tmp_path: Path, isolated_user_roots: None
) -> None:
    with pytest.raises(LaunchError) as exc:
        paths.require_project(tmp_path / "missing")
    assert exc.value.code == "NOT_FOUND"
    assert "init" in str(exc.value.remediation).lower()


def test_ensure_layout_creates_ledger_dirs(
    tmp_path: Path, isolated_user_roots: None
) -> None:
    proj = _init(tmp_path)
    p = paths.ensure_layout(proj)
    assert p.ledger_dir.is_dir()
    assert p.releases_dir.is_dir()
    assert p.events_dir.is_dir()
    assert p.indexes_dir.is_dir()


def test_legacy_initialize_project_rejects_removed_flags(
    tmp_path: Path, isolated_user_roots: None
) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    with pytest.raises(LaunchError) as exc:
        paths.initialize_project(proj, releaseledger_dir=".rl")
    assert exc.value.code == "USAGE_ERROR"
    # The flag stored in data uses the same spelling as the user-facing option.
    assert "releaseledger-dir" in str(exc.value.data.get("flag", ""))


def test_legacy_initialize_project_rejects_external_dir(
    tmp_path: Path, isolated_user_roots: None
) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    with pytest.raises(LaunchError) as exc:
        paths.initialize_project(proj, external_dir=True)
    assert exc.value.code == "USAGE_ERROR"


def test_legacy_initialize_project_with_force_is_idempotent(
    tmp_path: Path, isolated_user_roots: None
) -> None:
    proj = _init(tmp_path)
    result = paths.initialize_project(proj, force=True)
    assert result["kind"] == "project_init_idempotent"


def test_resolve_releaseledger_dir_is_deprecated(
    tmp_path: Path, isolated_user_roots: None
) -> None:
    proj = _init(tmp_path)
    with pytest.raises(LaunchError) as exc:
        paths.resolve_releaseledger_dir(proj, "anything")
    assert exc.value.code == "USAGE_ERROR"


def test_load_releaseledger_project_uses_layout(
    tmp_path: Path, isolated_user_roots: None
) -> None:
    proj = _init(tmp_path)
    project = paths.load_releaseledger_project(proj)
    assert project.layout.project_root == proj.resolve()
    assert project.config.config_version == 2
    assert project.data_root == proj / ".ledger" / "releaseledger" / "data"


def test_project_paths_project_field_is_shared(
    tmp_path: Path, isolated_user_roots: None
) -> None:
    proj = _init(tmp_path)
    p = paths.resolve_project_paths(proj)
    other = paths.build_project_paths(p.project, "main")
    assert other.project is p.project
    assert other.ledger_dir == p.ledger_dir
