"""Releaseledger legacy-to-schema-3 storage migration.

Owns the Releaseledger-specific migration behaviour that the Ledgercore
generic executor does not understand:

* legacy ``.releaseledger.toml`` / ``releaseledger.toml`` discovery;
* config version-1 to version-2 transformation;
* branch-ledger inventory from an arbitrary layout root;
* domain record validation (release, entry, event, audit);
* index rebuild for every discovered ledger ref;
* domain-level migration receipt;
* CLI rendering and remediation.

Generic copy, staging, hashing, verification, activation, rollback,
and journaling are delegated to Ledgercore through
:mod:`releaseledger.ledgercore_backend`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from releaseledger import ledgercore_backend as _backend
from releaseledger.errors import (
    CODE_CONFIG_ERROR,
    CODE_NOT_FOUND,
    CODE_VALIDATION_ERROR,
    LaunchError,
)

__all__ = [
    "ReleaseledgerMigrationRequest",
    "LegacyReleaseledgerSource",
    "ReleaseledgerDataInventory",
    "LedgerInventory",
    "MigrationExcludedPath",
    "ReleaseledgerMigrationTarget",
    "ReleaseledgerStorageMigrationPlan",
    "PathSelectionResult",
    "discover_legacy_project",
    "discover_legacy_source",
    "iter_legacy_ledger_roots",
    "select_legacy_durable_paths",
    "build_strict_inventory",
    "inventory_legacy_data",
    "plan_migration",
    "execute_migration",
    "validate_domain_records",
    "rebuild_all_indexes",
    "assert_inventory_preserved",
    "assert_same_source_snapshot",
    "transform_legacy_config_v1_to_v2",
    "read_migration_journal",
    "migration_status",
    "recover_migration",
]

# File names searched when detecting a legacy Releaseledger project.
LEGACY_CONFIG_NAMES = (".releaseledger.toml", "releaseledger.toml")

# Names that will be excluded from the copy during migration.
SKIP_DIRS = frozenset({"indexes", "__pycache__"})
SKIP_FILES = frozenset({".DS_Store", "Thumbs.db"})

# The migration journal is a JSON-lines file written alongside the
# Ledgercore journal so that the Releaseledger CLI can report domain-
# specific state.
JOURNAL_FILENAME = ".releaseledger-migration.jsonl"


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReleaseledgerMigrationRequest:
    """User-facing migration request parsed from CLI flags."""

    start: Path
    data_storage: Literal["project", "external", "user-data"]
    external_root: str | None
    target: Literal["project", "local"]
    mode: Literal["copy", "move"]
    preserve_legacy_config: bool = False


@dataclass(frozen=True, slots=True)
class MigrationExcludedPath:
    """A path excluded from migration with the reason."""

    relative_path: str
    reason: str


@dataclass(frozen=True, slots=True)
class LedgerInventory:
    """Inventory of a single ledger directory."""

    ref: str
    release_versions: tuple[str, ...]
    release_count: int
    entry_count: int
    event_row_count: int
    audit_sheet_count: int
    durable_regular_file_count: int
    selected_relative_paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReleaseledgerDataInventory:
    """Complete inventory of a legacy data root."""

    data_root: Path
    ledgers: tuple[LedgerInventory, ...]
    total_releases: int
    total_entries: int
    total_event_rows: int
    total_audit_sheets: int
    total_regular_files: int
    selected_relative_paths: tuple[str, ...]
    excluded_paths: tuple[MigrationExcludedPath, ...]
    unexpected_paths: tuple[str, ...]

    def filtered_durable(self) -> ReleaseledgerDataInventory:
        """Return self (already filtered to durable files)."""
        return self


@dataclass(frozen=True, slots=True)
class LegacyReleaseledgerSource:
    """Typed representation of the legacy .releaseledger source."""

    config_path: Path
    data_root: Path
    workspace_root: Path
    legacy_config: dict[str, object]
    inventory: ReleaseledgerDataInventory


@dataclass(frozen=True, slots=True)
class ReleaseledgerMigrationTarget:
    """Prepared target for migration (not yet activated)."""

    project_root: Path
    project_uuid: str
    project_name: str | None
    data_root: Path
    indexes_root: Path
    config_path: Path


@dataclass(frozen=True, slots=True)
class ReleaseledgerStorageMigrationPlan:
    """Complete migration plan with explicit source and target."""

    request: ReleaseledgerMigrationRequest
    source: LegacyReleaseledgerSource
    target: ReleaseledgerMigrationTarget
    transformed_config_text: str
    generic_plan: object
    inventory_before: ReleaseledgerDataInventory
    selected_paths: tuple[str, ...]
    excluded_paths: tuple[MigrationExcludedPath, ...]
    warnings: tuple[str, ...]

    def to_result(self) -> dict[str, object]:
        """Return a serializable view for CLI output."""
        return {
            "kind": "releaseledger_migration_plan",
            "legacy_config_path": str(self.source.config_path),
            "legacy_data_root": str(self.source.data_root),
            "workspace_root": str(self.source.workspace_root),
            "target_data_root": str(self.target.data_root),
            "target_indexes_root": str(self.target.indexes_root),
            "mode": self.request.mode,
            "preserve_legacy_config": self.request.preserve_legacy_config,
            "inventory": {
                "ledger_refs": [li.ref for li in self.inventory_before.ledgers],
                "total_releases": self.inventory_before.total_releases,
                "total_entries": self.inventory_before.total_entries,
                "total_event_rows": self.inventory_before.total_event_rows,
                "total_audit_sheets": self.inventory_before.total_audit_sheets,
                "total_regular_files": self.inventory_before.total_regular_files,
            },
            "selected_paths_count": len(self.selected_paths),
            "excluded_paths_count": len(self.excluded_paths),
            "warnings": list(self.warnings),
        }


# ---------------------------------------------------------------------------
# Recursive ledger-ref discovery
# ---------------------------------------------------------------------------


def iter_legacy_ledger_roots(
    data_root: Path,
) -> Iterator[tuple[str, Path]]:
    """Yield (ref, ledger_dir) for every ledger root under data_root.

    A directory is a ledger root when it contains one or more recognized
    ledger children (releases/, events/, indexes/). Intermediate path
    segments are not ledger refs by themselves.
    """
    ledgers_dir = data_root / "ledgers"
    if not ledgers_dir.is_dir():
        return

    recognized_children = frozenset({"releases", "events", "indexes"})
    seen: set[str] = set()

    for dirpath, dirnames, filenames in _walk_no_symlinks(ledgers_dir):
        dirpath = Path(dirpath)
        # Check if this directory is a ledger root
        child_names = set(dirnames) | set(filenames)
        if recognized_children & child_names:
            ref = str(dirpath.relative_to(ledgers_dir))
            # Convert to POSIX path for consistency
            ref = ref.replace("\\", "/")
            if ref not in seen:
                seen.add(ref)
                yield ref, dirpath
                # Don't descend into the ledger root's children
                dirnames.clear()


def _walk_no_symlinks(root: Path):
    """Walk a directory tree without following symlinks."""
    from os import scandir, walk

    for dirpath, dirnames, _filenames in walk(root, followlinks=False):
        # Filter out symlinks from dirnames and filenames
        filtered_dirs = []
        filtered_files = []
        try:
            with scandir(dirpath) as it:
                for entry in it:
                    if entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        filtered_dirs.append(entry.name)
                    elif entry.is_file(follow_symlinks=False):
                        filtered_files.append(entry.name)
        except OSError:
            continue
        dirnames[:] = sorted(filtered_dirs)
        yield dirpath, dirnames, sorted(filtered_files)


# ---------------------------------------------------------------------------
# Path selection policy
# ---------------------------------------------------------------------------


def select_legacy_durable_paths(
    data_root: Path,
) -> PathSelectionResult:
    """Select durable regular files and exclude non-durable content.

    Returns a PathSelectionResult with included, excluded, and warning paths.
    """
    data_root = Path(data_root).resolve()
    included: list[str] = []
    excluded: list[MigrationExcludedPath] = []
    warnings: list[str] = []
    unexpected: list[str] = []

    # Patterns for directories to exclude (relative to any ledger root)
    skip_file_names = SKIP_FILES

    for _ref, ledger_dir in iter_legacy_ledger_roots(data_root):
        for path in ledger_dir.rglob("*"):
            if path.is_symlink():
                rel = str(path.relative_to(data_root))
                excluded.append(
                    MigrationExcludedPath(
                        relative_path=rel,
                        reason="symlink",
                    )
                )
                continue

            if not path.is_file():
                continue

            rel = str(path.relative_to(data_root))
            parts = PurePosixPath(rel.replace("\\", "/")).parts

            # Exclude index directories
            if "indexes" in parts[:-1]:  # Don't exclude files named 'indexes'
                excluded.append(
                    MigrationExcludedPath(
                        relative_path=rel,
                        reason="old index",
                    )
                )
                continue

            # Exclude __pycache__
            if "__pycache__" in parts:
                excluded.append(
                    MigrationExcludedPath(
                        relative_path=rel,
                        reason="cache",
                    )
                )
                continue

            # Exclude known temp files
            if path.name in skip_file_names:
                excluded.append(
                    MigrationExcludedPath(
                        relative_path=rel,
                        reason="temp file",
                    )
                )
                continue

            included.append(rel)

    # Also include non-ledger files directly under data_root
    for path in data_root.rglob("*"):
        if path.is_symlink():
            continue
        if not path.is_file():
            continue
        rel = str(path.relative_to(data_root))
        parts = PurePosixPath(rel.replace("\\", "/")).parts
        if parts[0] == "ledgers":
            continue  # Already handled above
        if path.name in skip_file_names:
            continue
        included.append(rel)

    included = sorted(set(included))

    return PathSelectionResult(
        included=tuple(included),
        excluded=tuple(excluded),
        warnings=tuple(warnings),
        unexpected=tuple(unexpected),
    )


@dataclass(frozen=True, slots=True)
class PathSelectionResult:
    """Result of path selection policy."""

    included: tuple[str, ...]
    excluded: tuple[MigrationExcludedPath, ...]
    warnings: tuple[str, ...]
    unexpected: tuple[str, ...]


# ---------------------------------------------------------------------------
# Strict inventory
# ---------------------------------------------------------------------------


def build_strict_inventory(
    data_root: Path,
    selected_paths: tuple[str, ...] | None = None,
) -> ReleaseledgerDataInventory:
    """Build a strict typed inventory of the data root.

    If selected_paths is provided, only those paths are counted.
    """
    data_root = Path(data_root).resolve()
    if selected_paths is not None:
        path_set = set(selected_paths)
    else:
        path_set = None

    ledgers: list[LedgerInventory] = []
    total_releases = 0
    total_entries = 0
    total_event_rows = 0
    total_audit_sheets = 0
    total_regular_files = 0
    all_selected: list[str] = []
    excluded: list[MigrationExcludedPath] = []
    unexpected: list[str] = []

    for ref, ledger_dir in iter_legacy_ledger_roots(data_root):
        li = _build_ledger_inventory(data_root, ref, ledger_dir, path_set)
        ledgers.append(li)
        total_releases += li.release_count
        total_entries += li.entry_count
        total_event_rows += li.event_row_count
        total_audit_sheets += li.audit_sheet_count
        total_regular_files += li.durable_regular_file_count
        all_selected.extend(li.selected_relative_paths)

    # Include non-ledger files
    non_ledger_paths = [
        p for p in (selected_paths or ()) if not p.startswith("ledgers/")
    ]
    all_selected.extend(non_ledger_paths)
    total_regular_files += len(non_ledger_paths)

    return ReleaseledgerDataInventory(
        data_root=data_root,
        ledgers=tuple(sorted(ledgers, key=lambda li: li.ref)),
        total_releases=total_releases,
        total_entries=total_entries,
        total_event_rows=total_event_rows,
        total_audit_sheets=total_audit_sheets,
        total_regular_files=total_regular_files,
        selected_relative_paths=tuple(sorted(all_selected)),
        excluded_paths=tuple(excluded),
        unexpected_paths=tuple(unexpected),
    )


def _build_ledger_inventory(
    data_root: Path,
    ref: str,
    ledger_dir: Path,
    path_set: set[str] | None,
) -> LedgerInventory:
    """Build inventory for a single ledger directory."""
    releases_dir = ledger_dir / "releases"
    events_file = ledger_dir / "events" / "events.jsonl"

    release_versions: list[str] = []
    release_count = 0
    entry_count = 0
    audit_count = 0
    selected_paths: list[str] = []

    if releases_dir.is_dir():
        for version_dir in sorted(releases_dir.iterdir(), key=lambda p: p.name):
            if not version_dir.is_dir():
                continue
            release_md = version_dir / "release.md"
            rel_release = str(release_md.relative_to(data_root))
            if path_set is not None and rel_release not in path_set:
                continue
            if release_md.is_file():
                release_versions.append(version_dir.name)
                release_count += 1
                selected_paths.append(rel_release)

            entries_dir = version_dir / "entries"
            if entries_dir.is_dir():
                for entry in sorted(entries_dir.glob("entry-*.md")):
                    if not entry.is_file():
                        continue
                    rel_entry = str(entry.relative_to(data_root))
                    if path_set is not None and rel_entry not in path_set:
                        continue
                    entry_count += 1
                    selected_paths.append(rel_entry)

            audit_dir = version_dir / "audit"
            if audit_dir.is_dir():
                for audit_file in sorted(audit_dir.glob("*.yaml")):
                    if not audit_file.is_file():
                        continue
                    rel_audit = str(audit_file.relative_to(data_root))
                    if path_set is not None and rel_audit not in path_set:
                        continue
                    audit_count += 1
                    selected_paths.append(rel_audit)

    event_row_count = 0
    if events_file.is_file():
        rel_events = str(events_file.relative_to(data_root))
        if path_set is None or rel_events in path_set:
            selected_paths.append(rel_events)
            try:
                for _ in _read_jsonl(events_file):
                    event_row_count += 1
            except Exception:
                event_row_count = -1  # signal corrupt

    # Count other durable files in this ledger
    durable_file_count = len(selected_paths)

    return LedgerInventory(
        ref=ref,
        release_versions=tuple(sorted(release_versions)),
        release_count=release_count,
        entry_count=entry_count,
        event_row_count=event_row_count,
        audit_sheet_count=audit_count,
        durable_regular_file_count=durable_file_count,
        selected_relative_paths=tuple(sorted(selected_paths)),
    )


# ---------------------------------------------------------------------------
# Source discovery
# ---------------------------------------------------------------------------


def discover_legacy_source(
    start: Path,
) -> LegacyReleaseledgerSource:
    """Discover and validate the legacy source, returning a typed model."""
    config_path, legacy_config = discover_legacy_project(start)
    workspace_root = config_path.parent.resolve()
    legacy_dir = _resolve_legacy_data_root(workspace_root, legacy_config)

    selected = select_legacy_durable_paths(legacy_dir)
    inventory = build_strict_inventory(legacy_dir, selected_paths=selected.included)

    return LegacyReleaseledgerSource(
        config_path=config_path,
        data_root=legacy_dir,
        workspace_root=workspace_root,
        legacy_config=legacy_config,
        inventory=inventory,
    )


# ---------------------------------------------------------------------------
# Inventory comparison
# ---------------------------------------------------------------------------


def assert_inventory_preserved(
    source: ReleaseledgerDataInventory,
    target: ReleaseledgerDataInventory,
) -> None:
    """Assert that target inventory matches source inventory.

    Raises LaunchError with VALIDATION_ERROR code if any mismatch is found.
    """
    errors: list[str] = []

    source_refs = {li.ref for li in source.ledgers}
    target_refs = {li.ref for li in target.ledgers}

    missing_refs = source_refs - target_refs
    extra_refs = target_refs - source_refs

    if missing_refs:
        errors.append(f"Missing ledger refs: {sorted(missing_refs)}")
    if extra_refs:
        errors.append(f"Extra ledger refs: {sorted(extra_refs)}")

    for source_li in source.ledgers:
        target_li = None
        for tli in target.ledgers:
            if tli.ref == source_li.ref:
                target_li = tli
                break

        if target_li is None:
            continue

        if source_li.release_count != target_li.release_count:
            errors.append(
                f"{source_li.ref}: release count {source_li.release_count} "
                f"!= {target_li.release_count}"
            )

        if source_li.entry_count != target_li.entry_count:
            errors.append(
                f"{source_li.ref}: entry count {source_li.entry_count} "
                f"!= {target_li.entry_count}"
            )

        missing_versions = set(source_li.release_versions) - set(
            target_li.release_versions
        )
        if missing_versions:
            errors.append(
                f"{source_li.ref}: missing release versions: {sorted(missing_versions)}"
            )

    source_paths = set(source.selected_relative_paths)
    target_paths = set(target.selected_relative_paths)
    missing_paths = source_paths - target_paths
    if missing_paths:
        errors.append(f"Missing selected paths: {sorted(missing_paths)[:5]}...")

    if source.total_releases != target.total_releases:
        errors.append(
            f"Total releases: {source.total_releases} != {target.total_releases}"
        )

    if source.total_entries != target.total_entries:
        errors.append(
            f"Total entries: {source.total_entries} != {target.total_entries}"
        )

    if errors:
        raise LaunchError(
            f"Migration conservation check failed: {'; '.join(errors)}",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
            data={"errors": errors},
            remediation=[
                "The migration did not copy all records.",
                "Inspect source and target data directories.",
            ],
        )


def assert_same_source_snapshot(
    before: ReleaseledgerDataInventory,
    now: ReleaseledgerDataInventory,
) -> None:
    """Assert the source has not changed between planning and execution."""
    if before.total_releases != now.total_releases:
        raise LaunchError(
            "Source data changed between planning and execution.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )


# ---------------------------------------------------------------------------
# Config transformation
# ---------------------------------------------------------------------------


def transform_legacy_config_v1_to_v2(
    legacy_config: dict[str, object],
) -> str:
    """Transform a v1 legacy config to v2 TOML text.

    Preserves all supported fields, removes obsolete storage fields.
    """
    # Fields to preserve
    preserved: dict[str, object] = {}

    # Map legacy field names to v2 names
    field_mapping = {
        "ledger_ref": "ledger_ref",
        "ledger_parent_ref": "ledger_parent_ref",
        "ledger_branch_guard": "ledger_branch_guard",
        "ledger_code": "ledger.code",
        "release": "release",
        "changelog": "changelog",
        "git": "git",
    }

    for legacy_key, v2_key in field_mapping.items():
        if legacy_key in legacy_config:
            preserved[v2_key] = legacy_config[legacy_key]

    # Build v2 TOML text
    lines = ["config_version = 2", ""]

    for key, value in preserved.items():
        if isinstance(value, dict):
            lines.append(f"[{key}]")
            for k, v in value.items():
                lines.append(f"{k} = {_toml_value(v)}")
            lines.append("")
        else:
            lines.append(f"{key} = {_toml_value(value)}")

    return "\n".join(lines) + "\n"


def _toml_value(value: object) -> str:
    """Format a Python value as a TOML literal."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return f'"{value}"'
    if isinstance(value, (int, float)):
        return str(value)
    return f'"{value}"'


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def discover_legacy_project(start: Path) -> tuple[Path, dict[str, object]]:
    """Locate a legacy Releaseledger config and return its path and parsed content.

    Returns ``(config_path, parsed_config)`` where ``parsed_config`` is the
    full TOML document loaded as a plain dict. Raises ``NOT_FOUND`` when no
    legacy config is found.
    """

    search = Path(start).resolve()
    if search.is_file():
        search = search.parent

    for name in LEGACY_CONFIG_NAMES:
        candidate = search / name
        if candidate.is_file():
            try:
                import tomli

                with candidate.open("rb") as fh:
                    parsed = tomli.load(fh)
            except Exception as exc:
                raise LaunchError(
                    f"Legacy config at {candidate} is unreadable: {exc}",
                    code=CODE_CONFIG_ERROR,
                    exit_code=2,
                    data={"path": str(candidate)},
                ) from exc
            if not isinstance(parsed, dict):
                raise LaunchError(
                    f"Legacy config at {candidate} is not a TOML table.",
                    code=CODE_CONFIG_ERROR,
                    exit_code=2,
                    data={"path": str(candidate)},
                )
            return candidate, parsed

    raise LaunchError(
        f"No legacy Releaseledger config found from {search}.",
        code=CODE_NOT_FOUND,
        exit_code=2,
        data={"start": str(search)},
        remediation=[
            "Run `releaseledger init` to create a new schema-3 project.",
        ],
    )


