"""Tests for ``releaseledger.ledgercore_backend``.

The adapter is the sole owner of Ledgercore 0.5.x calls inside
releaseledger. These tests cover the public surface, the semantic mount
contract, structured error mapping, and the read-only guarantee on
``load_releaseledger_ledger_layout``.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
import tomlkit

from releaseledger import ledgercore_backend as backend
from releaseledger.errors import LaunchError

PROJECT_UUID = "7864d9da-4d0e-47a6-9074-581a4b2d684c"


@pytest.fixture()
def isolated_user_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point platformdirs at a temp directory so user-data/cache are isolated."""

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    # platformdirs on macOS uses these; set on every platform for safety.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def _write_schema3_manifest(
    project_root: Path,
    *,
    data_storage: str = "project",
    external_root: str | None = None,
    indexes_storage: str = "cache",
    extra_mounts: dict[str, dict[str, str]] | None = None,
    drop_mounts: tuple[str, ...] = (),
    project_name: str = "demo",
) -> Path:
    """Write a schema-3 ``.ledger/ledger.toml`` for the given storage choice."""

    ledg = project_root / ".ledger"
    ledg.mkdir(parents=True, exist_ok=True)
    doc = tomlkit.document()
    doc.add("schema_version", 3)
    project = tomlkit.table()
    project.add("uuid", PROJECT_UUID)
    project.add("name", project_name)
    doc.add("project", project)
    ledgers = tomlkit.table()
    rl = tomlkit.table()
    mounts = tomlkit.table()
    if backend.DATA_MOUNT not in drop_mounts:
        data = tomlkit.table()
        data.add("storage", data_storage)
        if external_root:
            data.add("root", external_root)
        mounts.add(backend.DATA_MOUNT, data)
    if backend.INDEXES_MOUNT not in drop_mounts:
        idx = tomlkit.table()
        idx.add("storage", indexes_storage)
        mounts.add(backend.INDEXES_MOUNT, idx)
    if extra_mounts:
        for name, body in extra_mounts.items():
            tbl = tomlkit.table()
            for k, v in body.items():
                tbl.add(k, v)
            mounts.add(name, tbl)
    rl.add("mounts", mounts)
    ledgers.add("releaseledger", rl)
    doc.add("ledgers", ledgers)
    manifest_path = ledg / "ledger.toml"
    manifest_path.write_text(tomlkit.dumps(doc))
    return manifest_path


def _load_layout(
    project_root: Path,
    *,
    validate_storage: bool = True,
    allow_missing: bool = False,
) -> backend.ReleaseledgerLedgerLayout:
    return backend.load_releaseledger_ledger_layout(
        project_root, validate_storage=validate_storage, allow_missing=allow_missing
    )


def test_schema3_project_storage(tmp_path: Path, isolated_user_roots: None) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    manifest_path = _write_schema3_manifest(proj)
    layout = _load_layout(proj)
    assert layout.project_uuid == PROJECT_UUID
    assert layout.project_name == "demo"
    assert layout.manifest_path == manifest_path.resolve()
    assert layout.config_path == (proj / ".ledger" / "releaseledger" / "config.toml")
    assert layout.data_root == (proj / ".ledger" / "releaseledger" / "data")
    assert layout.data_storage == "project"
    assert layout.data_source == "manifest"
    assert layout.indexes_root.name == "indexes"
    assert layout.indexes_root.parts[-5] == "ledgerwerk"
    assert layout.checkout_id.startswith("proj-")
    assert layout.validation_report is not None
    assert layout.validation_report.valid is True
    assert layout.external_root is None


def test_local_overlay_changes_storage(
    tmp_path: Path, isolated_user_roots: None
) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    _write_schema3_manifest(proj, data_storage="project")
    layout = backend.set_releaseledger_data_target(
        proj, storage="user-data", external_root=None, target="local"
    )
    assert layout is not None
    new_layout = _load_layout(proj)
    assert new_layout.data_storage == "user-data"
    assert new_layout.data_source in {"local", "manifest"}
    # The data source should be the local overlay (Ledgercore records the
    # override's source on the EffectiveMount).
    assert new_layout.data_source == "local"


