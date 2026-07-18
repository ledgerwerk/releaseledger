"""Releaseledger sole adapter for Ledgercore 0.5.x public APIs.

This module is the only place in releaseledger that imports detailed
Ledgercore manifest, layout, binding, validation, and migration APIs.
Domain code consumes :class:`ReleaseledgerLedgerLayout` and the typed
helpers defined here, not Ledgercore dataclasses directly.

The adapter owns:

* canonical ``.ledger/ledger.toml`` discovery and schema-3 parsing;
* canonical ``.ledger/ledger.local.toml`` parsing and overlay;
* ``config``, ``data``, and ``indexes`` mount path derivation;
* semantic Releaseledger mount contract validation;
* external store marker validation;
* structured mapping from ``ledgercore`` errors to releaseledger
  :class:`LaunchError` while preserving ``__cause__`` and the original
  ``code`` in ``error.data``.

Generic utility modules (``ledgercore.atomic``, ``ledgercore.frontmatter``,
``ledgercore.ids``, ``ledgercore.io``, ``ledgercore.jsonio``,
``ledgercore.jsonl``, ``ledgercore.refs``, ``ledgercore.yamlio``) remain
importable from the wider releaseledger codebase.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from ledgercore.errors import (
    LedgerCoreError,
    StorageBindingError,
    StorageError,
)
from ledgercore.manifest import (
    EffectiveLedgerRegistration,
    EffectiveMount,
    LedgerLocalOverrides,
    LedgerProjectManifest,
    LedgerRegistration,
    MountDefinition,
    StorageKind,
)
from ledgercore.storage_binding import (
    StorageBinding,
    StorageValidationReport,
    StorageValidationResult,
    initialize_config_binding,
    initialize_storage_binding,
    validate_external_store,
    validate_storage_binding,
)
from ledgercore.storage_paths import (
    derive_cache_mount_path,
    derive_external_mount_path,
    derive_project_mount_path,
    derive_tool_config_path,
    derive_user_data_mount_path,
)
from ledgercore.tomlio import (
    clear_local_mount_override as _ledgercore_clear_local_mount_override,
)
from ledgercore.tomlio import load_ledger_project
from ledgercore.tomlio import (
    read_ledger_manifest as _ledgercore_read_ledger_manifest,
)
from ledgercore.tomlio import (
    set_local_mount_override as _ledgercore_set_local_mount_override,
)
from ledgercore.tomlio import (
    write_ledger_manifest as _ledgercore_write_ledger_manifest,
)
from platformdirs import user_cache_path, user_data_path

from releaseledger.errors import (
    CODE_CONFIG_ERROR,
    CODE_NOT_FOUND,
    CODE_VALIDATION_ERROR,
    LaunchError,
)

__all__ = [
    "DATA_MOUNT",
    "INDEXES_MOUNT",
    "MIGRATION_STRATEGY_REBUILD",
    "PreparedReleaseledgerTarget",
    "ReleaseledgerLedgerLayout",
    "TOOL_NAME",
    "UserNamespace",
    "build_releaseledger_legacy_migration_plan",
    "clear_releaseledger_data_override",
    "ensure_releaseledger_registration",
    "execute_releaseledger_layout_migration",
    "initialize_releaseledger_locations",
    "load_releaseledger_ledger_layout",
    "plan_releaseledger_layout_migration",
    "prepare_legacy_migration_target",
    "set_releaseledger_data_target",
]

TOOL_NAME = "releaseledger"
DATA_MOUNT = "data"
INDEXES_MOUNT = "indexes"

# User-data and user-cache roots use the canonical Ledgerwerk namespace so
# they line up with other Ledgerwerk tools on the same machine.
USER_NAMESPACE = "ledgerwerk"

ALLOWED_DATA_STORAGE: frozenset[str] = frozenset({"project", "external", "user-data"})
ALLOWED_INDEXES_STORAGE: frozenset[str] = frozenset({"cache"})

MIGRATION_STRATEGY_REBUILD = "rebuild"

# Index of strategic mount validation message templates. The messages are
# stable enough to be used in tests and CLI remediation hints but the
# LaunchError code and structured ``data`` are the only thing the CLI
# commands should depend on.
_MOUNT_NAMES = frozenset({DATA_MOUNT, INDEXES_MOUNT})


@dataclass(frozen=True, slots=True)
class UserNamespace:
    """Resolved Ledgerwerk user-data and user-cache roots."""

    user_data: Path
    user_cache: Path


@dataclass(frozen=True, slots=True)
class PreparedReleaseledgerTarget:
    """Prepared migration target, computed without writing any files."""

    project_root: Path
    project_uuid: str
    project_name: str | None
    config_path: Path
    data_root: Path
    indexes_root: Path
    config_binding: StorageBinding
    data_binding: StorageBinding
    indexes_binding: StorageBinding
    config_changes: Any  # LedgerProjectManifest or LedgerLocalOverrides


@dataclass(frozen=True, slots=True)
class ReleaseledgerLedgerLayout:
    """Adapter view over a resolved Releaseledger schema-3 project.

    Domain code receives this object instead of touching Ledgercore
    dataclasses directly. The fields are intentionally stable: names
    match releaseledger vocabulary (``data_root``, ``indexes_root``,
    ``data_storage``, etc.) and the embedded ``mounts`` mapping is
    derived from the effective ledger registration.
    """

    project_root: Path
    project_uuid: str
    project_name: str | None
    manifest_path: Path
    local_config_path: Path
    config_path: Path
    config_binding_path: Path
    data_root: Path
    data_binding_path: Path
    data_storage: StorageKind
    data_source: str
    external_root: Path | None
    indexes_root: Path
    indexes_binding_path: Path
    checkout_id: str
    validation_report: StorageValidationReport | None
    loaded: Any


def _user_namespace() -> UserNamespace:
    """Return the Ledgerwerk user-data and user-cache roots for this host.

    Honors ``XDG_DATA_HOME`` and ``XDG_CACHE_HOME`` through platformdirs
    so tests can override the machine-local state without monkey-patching.
    """

    return UserNamespace(
        user_data=Path(user_data_path(USER_NAMESPACE, appauthor=False)),
        user_cache=Path(user_cache_path(USER_NAMESPACE, appauthor=False)),
    )


def _map_ledgercore_error(
    exc: LedgerCoreError,
    *,
    code: str,
    remediation: list[str] | None = None,
    extra_data: Mapping[str, object] | None = None,
) -> LaunchError:
    """Wrap a :class:`LedgerCoreError` into a :class:`LaunchError`.

    The original ``code`` from the Ledgercore exception is preserved in
    ``error.data`` together with the exception class name. ``__cause__``
    is set so traceback traversal still surfaces the original error. No
    string matching against the Ledgercore message is performed.
    """

    data: dict[str, object] = {
        "ledgercore_code": exc.code,
        "ledgercore_error_type": type(exc).__name__,
        "tool": TOOL_NAME,
    }
    if extra_data:
        data.update(dict(extra_data))
    return LaunchError(
        str(exc),
        code=code,
        exit_code=2,
        data=data,
        remediation=remediation or [],
    )


def _resolve_mount_path(
    *,
    project_root: Path,
    project_uuid: str,
    checkout_id: str,
    storage: StorageKind,
    mount_name: str,
    external_root: str | None,
    user_namespace: UserNamespace,
) -> Path:
    """Resolve a single schema-3 mount path according to storage kind.

    Mirrors the rules in
    :mod:`ledgercore.storage_paths` but is invoked through one place so
    the adapter can be the single point of contact for path logic.
    """

    if storage == "project":
        return derive_project_mount_path(project_root, TOOL_NAME, mount_name)
    if storage == "external":
        if not external_root:
            raise LaunchError(
                f"mount '{mount_name}' requires an external root",
                code=CODE_CONFIG_ERROR,
                exit_code=2,
                data={"tool": TOOL_NAME, "mount": mount_name},
            )
        return derive_external_mount_path(
            external_root,
            TOOL_NAME,
            project_uuid,
            mount_name,
            project_root=project_root,
        )
    if storage == "user-data":
        return derive_user_data_mount_path(
            user_namespace.user_data, TOOL_NAME, project_uuid, mount_name
        )
    if storage == "cache":
        return derive_cache_mount_path(
            user_namespace.user_cache,
            TOOL_NAME,
            project_uuid,
            checkout_id,
            mount_name,
        )
    raise LaunchError(
        f"unsupported storage kind {storage!r} for mount {mount_name!r}",
        code=CODE_CONFIG_ERROR,
        exit_code=2,
        data={"tool": TOOL_NAME, "mount": mount_name, "storage": storage},
    )


def _semantic_mount_contract(
    registration: EffectiveLedgerRegistration,
) -> tuple[EffectiveMount, EffectiveMount]:
    """Validate the Releaseledger mount contract.

    Requires exactly ``data`` and ``indexes`` mounts, with the storage
    kinds described in plan section 7.1. Raises a :class:`LaunchError`
    with a stable code and structured ``data`` when the contract is
    violated.
    """

    mounts = registration.mounts
    names = set(mounts)
    if names != _MOUNT_NAMES:
        missing = _MOUNT_NAMES - names
        extra = names - _MOUNT_NAMES
        raise LaunchError(
            "Releaseledger registration must declare exactly 'data' and "
            "'indexes' mounts.",
            code=CODE_CONFIG_ERROR,
            exit_code=2,
            data={
                "tool": TOOL_NAME,
                "expected_mounts": sorted(_MOUNT_NAMES),
                "actual_mounts": sorted(names),
                "missing_mounts": sorted(missing),
                "extra_mounts": sorted(extra),
            },
        )

    data_mount = mounts[DATA_MOUNT]
    if data_mount.storage not in ALLOWED_DATA_STORAGE:
        raise LaunchError(
            f"data mount storage must be one of {sorted(ALLOWED_DATA_STORAGE)}, "
            f"got {data_mount.storage!r}.",
            code=CODE_CONFIG_ERROR,
            exit_code=2,
            data={
                "tool": TOOL_NAME,
                "mount": DATA_MOUNT,
                "actual_storage": data_mount.storage,
                "allowed_storage": sorted(ALLOWED_DATA_STORAGE),
            },
        )

    indexes_mount = mounts[INDEXES_MOUNT]
    if indexes_mount.storage not in ALLOWED_INDEXES_STORAGE:
        raise LaunchError(
            f"indexes mount storage must be one of {sorted(ALLOWED_INDEXES_STORAGE)}, "
            f"got {indexes_mount.storage!r}.",
            code=CODE_CONFIG_ERROR,
            exit_code=2,
            data={
                "tool": TOOL_NAME,
                "mount": INDEXES_MOUNT,
                "actual_storage": indexes_mount.storage,
                "allowed_storage": sorted(ALLOWED_INDEXES_STORAGE),
            },
        )

    return data_mount, indexes_mount


def _binding_path(root: Path) -> Path:
    """Return the canonical ``.ledger-project.toml`` marker for a mount root."""

    return root / ".ledger-project.toml"


def _validate_optional_binding(
    *,
    mount_root: Path,
    expected: StorageBinding,
    allow_missing: bool,
) -> StorageValidationResult:
    """Run Ledgercore's per-mount validation in a uniform way.

    Builds a minimal namespace that exposes the attributes
    :func:`validate_storage_binding` reads (``path``, ``project_uuid``,
    ``tool``, ``name``, ``storage``) so we can validate a mount without
    depending on the schema-2 ``ResolvedLedgerLayout`` type.
    """

    mount_ns = SimpleNamespace(
        path=mount_root,
        project_uuid=expected.project_uuid,
        tool=expected.tool,
        name=expected.mount,
        storage=expected.storage,
    )
    return validate_storage_binding(
        mount_ns, allow_missing=allow_missing, expected=expected
    )


def _expected_binding(
    *, project_uuid: str, project_name: str | None, tool: str, mount: str, storage: str
) -> StorageBinding:
    """Construct the canonical storage binding for a project location."""

    return StorageBinding(
        schema_version=1,
        layout_version=3,
        project_uuid=project_uuid,
        project_name=project_name,
        tool=tool,
        mount=mount,
        storage=cast(StorageKind, storage),
    )


def _load_project(start: Path, *, allow_missing: bool) -> Any:
    """Load a schema-3 project or raise a structured :class:`LaunchError`.

    Delegates to :func:`ledgercore.load_ledger_project`. Wraps every
    ``LedgerCoreError`` into a :class:`LaunchError` preserving
    ``__cause__``. When ``allow_missing`` is true a missing project is
    reported with the ``NOT_FOUND`` code.
    """

    try:
        return load_ledger_project(start)
    except LedgerCoreError as exc:
        cause_data: dict[str, object] = {
            "start": str(start.resolve()),
        }
        if isinstance(exc, StorageError):
            raise _map_ledgercore_error(
                exc, code=CODE_CONFIG_ERROR, extra_data=cause_data
            ) from exc
        if allow_missing and "No canonical" in str(exc):
            raise LaunchError(
                f"No Releaseledger project found from {start}",
                code=CODE_NOT_FOUND,
                exit_code=2,
                data=cause_data,
                remediation=[
                    "Run `releaseledger init` to initialize a schema-3 project.",
                ],
            ) from exc
        raise _map_ledgercore_error(
            exc, code=CODE_CONFIG_ERROR, extra_data=cause_data
        ) from exc


def load_releaseledger_ledger_layout(
    start: Path,
    *,
    validate_storage: bool = True,
    allow_missing: bool = False,
    user_namespace: UserNamespace | None = None,
) -> ReleaseledgerLedgerLayout:
    """Load a Releaseledger schema-3 project and return the adapter view.

    Parameters
    ----------
    start:
        File or directory to search upward from.
    validate_storage:
        If true, run ``validate_storage_binding`` on the resolved
        ``config``, ``data``, and ``indexes`` locations. The validation
        report is attached to the returned layout.
    allow_missing:
        If true, a missing canonical project raises ``NOT_FOUND`` instead
        of ``CONFIG_ERROR``. A malformed manifest still raises
        ``CONFIG_ERROR``; we never silently fall back to legacy mode.
    """

    search = Path(start).resolve()
    if search.is_file():
        search = search.parent

    loaded = _load_project(search, allow_missing=allow_missing)
    manifest: LedgerProjectManifest = loaded.manifest
    if manifest.schema_version != 3:
        raise LaunchError(
            f"unsupported manifest schema_version={manifest.schema_version}",
            code=CODE_CONFIG_ERROR,
            exit_code=2,
            data={"schema_version": manifest.schema_version},
            remediation=[
                "Migrate the .ledger/ledger.toml to schema 3 before loading.",
            ],
        )

    registration = manifest.ledgers.get(TOOL_NAME)
    if registration is None:
        raise LaunchError(
            f"no '{TOOL_NAME}' registration in {loaded.locator.manifest_path}",
            code=CODE_CONFIG_ERROR,
            exit_code=2,
            data={
                "tool": TOOL_NAME,
                "available_tools": sorted(manifest.ledgers),
                "manifest_path": str(loaded.locator.manifest_path),
            },
            remediation=[
                "Add a [ledgers.releaseledger] registration with 'data' and "
                "'indexes' mounts.",
            ],
        )

    effective = loaded.effective_ledgers[TOOL_NAME]
    data_mount, indexes_mount = _semantic_mount_contract(effective)

    project_root = loaded.locator.project_root.resolve()
    project_uuid = manifest.project_uuid
    project_name = manifest.project_name
    user_ns = user_namespace or _user_namespace()
    checkout_id = _derive_checkout_id(project_root)

    config_path = derive_tool_config_path(project_root, TOOL_NAME)
    data_root = _resolve_mount_path(
        project_root=project_root,
        project_uuid=project_uuid,
        checkout_id=checkout_id,
        storage=data_mount.storage,
        mount_name=DATA_MOUNT,
        external_root=data_mount.external_root,
        user_namespace=user_ns,
    )
    indexes_root = _resolve_mount_path(
        project_root=project_root,
        project_uuid=project_uuid,
        checkout_id=checkout_id,
        storage=indexes_mount.storage,
        mount_name=INDEXES_MOUNT,
        external_root=indexes_mount.external_root,
        user_namespace=user_ns,
    )

    config_binding = _expected_binding(
        project_uuid=project_uuid,
        project_name=project_name,
        tool=TOOL_NAME,
        mount="config",
        storage="project",
    )
    data_binding = _expected_binding(
        project_uuid=project_uuid,
        project_name=project_name,
        tool=TOOL_NAME,
        mount=DATA_MOUNT,
        storage=data_mount.storage,
    )
    indexes_binding = _expected_binding(
        project_uuid=project_uuid,
        project_name=project_name,
        tool=TOOL_NAME,
        mount=INDEXES_MOUNT,
        storage=indexes_mount.storage,
    )

    report: StorageValidationReport | None = None
    if validate_storage:
        results: list[StorageValidationResult] = []
        results.append(
            _validate_optional_binding(
                mount_root=config_path.parent,
                expected=config_binding,
                allow_missing=True,
            )
        )
        if data_mount.storage == "external" and data_mount.external_root:
            try:
                validate_external_store(
                    Path(os.path.expanduser(data_mount.external_root)),
                    allow_legacy=True,
                )
            except StorageBindingError as exc:
                raise _map_ledgercore_error(
                    exc,
                    code=CODE_CONFIG_ERROR,
                    extra_data={
                        "mount": DATA_MOUNT,
                        "external_root": data_mount.external_root,
                    },
                ) from exc
        results.append(
            _validate_optional_binding(
                mount_root=data_root,
                expected=data_binding,
                allow_missing=True,
            )
        )
        results.append(
            _validate_optional_binding(
                mount_root=indexes_root,
                expected=indexes_binding,
                allow_missing=True,
            )
        )
        report = StorageValidationReport(tuple(results))

    return ReleaseledgerLedgerLayout(
        project_root=project_root,
        project_uuid=project_uuid,
        project_name=project_name,
        manifest_path=loaded.locator.manifest_path.resolve(),
        local_config_path=loaded.locator.local_config_path.resolve(),
        config_path=config_path,
        config_binding_path=_binding_path(config_path.parent),
        data_root=data_root,
        data_binding_path=_binding_path(data_root),
        data_storage=data_mount.storage,
        data_source=data_mount.source,
        external_root=(
            Path(os.path.expanduser(data_mount.external_root))
            if data_mount.storage == "external" and data_mount.external_root
            else None
        ),
        indexes_root=indexes_root,
        indexes_binding_path=_binding_path(indexes_root),
        checkout_id=checkout_id,
        validation_report=report,
        loaded=loaded,
    )


def _derive_checkout_id(project_root: Path) -> str:
    """Return the deterministic checkout id for a project root.

    Kept private to the adapter because no other module should depend on
    the cache checkout identity directly. The implementation mirrors
    :func:`ledgercore.storage_paths.derive_checkout_id` so layout
    resolution and validation agree.
    """

    from ledgercore.storage_paths import derive_checkout_id

    return derive_checkout_id(project_root)


def ensure_releaseledger_registration(
    project_root: Path,
    *,
    project_uuid: str | None = None,
    project_name: str | None = None,
    data_storage: str = "project",
    external_root: str | None = None,
) -> LedgerProjectManifest:
    """Create or update a schema-3 manifest with a Releaseledger entry.

    The function is intentionally conservative: existing registrations
    and project identity are preserved, comments and unrelated tables
    are not touched, and the canonical schema-3 layout is enforced.
    """

    if data_storage not in ALLOWED_DATA_STORAGE:
        raise LaunchError(
            f"unsupported data storage {data_storage!r}",
            code=CODE_CONFIG_ERROR,
            exit_code=2,
            data={
                "tool": TOOL_NAME,
                "allowed": sorted(ALLOWED_DATA_STORAGE),
                "requested": data_storage,
            },
        )
    if data_storage == "external" and not external_root:
        raise LaunchError(
            "external data storage requires --external-root",
            code="USAGE_ERROR",
            exit_code=2,
            data={"tool": TOOL_NAME},
        )

    project_root = Path(project_root).resolve()
    manifest_path = project_root / ".ledger" / "ledger.toml"
    ledgers_table: dict[str, Any]
    manifest: LedgerProjectManifest
    project_uuid_resolved: str
    project_name_resolved: str | None

    if manifest_path.is_file():
        document = _ledgercore_read_ledger_manifest(manifest_path)
        if not isinstance(document, LedgerProjectManifest):
            raise LaunchError(
                f"{manifest_path} is not a schema-3 project manifest",
                code=CODE_CONFIG_ERROR,
                exit_code=2,
                data={"manifest_path": str(manifest_path)},
            )
        manifest = document
        project_uuid_resolved = manifest.project_uuid
        project_name_resolved = manifest.project_name
        ledgers_table = {
            name: LedgerRegistration(
                name=reg.name,
                mounts={n: m for n, m in reg.mounts.items()},
            )
            for name, reg in manifest.ledgers.items()
        }
    else:
        import uuid

        project_uuid_resolved = project_uuid or str(uuid.uuid4())
        project_name_resolved = project_name
        ledgers_table = {}

    mounts: dict[str, MountDefinition] = {}
    if TOOL_NAME in ledgers_table:
        mounts.update(ledgers_table[TOOL_NAME].mounts)
    mounts[DATA_MOUNT] = MountDefinition(
        name=DATA_MOUNT,
        storage=cast(StorageKind, data_storage),
        external_root=external_root,
    )
    mounts[INDEXES_MOUNT] = MountDefinition(
        name=INDEXES_MOUNT,
        storage="cache",
        external_root=None,
    )
    ledgers_table[TOOL_NAME] = LedgerRegistration(name=TOOL_NAME, mounts=mounts)

    manifest = LedgerProjectManifest(
        schema_version=3,
        project_uuid=project_uuid_resolved,
        project_name=project_name_resolved,
        ledgers=ledgers_table,
    )
    try:
        _ledgercore_write_ledger_manifest(manifest_path, manifest)
    except LedgerCoreError as exc:
        raise _map_ledgercore_error(
            exc,
            code=CODE_CONFIG_ERROR,
            extra_data={"manifest_path": str(manifest_path)},
        ) from exc
    return manifest


def initialize_releaseledger_locations(
    layout: ReleaseledgerLedgerLayout,
    *,
    initialize_config: bool,
    initialize_data: bool,
    initialize_indexes: bool,
) -> dict[str, object]:
    """Materialize the canonical bindings and directories for a layout.

    Only the locations the caller opts in to are touched. The function
    delegates to :func:`initialize_config_binding` and
    :func:`initialize_storage_binding` from Ledgercore so the binding
    markers stay compatible with future versions.
    """

    written: dict[str, object] = {}

    if initialize_config:
        config_layout = SimpleNamespace(
            tool_config_path=layout.config_path,
            project_uuid=layout.project_uuid,
            ledger_name=TOOL_NAME,
        )
        try:
            binding = initialize_config_binding(config_layout)
        except LedgerCoreError as exc:
            raise _map_ledgercore_error(
                exc,
                code=CODE_CONFIG_ERROR,
                extra_data={"mount": "config", "path": str(layout.config_path)},
            ) from exc
        written["config_binding"] = str(layout.config_binding_path)
        written["config_binding_identity"] = binding

    if initialize_data:
        mount = SimpleNamespace(
            path=layout.data_root,
            project_uuid=layout.project_uuid,
            tool=TOOL_NAME,
            name=DATA_MOUNT,
            storage=layout.data_storage,
        )
        try:
            initialize_storage_binding(mount, require_empty=False)
        except LedgerCoreError as exc:
            raise _map_ledgercore_error(
                exc,
                code=CODE_CONFIG_ERROR,
                extra_data={"mount": DATA_MOUNT, "path": str(layout.data_root)},
            ) from exc
        written["data_binding"] = str(layout.data_binding_path)
        written["data_root"] = str(layout.data_root)

    if initialize_indexes:
        mount = SimpleNamespace(
            path=layout.indexes_root,
            project_uuid=layout.project_uuid,
            tool=TOOL_NAME,
            name=INDEXES_MOUNT,
            storage="cache",
        )
        try:
            initialize_storage_binding(mount, require_empty=False)
        except LedgerCoreError as exc:
            raise _map_ledgercore_error(
                exc,
                code=CODE_CONFIG_ERROR,
                extra_data={"mount": INDEXES_MOUNT, "path": str(layout.indexes_root)},
            ) from exc
        written["indexes_binding"] = str(layout.indexes_binding_path)
        written["indexes_root"] = str(layout.indexes_root)

    return written


def set_releaseledger_data_target(
    start: Path,
    *,
    storage: str,
    external_root: str | None,
    target: str,
) -> LedgerLocalOverrides:
    """Set the data mount storage in the manifest or the local overlay.

    ``target`` is ``"project"`` (writes the manifest) or ``"local"``
    (writes ``.ledger/ledger.local.toml``). The function returns the
    updated overlay without committing it for ``local``; callers should
    pass the result back through a writer to persist it.
    """

    if storage not in ALLOWED_DATA_STORAGE:
        raise LaunchError(
            f"unsupported data storage {storage!r}",
            code=CODE_CONFIG_ERROR,
            exit_code=2,
            data={
                "tool": TOOL_NAME,
                "allowed": sorted(ALLOWED_DATA_STORAGE),
                "requested": storage,
            },
        )
    if storage == "external" and not external_root:
        raise LaunchError(
            "external data storage requires an external root",
            code="USAGE_ERROR",
            exit_code=2,
            data={"tool": TOOL_NAME},
        )

    loaded = _load_project(start, allow_missing=False)
    try:
        overrides = _ledgercore_set_local_mount_override(
            loaded,
            TOOL_NAME,
            DATA_MOUNT,
            storage=storage,
            root=external_root,
        )
    except LedgerCoreError as exc:
        raise _map_ledgercore_error(
            exc,
            code=CODE_CONFIG_ERROR,
            extra_data={"target": target, "storage": storage},
        ) from exc

    if target == "project":
        manifest = _ledgercore_read_ledger_manifest(loaded.locator.manifest_path)
        if not isinstance(manifest, LedgerProjectManifest):
            raise LaunchError(
                "project target requires a schema-3 manifest",
                code=CODE_CONFIG_ERROR,
                exit_code=2,
            )
        new_ledgers = {
            name: LedgerRegistration(
                name=reg.name,
                mounts=dict(reg.mounts),
            )
            for name, reg in manifest.ledgers.items()
        }
        registration = new_ledgers.get(TOOL_NAME)
        if registration is None:
            raise LaunchError(
                f"no '{TOOL_NAME}' registration to update",
                code=CODE_CONFIG_ERROR,
                exit_code=2,
                data={"tool": TOOL_NAME},
            )
        new_mounts = dict(registration.mounts)
        new_mounts[DATA_MOUNT] = MountDefinition(
            name=DATA_MOUNT,
            storage=cast(StorageKind, storage),
            external_root=external_root,
        )
        new_ledgers[TOOL_NAME] = LedgerRegistration(name=TOOL_NAME, mounts=new_mounts)
        manifest = LedgerProjectManifest(
            schema_version=manifest.schema_version,
            project_uuid=manifest.project_uuid,
            project_name=manifest.project_name,
            ledgers=new_ledgers,
        )
        try:
            _ledgercore_write_ledger_manifest(loaded.locator.manifest_path, manifest)
        except LedgerCoreError as exc:
            raise _map_ledgercore_error(
                exc,
                code=CODE_CONFIG_ERROR,
                extra_data={"manifest_path": str(loaded.locator.manifest_path)},
            ) from exc
        return overrides
    if target == "local":
        from ledgercore.tomlio import write_ledger_local_config

        try:
            write_ledger_local_config(loaded.locator.local_config_path, overrides)
        except LedgerCoreError as exc:
            raise _map_ledgercore_error(
                exc,
                code=CODE_CONFIG_ERROR,
                extra_data={"path": str(loaded.locator.local_config_path)},
            ) from exc
        return overrides
    raise LaunchError(
        f"unsupported target {target!r}; expected 'project' or 'local'",
        code="USAGE_ERROR",
        exit_code=2,
        data={"target": target},
    )


def clear_releaseledger_data_override(start: Path) -> LedgerLocalOverrides | None:
    """Remove the Releaseledger data mount override from the local overlay."""

    loaded = _load_project(start, allow_missing=False)
    try:
        overrides = _ledgercore_clear_local_mount_override(
            loaded, TOOL_NAME, DATA_MOUNT
        )
    except LedgerCoreError as exc:
        raise _map_ledgercore_error(
            exc,
            code=CODE_CONFIG_ERROR,
        ) from exc
    if overrides is None and loaded.locator.local_config_path.is_file():
        loaded.locator.local_config_path.unlink()

    from ledgercore.tomlio import write_ledger_local_config

    try:
        write_ledger_local_config(loaded.locator.local_config_path, overrides)
    except LedgerCoreError as exc:
        raise _map_ledgercore_error(
            exc,
            code=CODE_CONFIG_ERROR,
            extra_data={"path": str(loaded.locator.local_config_path)},
        ) from exc
    return overrides


def plan_releaseledger_layout_migration(
    layout: ReleaseledgerLedgerLayout | None,
    *,
    source_data_root: Path | None = None,
    target_data_storage: str,
    target_external_root: str | None,
    target: str = "project",
    target_indexes_strategy: str = MIGRATION_STRATEGY_REBUILD,
) -> Any:
    """Build a :class:`ledgercore.StorageMigrationPlan` for the layout.

    This is a thin wrapper that constructs the same plan Ledgercore
    would build, but constrains the migration to ``rebuild`` for the
    ``indexes`` mount as required by plan section 14.5.

    If source_data_root is provided, it is used as the migration source
    instead of the layout's loaded data root. This is required for legacy
    migration where the source is .releaseledger, not the canonical layout.
    """

    if target_indexes_strategy != MIGRATION_STRATEGY_REBUILD:
        raise LaunchError(
            "indexes migration strategy must be 'rebuild'",
            code=CODE_CONFIG_ERROR,
            exit_code=2,
            data={
                "tool": TOOL_NAME,
                "requested": target_indexes_strategy,
                "allowed": [MIGRATION_STRATEGY_REBUILD],
            },
        )
    if target_data_storage not in ALLOWED_DATA_STORAGE:
        raise LaunchError(
            f"unsupported data storage {target_data_storage!r}",
            code=CODE_CONFIG_ERROR,
            exit_code=2,
        )

    from ledgercore.migration import plan_storage_migration

    if target_data_storage == "external" and not target_external_root:
        raise LaunchError(
            "external data storage requires an external root",
            code="USAGE_ERROR",
            exit_code=2,
            data={"tool": TOOL_NAME},
        )

    if layout is None:
        raise LaunchError(
            "layout is required",
            code=CODE_CONFIG_ERROR,
            exit_code=2,
        )

    target_overrides = _ledgercore_set_local_mount_override(
        layout.loaded,
        TOOL_NAME,
        DATA_MOUNT,
        storage=target_data_storage,
        root=target_external_root if target_data_storage == "external" else None,
    )

    try:
        plan = plan_storage_migration(
            layout.loaded,
            layout.loaded.manifest,
            target_overrides,
            TOOL_NAME,
            cache_strategy="rebuild",
        )
        # Do not mutate the frozen dataclass.
        return plan
    except LedgerCoreError as exc:
        raise _map_ledgercore_error(
            exc,
            code=CODE_CONFIG_ERROR,
            extra_data={"target_data_storage": target_data_storage},
        ) from exc


def build_releaseledger_legacy_migration_plan(
    *,
    prepared_target: PreparedReleaseledgerTarget,
    staged_data_root: Path,
    staged_config_path: Path,
    project_uuid: str,
) -> Any:
    """Build an immutable StorageMigrationPlan from a staged legacy source.

    Constructs a real ledgercore StorageMigrationPlan whose data item
    source is the staged data root and destination is the prepared
    canonical target. The plan is built manually (not via the generic
    planner) so the source is always the stage, never the current
    canonical data mount.
    """
    import uuid

    from ledgercore.migration import StorageMigrationItem, StorageMigrationPlan

    migration_id = str(uuid.uuid4())

    # Config item: copy the transformed config from stage to canonical
    config_item = StorageMigrationItem(
        component="config",
        tool_name=TOOL_NAME,
        mount_name="config",
        source=staged_config_path,
        destination=prepared_target.config_path,
        source_binding=prepared_target.config_binding,
        destination_binding=prepared_target.config_binding,
        strategy="copy",  # type: ignore[arg-type]
    )

    # Data item: copy staged data to canonical data mount
    data_item = StorageMigrationItem(
        component="mount",
        tool_name=TOOL_NAME,
        mount_name=DATA_MOUNT,
        source=staged_data_root,
        destination=prepared_target.data_root,
        source_binding=prepared_target.data_binding,
        destination_binding=prepared_target.data_binding,
        strategy="copy",  # type: ignore[arg-type]
    )

    # Indexes item: rebuild at destination
    indexes_item = StorageMigrationItem(
        component="mount",
        tool_name=TOOL_NAME,
        mount_name=INDEXES_MOUNT,
        source=staged_data_root,
        destination=prepared_target.indexes_root,
        source_binding=prepared_target.data_binding,
        destination_binding=prepared_target.indexes_binding,
        strategy="rebuild",  # type: ignore[arg-type]
    )

    plan = StorageMigrationPlan(
        migration_id=migration_id,
        project_uuid=project_uuid,
        items=(config_item, data_item, indexes_item),
        config_changes=prepared_target.config_changes,
        warnings=(),
    )

    return plan


def prepare_legacy_migration_target(
    workspace_root: Path,
    *,
    project_name: str | None = None,
    data_storage: str = "project",
    external_root: str | None = None,
    target: str = "project",
) -> PreparedReleaseledgerTarget:
    """Prepare the migration target without writing any files.

    Reads existing canonical files if they exist but never writes.
    Returns a PreparedReleaseledgerTarget with all computed paths
    and bindings needed to build the migration plan.
    """
    import uuid as _uuid

    project_root = Path(workspace_root).resolve()
    manifest_path = project_root / ".ledger" / "ledger.toml"

    # Resolve project identity from existing manifest or generate new
    if manifest_path.is_file():
        document = _ledgercore_read_ledger_manifest(manifest_path)
        if isinstance(document, LedgerProjectManifest):
            resolved_uuid = document.project_uuid
            resolved_name = document.project_name or project_name
        else:
            resolved_uuid = str(_uuid.uuid4())
            resolved_name = project_name
    else:
        resolved_uuid = str(_uuid.uuid4())
        resolved_name = project_name

    # Reject --target local when no base manifest exists
    if target == "local" and not manifest_path.is_file():
        raise LaunchError(
            "--target local requires an existing schema-3 project manifest. "
            "Use --target project for legacy bootstrap or create a project first.",
            code=CODE_CONFIG_ERROR,
            exit_code=2,
            remediation=[
                "Run `releaseledger init` to create a schema-3 project.",
                "Or use `--target project` for legacy migration.",
            ],
        )

    # Compute mount paths
    if data_storage == "project":
        data_root = derive_project_mount_path(project_root, TOOL_NAME, DATA_MOUNT)
    elif data_storage == "external" and external_root:
        data_root = derive_external_mount_path(
            external_root,
            TOOL_NAME,
            resolved_uuid,
            DATA_MOUNT,
            project_root=project_root,
        )
    elif data_storage == "user-data":
        ns = _user_namespace()
        data_root = derive_user_data_mount_path(
            ns.user_data, TOOL_NAME, resolved_uuid, DATA_MOUNT
        )
    else:
        data_root = derive_project_mount_path(project_root, TOOL_NAME, DATA_MOUNT)

    checkout_id = _derive_checkout_id(project_root)
    ns = _user_namespace()
    indexes_root = derive_cache_mount_path(
        ns.user_cache, TOOL_NAME, resolved_uuid, checkout_id, INDEXES_MOUNT
    )
    config_path = derive_tool_config_path(project_root, TOOL_NAME)

    # Build expected bindings (pure — no writes)
    config_binding = _expected_binding(
        project_uuid=resolved_uuid,
        project_name=resolved_name,
        tool=TOOL_NAME,
        mount="config",
        storage="project",
    )
    data_binding = _expected_binding(
        project_uuid=resolved_uuid,
        project_name=resolved_name,
        tool=TOOL_NAME,
        mount=DATA_MOUNT,
        storage=data_storage,
    )
    indexes_binding = _expected_binding(
        project_uuid=resolved_uuid,
        project_name=resolved_name,
        tool=TOOL_NAME,
        mount=INDEXES_MOUNT,
        storage="cache",
    )

    # Build config_changes for activation after data copy
    # For project target: full manifest (creates .ledger/ledger.toml)
    # For local target: local overrides (creates .ledger/ledger.local.toml)
    if target == "project":
        from ledgercore.manifest import MountOverride

        registration = LedgerRegistration(
            name=TOOL_NAME,
            mounts={
                DATA_MOUNT: MountDefinition(
                    name=DATA_MOUNT,
                    storage=cast(StorageKind, data_storage),
                    external_root=external_root,
                ),
                INDEXES_MOUNT: MountDefinition(
                    name=INDEXES_MOUNT,
                    storage="cache",
                    external_root=None,
                ),
            },
        )
        config_changes = LedgerProjectManifest(
            schema_version=3,
            project_uuid=resolved_uuid,
            project_name=resolved_name,
            ledgers={TOOL_NAME: registration},
        )
    else:
        overrides = LedgerLocalOverrides(
            schema_version=3,
            ledgers={
                TOOL_NAME: {
                    DATA_MOUNT: MountOverride(
                        storage=cast(StorageKind, data_storage),
                        external_root=external_root,
                    )
                }
            },
        )
        config_changes = overrides

    return PreparedReleaseledgerTarget(
        project_root=project_root,
        project_uuid=resolved_uuid,
        project_name=resolved_name,
        config_path=config_path,
        data_root=data_root,
        indexes_root=indexes_root,
        config_binding=config_binding,
        data_binding=data_binding,
        indexes_binding=indexes_binding,
        config_changes=config_changes,
    )


def execute_releaseledger_layout_migration(
    plan: Any,
    *,
    mode: str = "copy",
    verify: str = "sha256",
    quiescence_check: Callable[[], None] | None = None,
    project_root: Path | None = None,
) -> Any:
    """Run a Releaseledger migration plan through :mod:`ledgercore.migration`.

    Uses only the ledgercore 0.5.0 executor contract. Does not pass
    ``staged_transform``, which is not in the declared minimum version.
    """

    from ledgercore.migration import execute_storage_migration

    def _safe_check() -> None:
        if quiescence_check is None:
            return
        try:
            quiescence_check()
        except Exception as exc:  # pragma: no cover - domain-defined
            raise LaunchError(
                "quiescence check failed",
                code=CODE_VALIDATION_ERROR,
                exit_code=1,
                data={"tool": TOOL_NAME},
            ) from exc

    try:
        kwargs: dict[str, object] = {
            "mode": mode,
            "verify": verify,
            "quiescence_check": _safe_check,
        }
        if project_root is not None:
            kwargs["project_root"] = project_root
        return execute_storage_migration(plan, **kwargs)  # type: ignore[arg-type]
    except LedgerCoreError as exc:
        raise _map_ledgercore_error(
            exc,
            code=CODE_CONFIG_ERROR,
            extra_data={"mode": mode},
        ) from exc
    except (OSError, ValueError) as exc:
        raise LaunchError(
            "storage migration execution failed",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
            data={"mode": mode},
        ) from exc