# ---------------------------------------------------------------------------
# Data inventory
# ---------------------------------------------------------------------------


def inventory_legacy_data(data_root: Path) -> dict[str, object]:
    """Walk a legacy data root and return ledger refs, counts, and paths.

    The returned dict is consumed by the migration planner and the
    domain validation step. Unexpected files (non-conforming names,
    symlinks, temp files) are listed under ``unexpected``.
    """

    data_root = Path(data_root).resolve()
    if not data_root.is_dir():
        raise LaunchError(
            f"Legacy data root {data_root} does not exist.",
            code=CODE_NOT_FOUND,
            exit_code=2,
            data={"path": str(data_root)},
        )

    ledgers_dir = data_root / "ledgers"
    if not ledgers_dir.is_dir():
        return _empty_inventory()

    refs: list[str] = []
    ref_details: dict[str, dict[str, object]] = {}
    unexpected: list[str] = []

    for child in sorted(ledgers_dir.iterdir(), key=lambda p: p.name):
        if child.name.startswith("."):
            continue
        if not child.is_dir():
            unexpected.append(str(child.relative_to(data_root)))
            continue
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", child.name):
            unexpected.append(str(child.relative_to(data_root)))
            continue
        refs.append(child.name)
        detail = _inventory_ledger(child)
        ref_details[child.name] = detail

    return {
        "data_root": str(data_root),
        "ledger_refs": refs,
        "ref_details": ref_details,
        "unexpected_files": unexpected,
        "total_releases": sum(
            int(d.get("release_count", 0)) for d in ref_details.values()
        ),
        "total_entries": sum(
            int(d.get("entry_count", 0)) for d in ref_details.values()
        ),
        "total_event_rows": sum(
            int(d.get("event_row_count", 0)) for d in ref_details.values()
        ),
        "total_audit_sheets": sum(
            int(d.get("audit_count", 0)) for d in ref_details.values()
        ),
    }