def test_external_storage_with_root(tmp_path: Path, isolated_user_roots: None) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    external = tmp_path / "ledger"
    external.mkdir()
    (external / ".ledger-store.toml").write_text("")
    _write_schema3_manifest(proj, data_storage="external", external_root="../ledger")
    layout = _load_layout(proj)
    assert layout.data_storage == "external"
    assert layout.external_root is not None
    resolved_external = external.resolve()
    assert str(layout.data_root).startswith(str(resolved_external))


def test_user_data_storage(tmp_path: Path, isolated_user_roots: None) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    _write_schema3_manifest(proj, data_storage="user-data")
    layout = _load_layout(proj)
    assert str(layout.data_root).startswith(str(tmp_path / "data" / "ledgerwerk"))


def test_cache_indexes_mount(tmp_path: Path, isolated_user_roots: None) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    _write_schema3_manifest(proj, indexes_storage="cache")
    layout = _load_layout(proj)
    assert layout.indexes_root.name == "indexes"
    assert "ledgerwerk" in layout.indexes_root.parts


def test_indexes_storage_must_be_cache(
    tmp_path: Path, isolated_user_roots: None
) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    _write_schema3_manifest(proj, indexes_storage="project")
    with pytest.raises(LaunchError) as exc:
        _load_layout(proj, validate_storage=False)
    assert exc.value.code == "CONFIG_ERROR"
    assert exc.value.data["mount"] == backend.INDEXES_MOUNT
    assert exc.value.data["actual_storage"] == "project"
    assert "cache" in exc.value.data["allowed_storage"]


def test_data_storage_must_be_supported_kind(
    tmp_path: Path, isolated_user_roots: None
) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    _write_schema3_manifest(proj, data_storage="cache")
    with pytest.raises(LaunchError) as exc:
        _load_layout(proj, validate_storage=False)
    assert exc.value.code == "CONFIG_ERROR"
    assert exc.value.data["mount"] == backend.DATA_MOUNT
    assert exc.value.data["actual_storage"] == "cache"


def test_missing_mount_rejected(tmp_path: Path, isolated_user_roots: None) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    _write_schema3_manifest(proj, drop_mounts=(backend.INDEXES_MOUNT,))
    with pytest.raises(LaunchError) as exc:
        _load_layout(proj, validate_storage=False)
    assert exc.value.code == "CONFIG_ERROR"
    assert backend.INDEXES_MOUNT in exc.value.data["missing_mounts"]


def test_extra_mount_rejected(tmp_path: Path, isolated_user_roots: None) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    _write_schema3_manifest(proj, extra_mounts={"extra": {"storage": "project"}})
    with pytest.raises(LaunchError) as exc:
        _load_layout(proj, validate_storage=False)
    assert exc.value.code == "CONFIG_ERROR"
    assert "extra" in exc.value.data["extra_mounts"]


def test_no_canonical_project_returns_not_found(
    tmp_path: Path, isolated_user_roots: None
) -> None:
    with pytest.raises(LaunchError) as exc:
        _load_layout(tmp_path, validate_storage=False, allow_missing=True)
    assert exc.value.code == "NOT_FOUND"
    assert "init" in str(exc.value.remediation).lower()


def test_malformed_canonical_project_does_not_fall_back(
    tmp_path: Path, isolated_user_roots: None
) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    ledg = proj / ".ledger"
    ledg.mkdir()
    (ledg / "ledger.toml").write_text("not a valid manifest = [unclosed")
    with pytest.raises(LaunchError) as exc:
        _load_layout(proj, validate_storage=False)
    assert exc.value.code == "CONFIG_ERROR"
    # The cause must be a Ledgercore error and the code must be preserved in data.
    assert exc.value.data["ledgercore_code"]
    assert exc.value.data["ledgercore_error_type"]


