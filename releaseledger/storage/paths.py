"""Releaseledger path adapter.

The thin path layer over the Ledgercore 0.5.x resolved layout. The
canonical project and storage topology come from
:mod:`releaseledger.ledgercore_backend`; this module only derives the
inner domain paths Releaseledger owns (``ledgers/<ref>/releases``,
``ledgers/<ref>/events``, and per-ledger cache indexes).

For the 0.4.0 release the dataclass keeps the legacy field names
(``workspace_root``, ``releaseledger_dir``) as deprecated aliases so
existing call sites and tests can transition without breaking.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import ledgercore

from releaseledger import ledgercore_backend as _backend
from releaseledger.errors import (
    CODE_CONFLICT,
    CODE_NOT_FOUND,
    CODE_USAGE_ERROR,
    LaunchError,
)
from releaseledger.storage.config import (
    CONFIG_VERSION,
    ProjectConfig,
    load_project_config,
    write_project_config,
)

__all__ = [
    "CANONICAL_PROJECT_CONFIG_FILENAME",
    "DEFAULT_RELEASELEDGER_DIR_NAME",
    "PROJECT_CONFIG_FILENAMES",
    "ProjectLocator",
    "ProjectPaths",
    "ReleaseledgerProject",
    "discover_workspace_root",
    "ensure_layout",
    "find_project_config",
    "initialize_project",
    "load_project_locator",
    "require_project",
    "resolve_project_paths",
    "resolve_releaseledger_dir",
]

# Legacy filenames retained so the rest of the codebase can still look them
# up. New code must not introduce new references to them.
PROJECT_CONFIG_FILENAMES = (".releaseledger.toml", "releaseledger.toml")
CANONICAL_PROJECT_CONFIG_FILENAME = ".releaseledger.toml"
DEFAULT_RELEASELEDGER_DIR_NAME = ".releaseledger"

LocatorSource = str  # Literal kept loose for the legacy field.


@dataclass(slots=True, frozen=True)
class ProjectLocator:
    """Legacy compatibility shim around the resolved schema-3 layout.

    The ``source`` field documents how the locator was determined. New
    code should not depend on the underlying values; they exist only to
    preserve the public attribute surface of the 0.3.x release line.
    """

    workspace_root: Path
    config_path: Path
    releaseledger_dir: Path
    source: str


@dataclass(slots=True, frozen=True)
class ReleaseledgerProject:
    """Adapter view over a resolved schema-3 Releaseledger project.

    Mirrors the public surface that domain code can depend on. The
    ``config`` field is loaded from ``.ledger/releaseledger/config.toml``
    and is the only place the project-local tool configuration lives.
    """

    layout: _backend.ReleaseledgerLedgerLayout
    config: ProjectConfig
    project_root: Path
    config_path: Path
    data_root: Path
    indexes_root: Path
    project_uuid: str
    project_name: str | None
    config_binding_path: Path
    data_binding_path: Path
    indexes_binding_path: Path


@dataclass(slots=True, frozen=True)
class ProjectPaths:
    """Resolved on-disk paths for the active branch ledger.

    The fields are derived from the schema-3 data and indexes mounts.
    ``workspace_root`` and ``releaseledger_dir`` are kept as deprecated
    aliases for one release; new code should use ``project_root`` and
    ``data_root`` instead.
    """

    project: ReleaseledgerProject
    ledger_ref: str
    ledger_dir: Path
    releases_dir: Path
    events_dir: Path
    indexes_dir: Path
    releases_index_path: Path
    entries_index_path: Path
    events_path: Path

    @property
    def project_root(self) -> Path:
        return self.project.project_root

    @property
    def workspace_root(self) -> Path:
        """Deprecated alias for :attr:`project_root`."""

        return self.project.project_root

    @property
    def data_root(self) -> Path:
        return self.project.data_root

    @property
    def releaseledger_dir(self) -> Path:
        """Deprecated alias for :attr:`data_root`."""

        return self.project.data_root

    @property
    def config_path(self) -> Path:
        return self.project.config_path

    @property
    def indexes_root(self) -> Path:
        return self.project.indexes_root

    @property
    def config(self) -> ProjectConfig:
        return self.project.config

    def paths_for_ledger(self, ledger_ref: str) -> ProjectPaths:
        """Return a new :class:`ProjectPaths` for a different branch ledger."""

        if not isinstance(ledger_ref, str) or not ledger_ref.strip():
            raise LaunchError(
                "ledger_ref must be a non-empty string.",
                code=CODE_USAGE_ERROR,
                exit_code=2,
                data={"ledger_ref": ledger_ref},
            )
        if ledger_ref == self.ledger_ref:
            return self
        return build_project_paths(self.project, ledger_ref)


# ---------------------------------------------------------------------------
# Project loading
# ---------------------------------------------------------------------------


def find_project_config(start: Path) -> Path | None:
    """Return the legacy ``.releaseledger.toml`` location, if any.

    Canonical projects resolve through the new schema-3 layout. This
    helper exists only to let the legacy CLI surface produce a
    migration-required error message when an unrecognised layout is
    encountered. The returned path is informational.
    """

    search = Path(start).resolve()
    if search.is_file():
        search = search.parent
    locator = ledgercore.locate_config(
        search,
        PROJECT_CONFIG_FILENAMES,
        default_filename=None,
    )
    if locator is None:
        return None
    return locator.config_path


def discover_workspace_root(start: Path) -> Path:
    """Return the directory that owns the project, or ``start`` itself."""

    legacy = find_project_config(start)
    if legacy is not None:
        return legacy.parent.resolve()
    return _resolve_search_root(start)


def load_project_locator(
    start: Path,
    *,
    releaseledger_dir_override: str | None = None,
) -> ProjectLocator:
    """Legacy compatibility shim around the new layout resolution.

    The override, when supplied, is intentionally rejected in the
    schema-3 runtime because storage topology is now an adapter-level
    concern, not a tool-config one.
    """

    if releaseledger_dir_override is not None:
        raise LaunchError(
            "releaseledger_dir_override is no longer supported; "
            "configure the canonical Ledger project instead.",
            code=CODE_USAGE_ERROR,
            exit_code=2,
        )
    project = load_releaseledger_project(start)
    return ProjectLocator(
        workspace_root=project.project_root,
        config_path=project.config_path,
        releaseledger_dir=project.data_root,
        source="canonical",
    )


def load_releaseledger_project(start: Path) -> ReleaseledgerProject:
    """Load the canonical project and tool config from ``start``."""

    search = _resolve_search_root(start)
    try:
        layout = _backend.load_releaseledger_ledger_layout(
            search, validate_storage=False, allow_missing=True
        )
    except LaunchError as exc:
        if exc.code == "NOT_FOUND":
            raise LaunchError(
                f"No Releaseledger project found from {search}",
                code=CODE_NOT_FOUND,
                exit_code=2,
                data={"start": str(search)},
                remediation=[
                    "Run `releaseledger init` to initialize a schema-3 project.",
                ],
            ) from exc
        raise
    config = (
        load_project_config(layout.config_path)
        if layout.config_path.is_file()
        else ProjectConfig()
    )
    return ReleaseledgerProject(
        layout=layout,
        config=config,
        project_root=layout.project_root,
        config_path=layout.config_path,
        data_root=layout.data_root,
        indexes_root=layout.indexes_root,
        project_uuid=layout.project_uuid,
        project_name=layout.project_name,
        config_binding_path=layout.config_binding_path,
        data_binding_path=layout.data_binding_path,
        indexes_binding_path=layout.indexes_binding_path,
    )


def resolve_project_paths(
    start: Path,
    *,
    ledger_ref: str | None = None,
) -> ProjectPaths:
    """Resolve paths for the active branch ledger or ``ledger_ref``."""

    project = load_releaseledger_project(start)
    selected = ledger_ref or project.config.ledger_ref
    return build_project_paths(project, selected)


def build_project_paths(
    project: ReleaseledgerProject, ledger_ref: str
) -> ProjectPaths:
    """Build a :class:`ProjectPaths` for ``ledger_ref`` against ``project``."""

    if not isinstance(ledger_ref, str) or not ledger_ref.strip():
        raise LaunchError(
            "ledger_ref must be a non-empty string.",
            code=CODE_USAGE_ERROR,
            exit_code=2,
            data={"ledger_ref": ledger_ref},
        )
    ledger_dir = project.data_root / "ledgers" / ledger_ref
    indexes_dir = project.indexes_root / "ledgers" / ledger_ref
    return ProjectPaths(
        project=project,
        ledger_ref=ledger_ref,
        ledger_dir=ledger_dir,
        releases_dir=ledger_dir / "releases",
        events_dir=ledger_dir / "events",
        indexes_dir=indexes_dir,
        releases_index_path=indexes_dir / "releases.json",
        entries_index_path=indexes_dir / "entries.json",
        events_path=ledger_dir / "events" / "events.jsonl",
    )


def require_project(start: Path) -> ProjectPaths:
    """Resolve paths or raise a structured NOT_FOUND error."""

    try:
        return resolve_project_paths(start)
    except LaunchError as exc:
        if exc.code == "NOT_FOUND":
            raise LaunchError(
                "Project not initialized: no .ledger/ledger.toml found.",
                code=CODE_NOT_FOUND,
                exit_code=2,
                data=exc.data,
                remediation=[
                    "Run `releaseledger init` to initialize the project.",
                ],
            ) from exc
        raise


# ---------------------------------------------------------------------------
# Layout creation
# ---------------------------------------------------------------------------


def ensure_layout(workspace_root: Path) -> ProjectPaths:
    """Create the durable ``ledgers/<ref>`` tree and cache stubs.

    Indexes are written into the cache mount only, never into the
    durable data mount. If the cache directory does not exist it is
    created; the durable layout itself is not created on read.
    """

    project = load_releaseledger_project(workspace_root)
    paths = build_project_paths(project, project.config.ledger_ref)
    ledgercore.ensure_dir(paths.ledger_dir)
    ledgercore.ensure_dir(paths.releases_dir)
    ledgercore.ensure_dir(paths.events_dir)
    ledgercore.ensure_dir(paths.indexes_dir)
    return paths


def initialize_project(
    workspace_root: Path,
    *,
    releaseledger_dir: str | None = None,
    project_name: str | None = None,
    force: bool = False,
    external_dir: bool = False,
) -> dict[str, object]:
    """Create the canonical schema-3 project and Releaseledger registration.

    ``releaseledger_dir``, ``external_dir``, and ``force`` are accepted
    only to produce precise remediation errors in this compatibility
    release. New code should call :func:`ensure_canonical_project`
    instead.
    """

    if releaseledger_dir is not None:
        raise LaunchError(
            "--releaseledger-dir is no longer supported; the canonical "
            "Ledger project owns the data mount.",
            code=CODE_USAGE_ERROR,
            exit_code=2,
            data={"flag": "--releaseledger-dir"},
            remediation=[
                "Run `releaseledger init` to create a schema-3 project.",
            ],
        )
    if external_dir:
        raise LaunchError(
            "--external-dir is no longer supported; use "
            "`releaseledger storage set data --storage external --root PATH`.",
            code=CODE_USAGE_ERROR,
            exit_code=2,
        )
    manifest_path = workspace_root / ".ledger" / "ledger.toml"
    # ``force`` is accepted for legacy compatibility but is now a no-op:
    # the schema-3 init is idempotent, so re-running on an existing
    # canonical project returns a summary rather than overwriting.
    return ensure_canonical_project(
        workspace_root, project_name=project_name, force=False
    )

def ensure_canonical_project(
    workspace_root: Path,
    *,
    project_name: str | None = None,
    project_uuid: str | None = None,
    force: bool = False,
    data_storage: str = "project",
    external_root: str | None = None,
    local_override: bool = False,
    adopt_empty: bool = False,
    force_config: bool = False,
) -> dict[str, object]:
    """Create or refresh a schema-3 project with the Releaseledger registration."""

    workspace_root = Path(workspace_root).resolve()
    manifest_path = workspace_root / ".ledger" / "ledger.toml"
    if manifest_path.is_file() and not force:
        # Idempotent: if both registration and config exist, return a
        # summary without rewriting anything.
        try:
            project = load_releaseledger_project(workspace_root)
        except LaunchError as exc:
            raise LaunchError(
                f"existing manifest at {manifest_path} is not a valid "
                "Releaseledger schema-3 project.",
                code=CODE_CONFLICT,
                exit_code=2,
                data={"path": str(manifest_path), "cause": exc.code},
            ) from exc
        config_path = project.config_path
        # force_config: backup and replace the tool config.
        if force_config and config_path.is_file():
            import shutil

            backup = config_path.with_suffix(config_path.suffix + ".bak")
            shutil.copy2(config_path, backup)
            write_project_config(config_path, ProjectConfig())
        written = {
            "kind": "project_init_idempotent",
            "project_root": str(workspace_root),
            "manifest_path": str(manifest_path),
            "config_path": str(config_path),
            "project_uuid": project.project_uuid,
            "project_name": project.project_name,
            "data_root": str(project.data_root),
            "data_storage": str(project.layout.data_storage),
            "indexes_root": str(project.indexes_root),
            "ledger_ref": project.config.ledger_ref,
            "config_version": project.config.config_version,
        }
        return written

    manifest = _backend.ensure_releaseledger_registration(
        workspace_root,
        project_uuid=project_uuid,
        project_name=project_name,
        data_storage=data_storage,
        external_root=external_root,
    )
    project = load_releaseledger_project(workspace_root)
    _backend.initialize_releaseledger_locations(
        project.layout,
        initialize_config=True,
        initialize_data=True,
        initialize_indexes=True,
    )
    if not project.config_path.is_file() or force_config:
        write_project_config(project.config_path, ProjectConfig())

    # If local_override is requested, create it via the adapter.
    if local_override and data_storage != "project":
        _backend.set_releaseledger_data_target(
            workspace_root,
            storage=data_storage,
            external_root=external_root,
            target="local",
        )

    # Create the durable internal layout.
    from releaseledger.storage.store import rebuild_indexes_for_paths

    paths = build_project_paths(project, project.config.ledger_ref)
    ledgercore.ensure_dir(paths.ledger_dir)
    ledgercore.ensure_dir(paths.releases_dir)
    ledgercore.ensure_dir(paths.events_dir)
    ledgercore.ensure_dir(paths.indexes_dir)
    # Write empty indexes.
    rebuild_indexes_for_paths(paths)

    return {
        "kind": "project_init",
        "project_root": str(workspace_root),
        "manifest_path": str(manifest_path),
        "config_path": str(project.config_path),
        "project_uuid": project.project_uuid,
        "project_name": project.project_name,
        "data_root": str(project.data_root),
        "data_storage": str(project.layout.data_storage),
        "indexes_root": str(project.indexes_root),
        "ledger_ref": project.config.ledger_ref,
        "config_version": CONFIG_VERSION,
        "schema_version": manifest.schema_version,
        "created": {
            "releases_dir": str(paths.releases_dir),
            "events_dir": str(paths.events_dir),
            "indexes_dir": str(paths.indexes_dir),
        },
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_search_root(start: Path) -> Path:
    """Resolve ``start`` to a directory used for upward search."""

    search = Path(start).resolve()
    if search.is_file():
        search = search.parent
    return search


# Kept for callers that imported the helper from the legacy module. It
# only validates the value; resolution is owned by the canonical layout.
def resolve_releaseledger_dir(
    workspace_root: Path,
    value: str,
    *,
    policy: str = "workspace",
) -> Path:
    """Deprecated resolver retained for one release."""

    if not isinstance(value, str) or not value.strip():
        raise LaunchError(
            "releaseledger_dir must be a non-empty string.",
            code=CODE_USAGE_ERROR,
            exit_code=2,
        )
    raise LaunchError(
        "resolve_releaseledger_dir is no longer supported; storage "
        "topology is owned by the canonical Ledger project.",
        code=CODE_USAGE_ERROR,
        exit_code=2,
        data={
            "workspace_root": str(workspace_root),
            "value": value,
            "policy": policy,
        },
        remediation=[
            "Configure the data mount through the canonical Ledger project "
            "(`releaseledger storage set data --storage ...`).",
        ],
    )


def _iter_legacy_path_holders() -> Iterator[Any]:
    """Compatibility hook used by tests and CLI diagnostic surfaces."""

    return iter(())