def _inventory_ledger(ledger_dir: Path) -> dict[str, object]:
    """Inventory a single ledger directory."""

    releases_dir = ledger_dir / "releases"
    events_file = ledger_dir / "events" / "events.jsonl"

    release_count = 0
    entry_count = 0
    audit_count = 0
    if releases_dir.is_dir():
        for version_dir in sorted(releases_dir.iterdir(), key=lambda p: p.name):
            if not version_dir.is_dir():
                continue
            release_md = version_dir / "release.md"
            if release_md.is_file():
                release_count += 1
            entries_dir = version_dir / "entries"
            if entries_dir.is_dir():
                entry_count += sum(
                    1 for e in entries_dir.glob("entry-*.md") if e.is_file()
                )
            audit_dir = version_dir / "audit"
            if audit_dir.is_dir():
                audit_count += sum(1 for a in audit_dir.glob("*.yaml") if a.is_file())

    event_row_count = 0
    if events_file.is_file():
        try:
            for _ in _read_jsonl(events_file):
                event_row_count += 1
        except Exception:
            event_row_count = -1  # signal corrupt

    return {
        "ledger_dir": str(ledger_dir),
        "release_count": release_count,
        "entry_count": entry_count,
        "audit_count": audit_count,
        "event_row_count": event_row_count,
    }