def test_ledgercore_error_preserves_cause(
    tmp_path: Path, isolated_user_roots: None
) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    ledg = proj / ".ledger"
    ledg.mkdir()
    (ledg / "ledger.toml").write_text("schema_version = 2\n")
    with pytest.raises(LaunchError) as exc:
        _load_layout(proj, validate_storage=False)
    assert exc.value.code == "CONFIG_ERROR"
    assert exc.value.__cause__ is not None


def test_load_is_read_only(tmp_path: Path, isolated_user_roots: None) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    _write_schema3_manifest(proj)
    before = sorted(p for p in proj.rglob("*") if p.is_file())
    _ = _load_layout(proj)
    after = sorted(p for p in proj.rglob("*") if p.is_file())
    assert before == after


def test_set_local_then_clear_round_trip(
    tmp_path: Path, isolated_user_roots: None
) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    _write_schema3_manifest(proj, data_storage="project")
    backend.set_releaseledger_data_target(
        proj, storage="user-data", external_root=None, target="local"
    )
    cleared = backend.clear_releaseledger_data_override(proj)
    # After clear the local override is gone, so the layout must fall back
    # to the committed manifest's data storage.
    assert cleared is not None
    assert backend.TOOL_NAME not in cleared.ledgers
    layout = _load_layout(proj)
    assert layout.data_storage == "project"


def test_ensure_registration_writes_schema3(
    tmp_path: Path, isolated_user_roots: None
) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    new_uuid = str(uuid.uuid4())
    manifest = backend.ensure_releaseledger_registration(
        proj, project_uuid=new_uuid, project_name="alpha"
    )
    assert manifest.schema_version == 3
    assert manifest.project_uuid == new_uuid
    assert manifest.project_name == "alpha"
    layout = _load_layout(proj)
    assert layout.project_uuid == new_uuid
    assert layout.data_storage == "project"
    assert layout.indexes_root.name == "indexes"


def test_ensure_registration_preserves_other_tools(
    tmp_path: Path, isolated_user_roots: None
) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    ledg = proj / ".ledger"
    ledg.mkdir()
    doc = tomlkit.document()
    doc.add("schema_version", 3)
    project = tomlkit.table()
    project.add("uuid", PROJECT_UUID)
    project.add("name", "multi")
    doc.add("project", project)
    ledgers = tomlkit.table()
    other = tomlkit.table()
    other_mounts = tomlkit.table()
    other_mounts.add("data", tomlkit.table().add("storage", "project"))
    other.add("mounts", other_mounts)
    ledgers.add("otherledger", other)
    doc.add("ledgers", ledgers)
    (ledg / "ledger.toml").write_text(tomlkit.dumps(doc))
    backend.ensure_releaseledger_registration(proj, project_name="multi")
    manifest = _load_layout(proj)
    assert "otherledger" in {name for name in manifest.loaded.manifest.ledgers}


def test_initialize_locations_writes_bindings(
    tmp_path: Path, isolated_user_roots: None
) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    _write_schema3_manifest(proj)
    layout = _load_layout(proj, validate_storage=False)
    written = backend.initialize_releaseledger_locations(
        layout,
        initialize_config=True,
        initialize_data=True,
        initialize_indexes=True,
    )
    assert (layout.config_path.parent / ".ledger-project.toml").exists()
    assert (layout.data_root / ".ledger-project.toml").exists()
    assert (layout.indexes_root / ".ledger-project.toml").exists()
    assert "config_binding" in written
    assert "data_binding" in written
    assert "indexes_binding" in written


def test_ledger_layout_is_frozen(tmp_path: Path, isolated_user_roots: None) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    _write_schema3_manifest(proj)
    layout = _load_layout(proj)
    with pytest.raises((AttributeError, Exception)):
        layout.project_uuid = "x"  # type: ignore[misc]