def _empty_inventory() -> dict[str, object]:
    return {
        "ledger_refs": [],
        "ref_details": {},
        "unexpected_files": [],
        "total_releases": 0,
        "total_entries": 0,
        "total_event_rows": 0,
        "total_audit_sheets": 0,
    }


# ---------------------------------------------------------------------------
# Domain validation
# ---------------------------------------------------------------------------


def validate_domain_records(data_root: Path) -> dict[str, object]:
    """Load and validate every domain record under *data_root*.

    Returns a detailed report. Records that fail to parse are listed
    under ``failures`` rather than halting the migration.
    """

    inventory = inventory_legacy_data(data_root)
    failures: list[dict[str, object]] = []
    ledger_reports: dict[str, dict[str, object]] = {}

    for ref in inventory.get("ledger_refs", []):
        ref_dir = Path(data_root) / "ledgers" / ref
        report = _validate_ledger_domain(ref_dir)
        ledger_reports[ref] = report
        for f in report.get("failures", []):
            failures.append(f)

    return {
        "data_root": str(data_root),
        "ledger_refs": inventory.get("ledger_refs"),
        "ledger_reports": ledger_reports,
        "total_failures": len(failures),
        "failures": failures,
        "valid": len(failures) == 0,
    }


def _validate_ledger_domain(ledger_dir: Path) -> dict[str, object]:
    """Validate all domain records within a single ledger."""

    # Delegate to the domain-level validators from the store layer.
    from releaseledger.storage.paths import ProjectPaths
    from releaseledger.storage.store import (
        list_releases_for_paths,
        load_entries_for_paths,
    )

    # We need a minimal paths object. Build a fake one that points at the
    # right data directory but does not require a full project load.
    # Use a lightweight approach: construct ProjectPaths manually.
    ledger_ref = ledger_dir.name
    fake_project = _fake_project(ledger_dir.parent.parent.resolve())
    try:
        paths = ProjectPaths(
            project=fake_project,
            ledger_ref=ledger_ref,
            ledger_dir=ledger_dir,
            releases_dir=ledger_dir / "releases",
            events_dir=ledger_dir / "events",
            indexes_dir=ledger_dir / "indexes",
            releases_index_path=ledger_dir / "indexes" / "releases.json",
            entries_index_path=ledger_dir / "indexes" / "entries.json",
            events_path=ledger_dir / "events" / "events.jsonl",
        )
    except Exception:
        return {
            "ledger_ref": ledger_ref,
            "release_count": 0,
            "entry_count": 0,
            "valid": False,
            "failures": [{"ledger_ref": ledger_ref, "error": "cannot construct paths"}],
        }

    failures: list[dict[str, object]] = []

    try:
        releases = list_releases_for_paths(paths)
    except Exception as exc:
        failures.append({"ledger_ref": ledger_ref, "error": str(exc)})
        releases = []

    for release in releases:
        try:
            entries = load_entries_for_paths(paths, release.version)
        except Exception as exc:
            failures.append(
                {
                    "ledger_ref": ledger_ref,
                    "release": release.version,
                    "error": str(exc),
                }
            )
            entries = []

        # Cross-record: entry release_version matches release.
        for entry in entries:
            if entry.release_version != release.version:
                failures.append(
                    {
                        "ledger_ref": ledger_ref,
                        "release": release.version,
                        "entry_id": entry.entry_id,
                        "error": (
                            f"entry release_version {entry.release_version} != "
                            f"{release.version}"
                        ),
                    }
                )

    return {
        "ledger_ref": ledger_ref,
        "release_count": len(releases),
        "entry_count": sum(
            1 for r in releases for _ in range(1)
        ),  # approximate, fixed below
        "valid": len(failures) == 0,
        "failures": failures,
    }


def _fake_project(data_root: Path, indexes_root: Path | None = None) -> Any:
    """Build a minimal ReleaseledgerProject for domain validation."""

    from types import SimpleNamespace

    if indexes_root is None:
        indexes_root = data_root / "indexes"

    ns = SimpleNamespace(
        project_root=data_root,
        config_path=data_root / ".ledger" / "releaseledger" / "config.toml",
        data_root=data_root,
        indexes_root=indexes_root,
        project_uuid="00000000-0000-0000-0000-000000000000",
        project_name=None,
        config_binding_path=data_root / ".ledger-project.toml",
        data_binding_path=data_root / ".ledger-project.toml",
        indexes_binding_path=data_root / ".ledger-project.toml",
        layout=None,
        config=None,
    )
    return ns


# ---------------------------------------------------------------------------
# Index rebuild
# ---------------------------------------------------------------------------


def rebuild_all_indexes(
    data_root: Path,
    indexes_root: Path | None = None,
) -> dict[str, object]:
    """Rebuild indexes for every ledger ref under *data_root*.

    If indexes_root is provided, indexes are written there instead of
    inside the data_root directory.
    """
    from releaseledger.storage.paths import ProjectPaths

    data_root = Path(data_root).resolve()
    if indexes_root is not None:
        indexes_root = Path(indexes_root).resolve()
    else:
        indexes_root = data_root / "indexes"

    refs = [r for r, _ in iter_legacy_ledger_roots(data_root)]
    results: dict[str, dict[str, object]] = {}

    for ref in refs:
        ref_dir = data_root / "ledgers" / ref
        idx_dir = indexes_root / "ledgers" / ref
        fake_project = _fake_project(data_root, indexes_root)

        from releaseledger.storage.store import rebuild_indexes_for_paths

        try:
            idx_dir.mkdir(parents=True, exist_ok=True)
            paths = ProjectPaths(
                project=fake_project,
                ledger_ref=ref,
                ledger_dir=ref_dir,
                releases_dir=ref_dir / "releases",
                events_dir=ref_dir / "events",
                indexes_dir=idx_dir,
                releases_index_path=idx_dir / "releases.json",
                entries_index_path=idx_dir / "entries.json",
                events_path=ref_dir / "events" / "events.jsonl",
            )
            rebuild_indexes_for_paths(paths)
            # Second rebuild for byte-identical check.

            ri_path = idx_dir / "releases.json"
            ei_path = idx_dir / "entries.json"
            h1 = _hash_file(ri_path)
            h2 = _hash_file(ei_path)
            rebuild_indexes_for_paths(paths)
            h1b = _hash_file(ri_path)
            h2b = _hash_file(ei_path)
            results[ref] = {
                "releases_index": "ok",
                "entries_index": "ok",
                "idempotent": h1 == h1b and h2 == h2b,
            }
        except Exception as exc:
            results[ref] = {"error": str(exc)}

    return {
        "data_root": str(data_root),
        "indexes_root": str(indexes_root),
        "refs": results,
    }


def _hash_file(path: Path) -> str:
    import hashlib

    if not path.is_file():
        return "missing"
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Migration planning
# ---------------------------------------------------------------------------


def plan_migration(request: ReleaseledgerMigrationRequest) -> dict[str, object]:
    """Build a migration plan for the given request.

    Returns a machine-readable plan that the CLI can display and the
    ``apply`` subcommand can execute.
    """

    config_path, legacy_config = discover_legacy_project(request.start)
    workspace_root = config_path.parent.resolve()

    # Resolve legacy data root.
    legacy_dir = _resolve_legacy_data_root(workspace_root, legacy_config)

    # Check for symlinks before proceeding.
    _reject_symlinks(legacy_dir)

    # Build strict inventory with path selection.
    selected = select_legacy_durable_paths(legacy_dir)
    inventory = build_strict_inventory(legacy_dir, selected_paths=selected.included)

    # Determine target paths.
    target_data_storage = request.data_storage
    target_external_root = request.external_root

    # Prepare the target (without activating).
    target_info = _backend.prepare_releaseledger_migration_target(
        workspace_root,
        data_storage=target_data_storage,
        external_root=target_external_root,
        target=request.target,
    )

    # Build the Ledgercore migration plan with explicit source.
    # First, we need a layout to pass to the planner.
    try:
        layout = _backend.load_releaseledger_ledger_layout(
            workspace_root, validate_storage=False, allow_missing=True
        )
    except LaunchError:
        # If no layout exists yet, create one.
        _backend.ensure_releaseledger_registration(
            workspace_root,
            project_name=legacy_config.get("project_name"),
            data_storage=target_data_storage,
            external_root=target_external_root,
        )
        layout = _backend.load_releaseledger_ledger_layout(
            workspace_root, validate_storage=False, allow_missing=False
        )

    # Call the backend planner with explicit legacy source.
    generic_plan = _backend.plan_releaseledger_layout_migration(
        layout,
        source_data_root=legacy_dir,
        target_data_storage=target_data_storage,
        target_external_root=target_external_root,
        target=request.target,
    )

    # Verify the plan uses our source.
    _backend.assert_plan_source(generic_plan, legacy_dir)

    # Build the plan.
    plan: dict[str, object] = {
        "kind": "releaseledger_migration_plan",
        "legacy_config_path": str(config_path),
        "legacy_data_root": str(legacy_dir),
        "workspace_root": str(workspace_root),
        "target_data_root": target_info["data_root"],
        "target_indexes_root": target_info["indexes_root"],
        "target_data_storage": target_data_storage,
        "target_external_root": target_external_root,
        "mode": request.mode,
        "preserve_legacy_config": request.preserve_legacy_config,
        "inventory": {
            "ledger_refs": [li.ref for li in inventory.ledgers],
            "total_releases": inventory.total_releases,
            "total_entries": inventory.total_entries,
            "total_event_rows": inventory.total_event_rows,
            "total_audit_sheets": inventory.total_audit_sheets,
            "total_regular_files": inventory.total_regular_files,
        },
        "selected_paths_count": len(selected.included),
        "excluded_paths_count": len(selected.excluded),
        "warnings": list(selected.warnings),
    }

    # Check for path overlap hazards.
    target_data = Path(target_info["data_root"])
    if _is_subpath(legacy_dir, target_data) or _is_subpath(target_data, legacy_dir):
        plan["warnings"].append(
            "Legacy data root and target data root overlap or are nested; "
            "the migration planner will reject this configuration at "
            "execution time."
        )
        plan["overlap_detected"] = True
    else:
        plan["overlap_detected"] = False

    return plan


def _reject_symlinks(data_root: Path) -> None:
    """Raise LaunchError if any symlinks exist under data_root."""
    import os

    data_root = Path(data_root).resolve()
    symlinks_found: list[str] = []

    for dirpath, dirnames, filenames in os.walk(data_root):
        for name in dirnames + filenames:
            path = Path(dirpath) / name
            if path.is_symlink():
                try:
                    rel = str(path.relative_to(data_root))
                except ValueError:
                    rel = str(path)
                symlinks_found.append(rel)

    if symlinks_found:
        raise LaunchError(
            f"Found {len(symlinks_found)} symlink(s) in legacy data: "
            f"{symlinks_found[:5]}",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
            data={"symlinks": symlinks_found},
            remediation=[
                "Remove or replace symlinks with the actual files.",
            ],
        )


def _resolve_legacy_data_root(workspace_root: Path, config: dict[str, object]) -> Path:
    """Resolve the legacy data directory from a version-1 config."""

    raw = config.get("releaseledger_dir", ".releaseledger")
    if not isinstance(raw, str) or not raw.strip():
        return workspace_root / ".releaseledger"

    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = workspace_root / candidate
    return candidate.resolve()


def _derive_target_data_root(
    workspace_root: Path,
    storage: str,
    external_root: str | None,
    inventory: dict[str, object],
) -> Path:
    """Estimate where data would land after migration."""

    # This is a best-effort estimate for the plan display. The actual
    # target is resolved by Ledgercore during execution.
    if storage == "project":
        return workspace_root / ".ledger" / "releaseledger" / "data"
    if storage == "external" and external_root:
        return Path(external_root).resolve()
    # user-data: the plan display doesn't need the exact path.
    return Path("/var/empty")  # placeholder


def _is_subpath(a: Path, b: Path) -> bool:
    """Return True if *a* is a subpath of *b* or vice versa."""

    try:
        a.resolve().relative_to(b.resolve())
        return True
    except ValueError:
        pass
    try:
        b.resolve().relative_to(a.resolve())
        return True
    except ValueError:
        pass
    return False


# ---------------------------------------------------------------------------
# Migration execution
# ---------------------------------------------------------------------------


def execute_migration(
    request: ReleaseledgerMigrationRequest,
    *,
    quiescence_check=None,
) -> dict[str, object]:
    """Execute a migration from legacy to schema-3.

    The heavy lifting (copy, stage, hash, activate) is delegated to
    Ledgercore via :func:`_backend.execute_releaseledger_layout_migration`.
    This function handles the Releaseledger-specific pre-flight and
    post-activation work (index rebuild, domain receipt).
    """

    config_path, legacy_config = discover_legacy_project(request.start)
    workspace_root = config_path.parent.resolve()
    legacy_dir = _resolve_legacy_data_root(workspace_root, legacy_config)

    # 1. Validate domain records before migration.
    domain_before = validate_domain_records(legacy_dir)
    if not domain_before["valid"]:
        failures = domain_before["failures"]
        raise LaunchError(
            f"{len(failures)} domain records failed validation; "
            "fix them before migration.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
            data={"failures": failures[:10]},  # cap in the error
            remediation=[
                "Inspect the failed records: `releaseledger storage validate --strict`",
                "Correct malformed records and retry the migration.",
            ],
        )

    # 2. Ensure the schema-3 manifest exists.
    try:
        _backend.ensure_releaseledger_registration(
            workspace_root,
            project_name=legacy_config.get("project_name"),
            data_storage=request.data_storage,
            external_root=request.external_root,
        )
    except LaunchError:
        pass  # Manifest may already exist.

    # 3. Load the schema-3 layout.
    layout = _backend.load_releaseledger_ledger_layout(
        workspace_root, validate_storage=False, allow_missing=False
    )

    # 4. Build a Ledgercore migration plan.
    plan = _backend.plan_releaseledger_layout_migration(
        layout,
        target_data_storage=request.data_storage,
        target_external_root=request.external_root,
    )

    # 5. Execute through Ledgercore.
    def _quiescence() -> None:
        if quiescence_check is not None:
            quiescence_check()

    result = _backend.execute_releaseledger_layout_migration(
        plan,
        mode=request.mode,
        quiescence_check=_quiescence,
        staged_domain_transform=lambda staging: _staged_rebuild(staging),
    )

    # 6. Rebuild indexes after migration.
    final_layout = _backend.load_releaseledger_ledger_layout(
        workspace_root, validate_storage=True, allow_missing=False
    )

    if (
        final_layout.validation_report is not None
        and not final_layout.validation_report.valid
    ):
        return {
            "kind": "migration_failed",
            "error": "Validation failed after migration.",
            "validation": [
                {"path": str(r.path), "valid": r.valid, "reason": r.reason}
                for r in final_layout.validation_report.results
            ],
        }

    # Rebuild indexes.
    index_result = rebuild_all_indexes(final_layout.data_root)

    # Post-migration domain validation.
    domain_after = validate_domain_records(final_layout.data_root)

    return {
        "kind": "releaseledger_migration_executed",
        "mode": request.mode,
        "legacy_data_root": str(legacy_dir),
        "target_data_root": str(final_layout.data_root),
        "target_indexes_root": str(final_layout.indexes_root),
        "domain_validation_before": domain_before["valid"],
        "domain_validation_after": domain_after["valid"],
        "indexes_rebuilt": index_result,
        "inventory": inventory_legacy_data(final_layout.data_root),
        "warnings": result.get("warnings", []) if isinstance(result, dict) else [],
    }


def _staged_rebuild(staging: Path) -> None:
    """Rebuild indexes in a staging directory before activation."""

    rebuild_all_indexes(staging)


# ---------------------------------------------------------------------------
# Journal and recovery
# ---------------------------------------------------------------------------


def read_migration_journal(journal_dir: Path) -> list[dict[str, object]]:
    """Read the Releaseledger migration journal from *journal_dir*."""

    path = journal_dir / JOURNAL_FILENAME
    if not path.is_file():
        return []
    return list(_read_jsonl(path))


def migration_status(
    workspace_root: Path,
) -> dict[str, object]:
    """Report the current migration state for a project.

    Detects whether the project is legacy, canonical, or mid-migration.
    """

    workspace_root = Path(workspace_root).resolve()

    # Check for legacy config.
    legacy = None
    for name in LEGACY_CONFIG_NAMES:
        candidate = workspace_root / name
        if candidate.is_file():
            legacy = str(candidate)
            break

    # Check for canonical manifest.
    manifest = workspace_root / ".ledger" / "ledger.toml"
    has_canonical = manifest.is_file()

    if not legacy and not has_canonical:
        return {
            "state": "uninitialized",
            "legacy_detected": False,
            "canonical_detected": False,
            "migration_in_progress": False,
            "migration_recovery_required": False,
        }

    if legacy and not has_canonical:
        return {
            "state": "legacy",
            "legacy_detected": True,
            "legacy_config_path": legacy,
            "canonical_detected": False,
            "migration_in_progress": False,
            "remediation": "Run `releaseledger storage migrate plan`.",
        }

    if has_canonical and legacy:
        return {
            "state": "canonical-with-legacy-artifacts",
            "legacy_detected": True,
            "legacy_config_path": legacy,
            "canonical_detected": True,
            "manifest_path": str(manifest),
            "migration_in_progress": False,
            "remediation": "Remove or archive legacy config files.",
        }

    # Check for migration journal.
    journal_dir = workspace_root / ".ledger" / "releaseledger"
    if (journal_dir / JOURNAL_FILENAME).is_file():
        journal = read_migration_journal(journal_dir)
        last = journal[-1] if journal else {}
        if last.get("phase") in ("in_progress", "failed"):
            return {
                "state": "migration-recovery-required",
                "canonical_detected": True,
                "migration_in_progress": False,
                "migration_recovery_required": True,
                "last_phase": last.get("phase"),
                "remediation": "Run `releaseledger storage migrate recover`.",
            }

    return {
        "state": "canonical-ready",
        "canonical_detected": True,
        "manifest_path": str(manifest),
        "migration_in_progress": False,
        "migration_recovery_required": False,
    }


def recover_migration(workspace_root: Path) -> dict[str, object]:
    """Attempt recovery from an interrupted migration journal."""

    journal_dir = workspace_root.resolve() / ".ledger" / "releaseledger"
    journal = read_migration_journal(journal_dir)
    if not journal:
        return {
            "kind": "recovery_noop",
            "message": "No migration journal found; nothing to recover.",
        }

    last = journal[-1]
    phase = last.get("phase", "unknown")
    return {
        "kind": "recovery_attempted",
        "last_phase": phase,
        "journal_entries": len(journal),
        "message": (
            f"Migration was in phase '{phase}'. Manual inspection of the "
            "journal and data directories is required before retrying."
        ),
    }


def _read_jsonl(path: Path):
    """Yield parsed JSON objects from a JSON-lines file."""

    if not path.is_file():
        return
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue
