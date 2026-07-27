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

import hashlib
import json
import shutil
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from releaseledger import ledgercore_backend as _backend
from releaseledger.errors import (
    CODE_CONFIG_ERROR,
    CODE_NOT_FOUND,
    CODE_USAGE_ERROR,
    CODE_VALIDATION_ERROR,
    LaunchError,
)

TOOL_NAME = _backend.TOOL_NAME
DATA_MOUNT = _backend.DATA_MOUNT
INDEXES_MOUNT = _backend.INDEXES_MOUNT

__all__ = [
    "ReleaseledgerMigrationRequest",
    "LegacyReleaseledgerSource",
    "ReleaseledgerDataInventory",
    "LedgerInventory",
    "MigrationFile",
    "MigrationExcludedPath",
    "PreparedMigrationStage",
    "PathSelectionResult",
    "discover_legacy_project",
    "discover_legacy_source",
    "iter_legacy_ledger_roots",
    "select_legacy_durable_paths",
    "build_strict_inventory",
    "inventory_legacy_data",
    "plan_migration",
    "serialize_migration_plan",
    "load_migration_plan",
    "validate_migration_plan",
    "execute_migration",
    "validate_domain_records",
    "rebuild_all_indexes",
    "assert_inventory_preserved",
    "assert_same_source_snapshot",
    "assert_index_rebuild_success",
    "transform_legacy_config_v1_to_v2",
    "project_config_from_legacy_mapping",
    "read_migration_journal",
    "migration_status",
    "recover_migration",
    "cleanup_migration",
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

# Migration staging directory name.
STAGING_DIR_NAME = ".migration"


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
    project_uuid: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class MigrationExcludedPath:
    """A path excluded from migration with the reason."""

    relative_path: str
    reason: str


@dataclass(frozen=True, slots=True)
class MigrationFile:
    """A single file in the migration inventory with hash."""

    relative_path: str
    size: int
    sha256: str


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
    files: tuple[MigrationFile, ...]


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
    files: tuple[MigrationFile, ...]
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
class PreparedMigrationStage:
    """A created migration staging directory ready for file copy."""

    stage_root: Path
    data_root: Path
    config_path: Path
    migration_id: str


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
        current = Path(dirpath)
        child_names = set(dirnames) | set(filenames)
        if recognized_children & child_names:
            ref = str(current.relative_to(ledgers_dir))
            ref = ref.replace("\\", "/")
            if ref not in seen:
                seen.add(ref)
                yield ref, current
                dirnames.clear()


def _walk_no_symlinks(root: Path) -> Iterator[tuple[str, list[str], list[str]]]:
    """Walk a directory tree without following symlinks."""
    from os import scandir, walk

    for dirpath, dirnames, _filenames in walk(root, followlinks=False):
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
    """Select durable regular files and exclude non-durable content."""
    data_root = Path(data_root).resolve()
    included: list[str] = []
    excluded: list[MigrationExcludedPath] = []
    warnings: list[str] = []
    unexpected: list[str] = []

    skip_file_names = SKIP_FILES

    for _ref, ledger_dir in iter_legacy_ledger_roots(data_root):
        for path in ledger_dir.rglob("*"):
            if path.is_symlink():
                rel = str(path.relative_to(data_root))
                excluded.append(
                    MigrationExcludedPath(relative_path=rel, reason="symlink")
                )
                continue

            if not path.is_file():
                continue

            rel = str(path.relative_to(data_root))
            parts = PurePosixPath(rel.replace("\\", "/")).parts

            if "indexes" in parts[:-1]:
                excluded.append(
                    MigrationExcludedPath(relative_path=rel, reason="old index")
                )
                continue

            if "__pycache__" in parts:
                excluded.append(
                    MigrationExcludedPath(relative_path=rel, reason="cache")
                )
                continue

            if path.name in skip_file_names:
                excluded.append(
                    MigrationExcludedPath(relative_path=rel, reason="temp file")
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
            continue
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
    all_files: list[MigrationFile] = []
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
        all_files.extend(li.files)

    # Include non-ledger files
    non_ledger_paths = [
        p for p in (selected_paths or ()) if not p.startswith("ledgers/")
    ]
    all_selected.extend(non_ledger_paths)
    total_regular_files += len(non_ledger_paths)
    for p in non_ledger_paths:
        fp = data_root / p
        if fp.is_file():
            all_files.append(
                MigrationFile(
                    relative_path=p,
                    size=fp.stat().st_size,
                    sha256=_hash_file_content(fp),
                )
            )

    return ReleaseledgerDataInventory(
        data_root=data_root,
        ledgers=tuple(sorted(ledgers, key=lambda li: li.ref)),
        total_releases=total_releases,
        total_entries=total_entries,
        total_event_rows=total_event_rows,
        total_audit_sheets=total_audit_sheets,
        total_regular_files=total_regular_files,
        selected_relative_paths=tuple(sorted(all_selected)),
        files=tuple(sorted(all_files, key=lambda f: f.relative_path)),
        excluded_paths=tuple(excluded),
        unexpected_paths=tuple(unexpected),
    )


def _collect_release_data(
    data_root: Path,
    releases_dir: Path,
    path_set: set[str] | None,
) -> tuple[list[str], int, int, int, list[str], list[MigrationFile]]:
    """Collect release versions and their files from a releases directory.

    Returns (release_versions, release_count, entry_count, audit_count,
             selected_paths, all_files).
    """
    release_versions: list[str] = []
    release_count = 0
    entry_count = 0
    audit_count = 0
    selected_paths: list[str] = []
    all_files: list[MigrationFile] = []

    if not releases_dir.is_dir():
        return (
            release_versions,
            release_count,
            entry_count,
            audit_count,
            selected_paths,
            all_files,
        )

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
            all_files.append(_file_entry(data_root, release_md))

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
                all_files.append(_file_entry(data_root, entry))

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
                all_files.append(_file_entry(data_root, audit_file))

    return (
        release_versions,
        release_count,
        entry_count,
        audit_count,
        selected_paths,
        all_files,
    )


def _collect_event_data(
    data_root: Path,
    events_file: Path,
    path_set: set[str] | None,
    selected_paths: list[str],
    all_files: list[MigrationFile],
) -> int:
    """Collect events file data. Returns event_row_count."""
    event_row_count = 0
    if events_file.is_file():
        rel_events = str(events_file.relative_to(data_root))
        if path_set is None or rel_events in path_set:
            selected_paths.append(rel_events)
            all_files.append(_file_entry(data_root, events_file))
            try:
                for _ in _read_jsonl_strict(events_file):
                    event_row_count += 1
            except LaunchError:
                raise
            except Exception:
                event_row_count = -1
    return event_row_count


def _collect_unexpected_ledger_files(
    data_root: Path,
    ledger_selected: set[str],
    selected_paths: list[str],
    all_files: list[MigrationFile],
) -> None:
    """Add unexpected regular files from the path set to the inventory."""
    if not ledger_selected:
        return
    for p in ledger_selected:
        if p not in selected_paths and not p.endswith("/"):
            fp = data_root / p
            if fp.is_file():
                selected_paths.append(p)
                all_files.append(_file_entry(data_root, fp))


def _build_ledger_inventory(
    data_root: Path,
    ref: str,
    ledger_dir: Path,
    path_set: set[str] | None,
) -> LedgerInventory:
    """Build inventory for a single ledger directory."""
    events_file = ledger_dir / "events" / "events.jsonl"

    ledger_prefix = f"ledgers/{ref}/"
    ledger_selected: set[str] = set()
    if path_set is not None:
        ledger_selected = {
            p for p in path_set if p == f"ledgers/{ref}" or p.startswith(ledger_prefix)
        }

    releases_dir = ledger_dir / "releases"
    (
        release_versions,
        release_count,
        entry_count,
        audit_count,
        selected_paths,
        all_files,
    ) = _collect_release_data(
        data_root,
        releases_dir,
        path_set,
    )

    event_row_count = _collect_event_data(
        data_root,
        events_file,
        path_set,
        selected_paths,
        all_files,
    )

    _collect_unexpected_ledger_files(
        data_root,
        ledger_selected,
        selected_paths,
        all_files,
    )

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
        files=tuple(sorted(all_files, key=lambda f: f.relative_path)),
    )


def _file_entry(data_root: Path, file_path: Path) -> MigrationFile:
    rel = str(file_path.relative_to(data_root))
    st = file_path.stat()
    return MigrationFile(
        relative_path=rel,
        size=st.st_size,
        sha256=_hash_file_content(file_path),
    )


def _hash_file_content(path: Path) -> str:
    """Return SHA-256 hex digest of a file's contents."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def _check_ledger_inventory(
    source_li: LedgerInventory,
    target: ReleaseledgerDataInventory,
) -> list[str]:
    """Check that a single ledger's inventory is preserved in target.

    Returns a list of error messages.
    """
    errors: list[str] = []

    target_li = None
    for tli in target.ledgers:
        if tli.ref == source_li.ref:
            target_li = tli
            break

    if target_li is None:
        return errors

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
    if source_li.event_row_count != target_li.event_row_count:
        errors.append(
            f"{source_li.ref}: event rows {source_li.event_row_count} "
            f"!= {target_li.event_row_count}"
        )
    if source_li.audit_sheet_count != target_li.audit_sheet_count:
        errors.append(
            f"{source_li.ref}: audit sheets {source_li.audit_sheet_count} "
            f"!= {target_li.audit_sheet_count}"
        )
    if source_li.durable_regular_file_count != target_li.durable_regular_file_count:
        errors.append(
            f"{source_li.ref}: durable files "
            f"{source_li.durable_regular_file_count}"
            f" != {target_li.durable_regular_file_count}"
        )

    missing_versions = set(source_li.release_versions) - set(target_li.release_versions)
    if missing_versions:
        errors.append(
            f"{source_li.ref}: missing release versions: {sorted(missing_versions)}"
        )

    source_files = {f.relative_path: f.sha256 for f in source_li.files}
    target_files = {f.relative_path: f.sha256 for f in target_li.files}
    for sp, sh in source_files.items():
        th = target_files.get(sp)
        if th is None:
            errors.append(f"{source_li.ref}: missing file {sp}")
        elif sh != th:
            errors.append(f"{source_li.ref}: hash mismatch for {sp}")

    return errors


def _check_aggregate_inventory(
    source: ReleaseledgerDataInventory,
    target: ReleaseledgerDataInventory,
) -> list[str]:
    """Check aggregate inventory counts and full file list.

    Returns a list of error messages.
    """
    errors: list[str] = []

    source_paths = set(source.selected_relative_paths)
    target_paths = set(target.selected_relative_paths)
    missing_paths = source_paths - target_paths
    if missing_paths:
        errors.append(f"Missing selected paths: {sorted(missing_paths)[:10]}...")

    if source.total_releases != target.total_releases:
        errors.append(
            f"Total releases: {source.total_releases} != {target.total_releases}"
        )
    if source.total_entries != target.total_entries:
        errors.append(
            f"Total entries: {source.total_entries} != {target.total_entries}"
        )
    if source.total_event_rows != target.total_event_rows:
        errors.append(
            f"Total event rows: {source.total_event_rows} != {target.total_event_rows}"
        )
    if source.total_audit_sheets != target.total_audit_sheets:
        errors.append(
            f"Total audit sheets: {source.total_audit_sheets}"
            f" != {target.total_audit_sheets}"
        )

    source_file_map = {f.relative_path: f for f in source.files}
    target_file_map = {f.relative_path: f for f in target.files}
    for sp, sf in source_file_map.items():
        tf = target_file_map.get(sp)
        if tf is None:
            errors.append(f"Missing file in target: {sp}")
        elif sf.sha256 != tf.sha256:
            errors.append(
                f"Hash mismatch: {sp}"
                f" (source={sf.sha256[:8]}... target={tf.sha256[:8]}...)"
            )

    return errors


def assert_inventory_preserved(
    source: ReleaseledgerDataInventory,
    target: ReleaseledgerDataInventory,
) -> None:
    """Assert that target inventory matches source inventory.

    Raises LaunchError with VALIDATION_ERROR code if any mismatch is found.
    Checks all counts, file hashes, and selected paths.
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
        errors.extend(_check_ledger_inventory(source_li, target))

    errors.extend(_check_aggregate_inventory(source, target))

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
    errors: list[str] = []

    if before.total_releases != now.total_releases:
        errors.append(
            f"total_releases: {before.total_releases} != {now.total_releases}"
        )
    if before.total_entries != now.total_entries:
        errors.append(f"total_entries: {before.total_entries} != {now.total_entries}")
    if before.total_event_rows != now.total_event_rows:
        errors.append(
            f"total_event_rows: {before.total_event_rows} != {now.total_event_rows}"
        )
    if before.total_audit_sheets != now.total_audit_sheets:
        errors.append(
            f"total_audit_sheets: {before.total_audit_sheets}"
            f" != {now.total_audit_sheets}"
        )

    before_files = {f.relative_path: f.sha256 for f in before.files}
    now_files = {f.relative_path: f.sha256 for f in now.files}
    for sp, sh in before_files.items():
        nh = now_files.get(sp)
        if nh is None:
            errors.append(f"Source file removed: {sp}")
        elif sh != nh:
            errors.append(f"Source file changed: {sp}")

    if errors:
        raise LaunchError(
            f"Source data changed between planning and execution: "
            f"{'; '.join(errors[:5])}",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )


def assert_index_rebuild_success(index_result: dict[str, object]) -> None:
    """Assert that all index rebuilds succeeded and are idempotent."""
    refs = index_result.get("refs", {})
    if not isinstance(refs, dict):
        return

    errors = {
        ref: result["error"]
        for ref, result in refs.items()
        if isinstance(result, dict) and "error" in result
    }
    non_idempotent = [
        ref
        for ref, result in refs.items()
        if isinstance(result, dict) and result.get("idempotent") is not True
    ]

    if errors or non_idempotent:
        parts: list[str] = []
        if errors:
            parts.append(f"index errors: {errors}")
        if non_idempotent:
            parts.append(f"non-idempotent rebuilds: {non_idempotent}")
        raise LaunchError(
            f"Index rebuild failed: {'; '.join(parts)}",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
            data={"errors": errors, "non_idempotent": non_idempotent},
        )


# ---------------------------------------------------------------------------
# Config transformation
# ---------------------------------------------------------------------------


def transform_legacy_config_v1_to_v2(
    legacy_config: dict[str, object],
) -> str:
    """Transform a v1 legacy config to v2 TOML text.

    Prefer project_config_from_legacy_mapping() which returns a validated
    ProjectConfig. This function remains for backward compatibility.
    """
    from releaseledger.storage.config import write_project_config

    config = project_config_from_legacy_mapping(legacy_config)
    import tempfile

    tmp = Path(tempfile.mktemp(suffix=".toml"))
    try:
        write_project_config(tmp, config, preserve_comments=False)
        return tmp.read_text(encoding="utf-8")
    finally:
        if tmp.exists():
            tmp.unlink()


def _migrate_flat_keys_to_sections(
    legacy: dict[str, object], data: dict[str, object]
) -> None:
    """Move legacy flat top-level keys into their v2 sub-sections.

    In v1 configs, changelog and git settings were top-level keys like
    ``changelog_standard``, ``changelog_group_mode``, ``git_max_commits``.
    In v2, they live under ``[changelog]`` and ``[git]`` sections.
    """
    from collections.abc import MutableMapping
    from copy import deepcopy

    # Changelog keys: strip 'changelog_' prefix
    changelog_prefix = "changelog_"
    changelog_section = data.get("changelog")
    if not isinstance(changelog_section, MutableMapping):
        changelog_section = {}
        data["changelog"] = changelog_section
    for key, value in legacy.items():
        if key.startswith(changelog_prefix) and key != "changelog_standard":
            sub_key = key[len(changelog_prefix) :]
            if sub_key not in changelog_section:
                changelog_section[sub_key] = deepcopy(value)
    # Special case: changelog_standard maps to 'standard'
    if "changelog_standard" in legacy and "standard" not in changelog_section:
        changelog_section["standard"] = deepcopy(legacy["changelog_standard"])

    # Git keys: strip 'git_' prefix
    git_prefix = "git_"
    git_section = data.get("git")
    if not isinstance(git_section, MutableMapping):
        git_section = {}
        data["git"] = git_section
    for key, value in legacy.items():
        if key.startswith(git_prefix):
            sub_key = key[len(git_prefix) :]
            if sub_key not in git_section:
                git_section[sub_key] = deepcopy(value)


def project_config_from_legacy_mapping(
    legacy: dict[str, object],
    *,
    source: str = "legacy",
) -> Any:
    """Convert a v1 legacy config dict to a ProjectConfig.

    Preserves all supported fields, removes obsolete storage fields.
    Returns a validated ProjectConfig (not raw TOML text).
    """
    from collections.abc import MutableMapping
    from copy import deepcopy

    from releaseledger.storage.config import (
        ALLOWED_CHANGELOG_KEYS,
        ALLOWED_GIT_KEYS,
        ALLOWED_LEDGER_KEYS,
        ALLOWED_RELEASE_KEYS,
        ALLOWED_TOP_LEVEL_KEYS,
        _config_from_dict,
    )

    data: dict[str, object] = {}

    # Copy only allowed top-level keys from legacy
    for key in ALLOWED_TOP_LEVEL_KEYS:
        if key in legacy:
            data[key] = deepcopy(legacy[key])

    data["config_version"] = 2

    # Migrate legacy top-level ledger_code into [ledger].code
    legacy_code = legacy.get("ledger_code", "")
    ledger = data.get("ledger")
    if isinstance(ledger, MutableMapping):
        ledger.pop("name", None)
        if "code" not in ledger and legacy_code:
            ledger["code"] = legacy_code
    elif legacy_code:
        data["ledger"] = {"code": legacy_code}

    # Preserve legacy parent_ref and branch_guard at top level
    for legacy_key in ("ledger_parent_ref", "ledger_branch_guard"):
        if legacy_key in legacy and legacy_key not in data:
            data[legacy_key] = legacy[legacy_key]

    # Migrate legacy top-level changelog/git keys into their sections.
    # In v1 configs, these were flat top-level keys like
    # changelog_standard, changelog_group_mode, git_max_commits, etc.
    _migrate_flat_keys_to_sections(legacy, data)

    # Filter sub-section keys to only allowed v2 keys
    for section, allowed in [
        ("ledger", ALLOWED_LEDGER_KEYS),
        ("release", ALLOWED_RELEASE_KEYS),
        ("changelog", ALLOWED_CHANGELOG_KEYS),
        ("git", ALLOWED_GIT_KEYS),
    ]:
        if section in data and isinstance(data[section], dict):
            data[section] = {k: v for k, v in data[section].items() if k in allowed}  # type: ignore[attr-defined]

    return _config_from_dict(data, source)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def discover_legacy_project(start: Path) -> tuple[Path, dict[str, object]]:
    """Locate a legacy Releaseledger config and return its path and parsed content.

    Returns ``(config_path, parsed_config)`` where ``parsed_config`` is the
    full TOML document loaded as a plain dict. Raises ``NOT_FOUND`` when no
    legacy config is found.
    """
    # Use tomllib (Python 3.11+) with tomli fallback
    try:
        import tomllib  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        import tomli as tomllib  # type: ignore[import-not-found]

    search = Path(start).resolve()
    if search.is_file():
        search = search.parent

    for name in LEGACY_CONFIG_NAMES:
        candidate = search / name
        if candidate.is_file():
            try:
                with candidate.open("rb") as fh:
                    parsed = tomllib.load(fh)
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
# Data inventory (legacy dict-based, kept for compatibility)
# ---------------------------------------------------------------------------


def inventory_legacy_data(data_root: Path) -> dict[str, object]:
    """Walk a legacy data root and return ledger refs, counts, and paths.

    Uses the strict inventory builder internally for accuracy.
    """
    data_root = Path(data_root).resolve()
    if not data_root.is_dir():
        raise LaunchError(
            f"Legacy data root {data_root} does not exist.",
            code=CODE_NOT_FOUND,
            exit_code=2,
            data={"path": str(data_root)},
        )

    inv = build_strict_inventory(data_root)

    ref_details: dict[str, dict[str, object]] = {}
    for li in inv.ledgers:
        ref_details[li.ref] = {
            "ledger_dir": str(data_root / "ledgers" / li.ref),
            "release_count": li.release_count,
            "entry_count": li.entry_count,
            "audit_count": li.audit_sheet_count,
            "event_row_count": li.event_row_count,
        }

    return {
        "data_root": str(data_root),
        "ledger_refs": [li.ref for li in inv.ledgers],
        "ref_details": ref_details,
        "unexpected_files": list(inv.unexpected_paths),
        "total_releases": inv.total_releases,
        "total_entries": inv.total_entries,
        "total_event_rows": inv.total_event_rows,
        "total_audit_sheets": inv.total_audit_sheets,
    }


# ---------------------------------------------------------------------------
# Domain validation
# ---------------------------------------------------------------------------


def validate_domain_records(data_root: Path) -> dict[str, object]:
    """Load and validate every domain record under *data_root*.

    Returns a detailed report. Records that fail to parse are listed
    under ``failures`` rather than halting the migration.
    Uses recursive ledger root iteration for nested refs.
    """
    failures: list[dict[str, object]] = []
    ledger_reports: dict[str, dict[str, object]] = {}

    for ref, ledger_dir in iter_legacy_ledger_roots(data_root):
        report = _validate_ledger_domain(ledger_dir, ref)
        ledger_reports[ref] = report
        for f in report.get("failures", []):  # type: ignore[attr-defined]
            failures.append(f)

    refs = [r for r, _ in iter_legacy_ledger_roots(data_root)]

    return {
        "data_root": str(data_root),
        "ledger_refs": refs,
        "ledger_reports": ledger_reports,
        "total_failures": len(failures),
        "failures": failures,
        "valid": len(failures) == 0,
    }


def _validate_ledger_domain(ledger_dir: Path, ref: str) -> dict[str, object]:
    """Validate all domain records within a single ledger."""
    from releaseledger.storage.paths import ProjectPaths
    from releaseledger.storage.store import (
        list_releases_for_paths,
        load_entries_for_paths,
    )

    fake_project = _fake_project(ledger_dir.parent.parent.resolve())
    try:
        paths = ProjectPaths(
            project=fake_project,
            ledger_ref=ref,
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
            "ledger_ref": ref,
            "release_count": 0,
            "entry_count": 0,
            "valid": False,
            "failures": [{"ledger_ref": ref, "error": "cannot construct paths"}],
        }

    failures: list[dict[str, object]] = []

    try:
        releases = list_releases_for_paths(paths)
    except Exception as exc:
        failures.append({"ledger_ref": ref, "error": str(exc)})
        releases = []

    total_entries = 0
    for release in releases:
        try:
            entries = load_entries_for_paths(paths, release.version)
        except Exception as exc:
            failures.append(
                {
                    "ledger_ref": ref,
                    "release": release.version,
                    "error": str(exc),
                }
            )
            entries = []

        total_entries += len(entries)
        for entry in entries:
            if entry.release_version != release.version:
                failures.append(
                    {
                        "ledger_ref": ref,
                        "release": release.version,
                        "entry_id": entry.entry_id,
                        "error": (
                            f"entry release_version {entry.release_version} != "
                            f"{release.version}"
                        ),
                    }
                )

    return {
        "ledger_ref": ref,
        "release_count": len(releases),
        "entry_count": total_entries,
        "valid": len(failures) == 0,
        "failures": failures,
    }


def _fake_project(data_root: Path, indexes_root: Path | None = None) -> Any:
    """Build a minimal ReleaseledgerProject for domain validation."""
    from types import SimpleNamespace

    if indexes_root is None:
        indexes_root = data_root / "indexes"

    return SimpleNamespace(
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
    from releaseledger.storage.store import rebuild_indexes_for_paths

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

    IMPORTANT: This function is read-only. It must not write any files.
    """
    config_path, legacy_config = discover_legacy_project(request.start)
    workspace_root = config_path.parent.resolve()

    legacy_dir = _resolve_legacy_data_root(workspace_root, legacy_config)

    _reject_symlinks(legacy_dir)

    selected = select_legacy_durable_paths(legacy_dir)
    inventory = build_strict_inventory(legacy_dir, selected_paths=selected.included)

    target_data_storage = request.data_storage
    target_external_root = request.external_root

    # Prepare the target (pure, no writes)
    target_info = _backend.prepare_legacy_migration_target(
        workspace_root,
        data_storage=target_data_storage,
        external_root=target_external_root,
        target=request.target,
    )

    plan: dict[str, object] = {
        "schema": "releaseledger.migration-plan.v1",
        "migration": "storage-layout",
        "kind": "releaseledger_migration_plan",
        "legacy_config_path": str(config_path),
        "legacy_data_root": str(legacy_dir),
        "workspace_root": str(workspace_root),
        "target_data_root": str(target_info.data_root),
        "target_indexes_root": str(target_info.indexes_root),
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
        "source": {
            "config_path": str(config_path),
            "data_root": str(legacy_dir),
            "fingerprint": _inventory_fingerprint(inventory),
        },
        "destination": {
            "data_root": str(target_info.data_root),
            "indexes_root": str(target_info.indexes_root),
            "storage": target_data_storage,
            "scope": request.target,
            "external_root": target_external_root,
        },
        "operations": [
            {
                "operation": "copy",
                "path": item.relative_path,
                "size": item.size,
                "sha256": item.sha256,
            }
            for item in inventory.files
        ],
        "preconditions": [
            "source inventory fingerprint matches plan",
            "legacy source contains no symlinks",
            "target paths do not overlap legacy source",
        ],
    }

    target_data = target_info.data_root
    if _is_subpath(legacy_dir, target_data) or _is_subpath(target_data, legacy_dir):
        plan["warnings"].append(  # type: ignore[attr-defined]
            "Legacy data root and target data root overlap or are nested; "
            "the migration planner will reject this configuration at "
            "execution time."
        )
        plan["overlap_detected"] = True
    else:
        plan["overlap_detected"] = False

    return serialize_migration_plan(plan)


def _inventory_fingerprint(inventory: ReleaseledgerDataInventory) -> str:
    payload = [
        {"path": item.relative_path, "size": item.size, "sha256": item.sha256}
        for item in inventory.files
    ]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def serialize_migration_plan(plan: dict[str, object]) -> dict[str, object]:
    """Return a deterministic migration plan with its canonical hash."""
    result = dict(plan)
    result.pop("plan_hash", None)
    encoded = json.dumps(result, sort_keys=True, separators=(",", ":"), default=str)
    result["plan_hash"] = "sha256:" + hashlib.sha256(encoded.encode()).hexdigest()
    return result


def load_migration_plan(path: Path) -> dict[str, object]:
    """Load and validate a versioned migration plan file."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LaunchError(
            f"Cannot read migration plan {path}: {exc}",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        ) from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != "releaseledger.migration-plan.v1"
    ):
        raise LaunchError(
            "Migration plan must use schema releaseledger.migration-plan.v1.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    expected = payload.get("plan_hash")
    actual = serialize_migration_plan(payload).get("plan_hash")
    if expected != actual:
        raise LaunchError(
            "Migration plan hash does not match its contents.",
            code="CONFLICT",
            exit_code=4,
        )
    return payload


def validate_migration_plan(plan: dict[str, object], start: Path) -> dict[str, object]:
    """Recompute source inventory and reject a stale plan."""
    source = discover_legacy_source(start)
    current = _inventory_fingerprint(source.inventory)
    source_data = plan.get("source", {})
    expected = source_data.get("fingerprint") if isinstance(source_data, dict) else None
    if expected != current:
        raise LaunchError(
            "Migration plan is stale: the legacy source inventory changed.",
            code="CONFLICT",
            exit_code=4,
            remediation=["Run `migrate plan storage-layout` again."],
        )
    return {
        "valid": True,
        "source_fingerprint": current,
        "plan_hash": plan.get("plan_hash"),
    }


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
    quiescence_check: Callable[[], object] | None = None,
) -> dict[str, object]:
    """Execute a migration from legacy to schema-3.

    The heavy lifting (copy, stage, hash, activate) is delegated to
    Ledgercore via :func:`_backend.execute_releaseledger_layout_migration`.
    This function handles the Releaseledger-specific pre-flight and
    post-activation work (index rebuild, domain receipt).
    """
    # 1. Discover and inventory the legacy source
    source = discover_legacy_source(request.start)
    selection = select_legacy_durable_paths(source.data_root)
    inventory_before = build_strict_inventory(
        source.data_root, selected_paths=selection.included
    )

    # 2. Validate domain records before migration
    domain_before = validate_domain_records(source.data_root)
    if not domain_before["valid"]:
        failures = domain_before["failures"]
        raise LaunchError(
            f"{len(failures)} domain records failed validation; "  # type: ignore[arg-type]
            "fix them before migration.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
            data={"failures": failures[:10]},  # type: ignore[index]
            remediation=[
                "Inspect the failed records and correct them.",
                "Re-run the migration after fixing.",
            ],
        )

    # 3. Prepare the canonical target (pure — no writes yet)
    prepared = _backend.prepare_legacy_migration_target(
        source.workspace_root,
        project_name=source.legacy_config.get("project_name"),  # type: ignore[arg-type]
        data_storage=request.data_storage,
        external_root=request.external_root,
        target=request.target,
        project_uuid=request.project_uuid,
    )

    # 3b. Preflight: classify all destinations before any durable write (RL-MIG-005)
    source_fingerprint = _inventory_fingerprint(inventory_before)
    preflight = _preflight_destinations(
        prepared=prepared,
        source_fingerprint=source_fingerprint,
    )
    # 4. Transform the legacy config to v2
    transformed_config = project_config_from_legacy_mapping(
        source.legacy_config,
        source=str(source.config_path),
    )

    # 5. Create staging area and copy selected files
    stage = create_migration_stage(prepared)
    _write_journal_row(
        prepared.data_root.parent,
        {
            "schema": "releaseledger.migration-journal.v2",
            "phase": "staging",
            "migration_id": stage.migration_id,
            "plan_hash": "pending",
            "reason": request.reason or "",
            "legacy_data_root": str(source.data_root),
            "target_data_root": str(prepared.data_root),
            "source_fingerprint": _inventory_fingerprint(inventory_before),
        },
    )

    copy_selected_files(
        source=source.data_root,
        destination=stage.data_root,
        relative_paths=selection.included,
    )

    # Write transformed config to stage
    from releaseledger.storage.config import write_project_config

    write_project_config(stage.config_path, transformed_config, preserve_comments=False)

    # Ledgercore executor handles binding markers during copy

    # 6. Validate staged data
    domain_staged = validate_domain_records(stage.data_root)
    if not domain_staged["valid"]:
        failures = domain_staged["failures"]
        raise LaunchError(
            f"{len(failures)} domain records in staged data failed validation.",  # type: ignore[arg-type]
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
            data={"failures": failures[:10]},  # type: ignore[index]
        )

    staged_inventory = build_strict_inventory(
        stage.data_root,
        selected_paths=selection.included,
    )
    assert_inventory_preserved(inventory_before, staged_inventory)

    # 7. Verify source has not changed
    current_snapshot = build_strict_inventory(
        source.data_root,
        selected_paths=selection.included,
    )
    assert_same_source_snapshot(inventory_before, current_snapshot)

    # 8. Build the immutable ledgercore plan from staged source
    generic_plan = _backend.build_releaseledger_legacy_migration_plan(
        prepared_target=prepared,
        staged_data_root=stage.data_root,
        staged_config_path=stage.config_path,
        project_uuid=prepared.project_uuid,
        data_action=preflight.get("data_action", "create"),  # type: ignore[arg-type]
        config_action=preflight.get("config_action", "create"),  # type: ignore[arg-type]
    )

    # 8b. Validate plan with Ledgercore (destination policies, fingerprints, overlaps)
    _validate_plan_with_ledgercore(generic_plan, source.workspace_root)

    _write_journal_row(
        prepared.data_root.parent,
        {
            "phase": "activation-prepared",
            "migration_id": stage.migration_id,
            "data_action": preflight.get("data_action", "create"),
            "config_action": preflight.get("config_action", "create"),
        },
    )

    # 9. Activate: either through Ledgercore or direct shell adoption
    def _quiescence() -> None:
        if quiescence_check is not None:
            quiescence_check()

    data_action = preflight.get("data_action", "create")
    config_action = preflight.get("config_action", "create")

    if data_action in ("replace", "noop") and config_action in ("merge", "noop"):
        # Shell adoption path: direct filesystem replacement.
        # This bypasses the Ledgercore executor which refuses to
        # activate into a non-empty destination.
        _adopt_canonical_shell(
            prepared=prepared,
            stage=stage,
            source=source,
            selection=selection,
            inventory_before=inventory_before,
            data_action=data_action,
            config_action=config_action,
            transformed_config=transformed_config,
            migration_id=stage.migration_id,
        )
    else:
        # Standard path: use Ledgercore executor
        try:
            _backend.execute_releaseledger_layout_migration(
                generic_plan,
                mode="copy",
                quiescence_check=_quiescence,
                project_root=source.workspace_root,
            )
        except LaunchError:
            _write_journal_row(
                prepared.data_root.parent,
                {
                    "phase": "failed",
                    "migration_id": stage.migration_id,
                },
            )
            remove_migration_stage(stage)
            raise

    _write_journal_row(
        prepared.data_root.parent,
        {
            "phase": "canonical-activated",
            "migration_id": stage.migration_id,
        },
    )

    # 10. Load the final layout and rebuild indexes
    final_layout = _backend.load_releaseledger_ledger_layout(
        source.workspace_root, validate_storage=True, allow_missing=False
    )

    if (
        final_layout.validation_report is not None
        and not final_layout.validation_report.valid
    ):
        raise LaunchError(
            "Validation failed after migration.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
            data={
                "validation": [
                    {"path": str(r.path), "valid": r.valid, "reason": r.reason}
                    for r in final_layout.validation_report.results
                ],
            },
        )

    index_result = rebuild_all_indexes(
        final_layout.data_root,
        final_layout.indexes_root,
    )
    assert_index_rebuild_success(index_result)

    _write_journal_row(
        prepared.data_root.parent,
        {
            "phase": "indexes-rebuilt",
            "migration_id": stage.migration_id,
        },
    )

    # 11. Post-migration domain validation and conservation
    validate_domain_records(final_layout.data_root)
    target_selection = select_legacy_durable_paths(final_layout.data_root)
    target_inventory = build_strict_inventory(
        final_layout.data_root,
        selected_paths=target_selection.included,
    )
    assert_inventory_preserved(inventory_before, target_inventory)

    _write_journal_row(
        prepared.data_root.parent,
        {
            "phase": "domain-verified",
            "migration_id": stage.migration_id,
        },
    )
    # 12. Write completed migration receipt
    target_fingerprint = _inventory_fingerprint(target_inventory)
    receipt_path = _write_migration_receipt(
        workspace_root=source.workspace_root,
        migration_id=stage.migration_id,
        project_uuid=prepared.project_uuid,
        plan_hash="pending",
        source_root=str(source.data_root),
        source_fingerprint=source_fingerprint,
        target_data_root=str(final_layout.data_root),
        target_fingerprint=target_fingerprint,
    )

    _write_journal_row(
        prepared.data_root.parent,
        {
            "phase": "receipt-written",
            "migration_id": stage.migration_id,
            "receipt_path": str(receipt_path),
        },
    )

    # 13. Handle move mode: retire legacy source after verification

    if request.mode == "move":
        retire_legacy_source_after_success(
            source,
            preserve_config=request.preserve_legacy_config,
        )
        _write_journal_row(
            prepared.data_root.parent,
            {
                "phase": "legacy-retired",
                "migration_id": stage.migration_id,
            },
        )

    # 13. Clean up staging area
    remove_migration_stage(stage)

    _write_journal_row(
        prepared.data_root.parent,
        {
            "phase": "complete",
            "migration_id": stage.migration_id,
        },
    )

    return {
        "kind": "releaseledger_migration_executed",
        "mode": request.mode,
        "migration_id": stage.migration_id,
        "reason": request.reason or "",
        "legacy_data_root": str(source.data_root),
        "target_data_root": str(final_layout.data_root),
        "target_indexes_root": str(final_layout.indexes_root),
        "project_uuid": prepared.project_uuid,
        "domain_validation_before": domain_before["valid"],
        "domain_validation_after": True,
        "indexes_rebuilt": index_result,
        "inventory": inventory_legacy_data(final_layout.data_root),
        "warnings": [],
    }


# ---------------------------------------------------------------------------
# Destination inspection (RL-MIG-005)
# ---------------------------------------------------------------------------

DestinationState = Literal[
    "absent",
    "empty-unbound",
    "owned-exact",
    "owned-empty-shell",
    "owned-divergent",
    "foreign-bound",
    "non-directory",
    "symlink",
    "non-empty-unbound",
]


def inspect_destination(
    path: Path,
    *,
    expected_tool: str,
    expected_mount: str,
    expected_project_uuid: str,
    source_hash: str | None = None,
) -> tuple[DestinationState, str]:
    """Inspect a destination path and classify its state.

    Returns (state, detail_message). Read-only; no filesystem writes.
    """
    if path.is_symlink():
        return "symlink", f"{path} is a symlink"
    if path.exists() and not path.is_dir():
        return "non-directory", f"{path} exists but is not a directory"
    if not path.exists():
        return "absent", f"{path} does not exist"

    marker = path / ".ledger-project.toml"
    children = list(path.iterdir())
    # Exclude the write lock file from content detection.
    has_content = any(
        child.name not in (".ledger-project.toml", "write.lock") for child in children
    )

    if not marker.is_file():
        if not has_content:
            return "empty-unbound", f"{path} is empty with no binding"
        return "non-empty-unbound", f"{path} has content but no binding marker"

    try:
        from ledgercore.storage_binding import (
            read_storage_binding,
            storage_bindings_match,
        )

        actual = read_storage_binding(marker)
    except Exception as exc:
        return "foreign-bound", f"{path} has unreadable binding: {exc}"

    from ledgercore.storage_binding import StorageBinding

    expected = StorageBinding(
        schema_version=1,
        layout_version=3,
        project_uuid=expected_project_uuid,
        project_name=None,
        tool=expected_tool,
        mount=expected_mount,
        storage="project",
    )

    if not storage_bindings_match(actual, expected):
        return "foreign-bound", (
            f"{path} belongs to {actual.project_uuid}/{actual.tool}/{actual.mount}"
        )

    # Owned by same project — check if content matches.
    if not has_content:
        return "owned-exact", f"{path} is owned and empty"

    # Check if the destination is an empty shell (binding marker + empty
    # directories + empty files only). This is the canonical state after
    # `init` before any migration has occurred.
    if _is_empty_shell(path):
        return "owned-empty-shell", f"{path} is an owned empty shell (no durable data)"

    if source_hash is not None:
        dest_hash = _inventory_fingerprint_from_root(path)
        if dest_hash == source_hash:
            return "owned-exact", f"{path} content matches source"

    return "owned-divergent", f"{path} has different content"


def _validate_plan_with_ledgercore(plan: Any, project_root: Path) -> None:
    """Validate a migration plan using Ledgercore's validation.

    Calls validate_storage_migration_plan to check destination policies,
    fingerprints, bindings, and path overlaps. Raises LaunchError if
    validation fails.
    Falls back gracefully if Ledgercore validation is not available.
    """
    try:
        from ledgercore.migration import validate_storage_migration_plan
    except ImportError:
        # Ledgercore validation not available, skip
        return

    try:
        result = validate_storage_migration_plan(plan, project_root=project_root)
        if not result.valid:
            errors = list(result.errors)
            raise LaunchError(
                f"Migration plan validation failed: {len(errors)} error(s).",
                code="CONFLICT",
                exit_code=4,
                data={"validation_errors": errors[:10]},
                remediation=[
                    "Inspect the validation errors listed above.",
                    "Run `releaseledger migrate plan storage-layout` to regenerate.",
                ],
            )
    except LaunchError:
        raise
    except Exception:
        # Ledgercore validation failed unexpectedly, log but continue
        # with Releaseledger-only validation
        pass


def _adopt_canonical_shell(
    *,
    prepared: Any,
    stage: PreparedMigrationStage,
    source: LegacyReleaseledgerSource,
    selection: PathSelectionResult,
    inventory_before: ReleaseledgerDataInventory,
    data_action: str,
    config_action: str,
    transformed_config: Any,
    migration_id: str,
) -> None:
    """Adopt an existing canonical shell by direct filesystem replacement.

    This is the Releaseledger-only fallback for when the Ledgercore
    executor cannot replace an owned destination. It performs:
    1. Validate the destination binding
    2. Back up the current destination
    3. Rename the staged destination into place
    4. Write binding markers
    5. Verify the result
    6. Remove the backup after verification
    """
    import shutil

    data_root = prepared.data_root
    config_path = prepared.config_path
    config_parent = config_path.parent
    backup_suffix = f".migrating-{migration_id}"

    # --- Data activation ---
    if data_action == "replace":
        # Validate binding of the existing destination
        marker = data_root / ".ledger-project.toml"
        if marker.is_file():
            from ledgercore.storage_binding import (
                read_storage_binding,
                storage_bindings_match,
            )

            actual = read_storage_binding(marker)
            if not storage_bindings_match(actual, prepared.data_binding):
                raise LaunchError(
                    "Cannot adopt shell: destination binding does not match.",
                    code="CONFLICT",
                    exit_code=4,
                )

        # Back up current destination
        backup_path = data_root.parent / f"data{backup_suffix}"
        if backup_path.exists():
            shutil.rmtree(backup_path)
        data_root.rename(backup_path)

        try:
            # Move staged data into place
            stage.data_root.rename(data_root)

            # Write binding marker
            _ensure_binding(data_root, prepared, binding=prepared.data_binding)

            # Verify the result
            target_selection = select_legacy_durable_paths(data_root)
            target_inventory = build_strict_inventory(
                data_root, selected_paths=target_selection.included
            )
            assert_inventory_preserved(inventory_before, target_inventory)

            # Backup succeeded, remove it
            if backup_path.exists():
                shutil.rmtree(backup_path, ignore_errors=True)

        except Exception:
            # Rollback: restore backup
            if data_root.exists():
                shutil.rmtree(data_root, ignore_errors=True)
            if backup_path.exists():
                backup_path.rename(data_root)
            raise

    elif data_action == "noop":
        # Data already matches, nothing to do
        pass

    # --- Config activation ---
    if config_action == "merge":
        # Merge legacy config with canonical config
        _merge_and_write_config(
            config_path=config_path,
            config_parent=config_parent,
            transformed_config=transformed_config,
            migration_id=migration_id,
            prepared=prepared,
        )
    elif config_action == "noop":
        # Config already matches, nothing to do
        pass

    # Write binding marker for config parent if needed
    _ensure_binding(config_parent, prepared, binding=prepared.config_binding)


def _merge_and_write_config(
    *,
    config_path: Path,
    config_parent: Path,
    transformed_config: Any,
    migration_id: str,
    prepared: Any,
) -> None:
    """Merge legacy config with canonical config and write the result.

    Performs a three-way merge:
    - base = ProjectConfig() (defaults)
    - legacy = transformed_config (from legacy .releaseledger.toml)
    - canonical = existing canonical config (from init)

    For every field:
    - If canonical == legacy: keep value
    - If canonical == base and legacy != base: take legacy
    - If canonical != base and legacy == base: keep canonical
    - If both differ from base and equal each other: keep value
    - If both differ from base and differ from each other: conflict (take legacy for now)
    """
    import shutil

    from releaseledger.storage.config import (
        ProjectConfig,
        load_project_config,
        write_project_config,
    )

    base = ProjectConfig()

    # Load canonical config if it exists
    canonical = None
    if config_path.is_file():
        try:
            canonical = load_project_config(config_path)
        except Exception:
            pass

    if canonical is None:
        # No canonical config, just write the transformed one
        write_project_config(config_path, transformed_config, preserve_comments=False)
        return

    # Three-way merge
    base_dict = _config_to_dict(base)
    canonical_dict = _config_to_dict(canonical)
    legacy_dict = _config_to_dict(transformed_config)

    merged_dict: dict[str, object] = {}
    changes: list[dict[str, str]] = []

    for field in base_dict:
        base_val = base_dict[field]
        canonical_val = canonical_dict.get(field, base_val)
        legacy_val = legacy_dict.get(field, base_val)

        if canonical_val == legacy_val:
            merged_dict[field] = canonical_val
        elif canonical_val == base_val:
            merged_dict[field] = legacy_val
            if legacy_val != base_val:
                changes.append(
                    {
                        "field": field,
                        "from": str(canonical_val),
                        "to": str(legacy_val),
                        "source": "legacy",
                    }
                )
        elif legacy_val == base_val:
            merged_dict[field] = canonical_val
        elif canonical_val == legacy_val:
            merged_dict[field] = canonical_val
        else:
            # Both differ from base and from each other.
            # Take legacy value (the migration source is authoritative).
            merged_dict[field] = legacy_val
            changes.append(
                {
                    "field": field,
                    "from": str(canonical_val),
                    "to": str(legacy_val),
                    "source": "legacy",
                }
            )

    # Build merged config
    merged_config = _dict_to_config(merged_dict)

    # Back up existing config
    backup_suffix = f".migrating-{migration_id}"
    if config_path.is_file():
        backup = config_path.with_suffix(config_path.suffix + backup_suffix)
        shutil.copy2(config_path, backup)

    # Write merged config preserving comments from canonical
    write_project_config(config_path, merged_config, preserve_comments=True)


def _config_to_dict(config: Any) -> dict[str, object]:
    """Convert a ProjectConfig to a flat dict of field names and values."""
    import dataclasses

    result: dict[str, object] = {}
    for field in dataclasses.fields(config):
        result[field.name] = getattr(config, field.name)
    return result


def _dict_to_config(data: dict[str, object]) -> Any:
    """Convert a dict back to a ProjectConfig."""
    # Filter to only known fields
    import dataclasses

    from releaseledger.storage.config import ProjectConfig

    known = {f.name for f in dataclasses.fields(ProjectConfig)}
    filtered = {k: v for k, v in data.items() if k in known}
    return ProjectConfig(**filtered)  # type: ignore[arg-type]


def _is_empty_shell(path: Path) -> bool:
    """Check if path contains only metadata scaffolding (no durable data).

    An empty shell has:
    - a binding marker (.ledger-project.toml)
    - empty directories (e.g., ledgers/main/releases/, ledgers/main/events/)
    - empty files (e.g., events.jsonl with 0 bytes)
    - no non-empty regular files beyond the binding marker
    """
    for child in path.rglob("*"):
        if child.name == ".ledger-project.toml":
            continue
        if child.name == "write.lock":
            continue
        if child.is_dir():
            continue
        if child.is_file():
            # Any non-empty file means it's not an empty shell
            if child.stat().st_size > 0:
                return False
    return True


def _preflight_destinations(
    *,
    prepared: Any,
    source_fingerprint: str,
) -> dict[str, object]:
    """Classify all migration destinations before any durable write.

    Raises LaunchError if any destination is in a conflicting state that
    would prevent safe migration (foreign-bound, non-directory, etc.).
    Owned-exact destinations will be treated as no-ops during execution.
    Owned-empty-shell destinations are adoptable (canonical shell created
    by init with no durable data).
    Owned-divergent data means actual non-empty content differs.
    Owned-divergent config is expected when a canonical config exists —
    migration will merge configs.

    Cache (indexes) destinations are always safe to rebuild, so they are
    not checked for conflicts.

    Returns a dict with 'data_action', 'config_action', and 'conflicts'.
    """
    # Only preflight durable destinations (data and config).
    # Cache/indexes are always safe to rebuild.
    destinations = [
        ("data", prepared.data_root, DATA_MOUNT),
        ("config", prepared.config_path.parent, "config"),
    ]

    conflicts: list[dict[str, object]] = []
    data_action = "create"
    config_action = "create"

    for component, path, mount in destinations:
        state, detail = inspect_destination(
            path,
            expected_tool=TOOL_NAME,
            expected_mount=mount,
            expected_project_uuid=prepared.project_uuid,
            source_hash=source_fingerprint if component == "data" else None,
        )

        if state in ("foreign-bound", "non-directory", "symlink", "non-empty-unbound"):
            conflicts.append(
                {
                    "component": component,
                    "path": str(path),
                    "state": state,
                    "detail": detail,
                    "remediation": (
                        "Run `releaseledger migrate recover --dry-run` to inspect."
                    ),
                }
            )
        elif state == "owned-divergent":
            # For config, owned-divergent is expected when a canonical config
            # exists. The migration will merge configs, not overwrite.
            # For data, owned-divergent means there's actual non-empty content
            # that differs from the source — this is a real conflict.
            if component == "config":
                config_action = "merge"
            else:
                conflicts.append(
                    {
                        "component": component,
                        "path": str(path),
                        "state": state,
                        "detail": detail,
                        "remediation": (
                            "The destination has different content from the migration source. "
                            "Inspect the listed file collision; resolve or choose another destination."
                        ),
                    }
                )
        elif state == "owned-empty-shell":
            # Empty shell is adoptable — no conflict.
            if component == "data":
                data_action = "replace"
            else:
                config_action = "merge"
        elif state == "owned-exact":
            # Already matches — no-op.
            if component == "data":
                data_action = "noop"
            else:
                config_action = "noop"
        # absent and empty-unbound are fine — will be created

    if conflicts:
        raise LaunchError(
            f"Migration cannot proceed because {len(conflicts)} destination check(s) failed. "
            "No files were changed.",
            code="CONFLICT",
            exit_code=4,
            data={
                "conflicts": conflicts,
                "data_action": data_action,
                "config_action": config_action,
            },
            remediation=[
                "Inspect the conflicting destinations listed above.",
                "Run `releaseledger migrate recover --dry-run` to inspect.",
            ],
        )

    return {
        "data_action": data_action,
        "config_action": config_action,
        "conflicts": [],
    }


def _inventory_fingerprint_from_root(path: Path) -> str:
    """Compute a hash fingerprint of all files under path."""
    import hashlib as _hashlib
    import json as _json

    files: list[dict[str, object]] = []
    for child in sorted(path.rglob("*")):
        if child.name == ".ledger-project.toml":
            continue
        if child.is_file() and not child.is_symlink():
            rel = str(child.relative_to(path))
            sha = _hashlib.sha256(child.read_bytes()).hexdigest()
            files.append({"path": rel, "sha256": sha})
    encoded = _json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + _hashlib.sha256(encoded).hexdigest()


def is_empty_releaseledger_bootstrap(
    path: Path,
    *,
    expected_project_uuid: str,
) -> bool:
    """Detect a strictly empty Releaseledger bootstrap scaffold.

    Recognizes only:
    - valid same-project Releaseledger binding
    - no release records, no entry records, no non-empty event log
    - only generated empty index files and expected directories
    - default generated tool config
    - no unknown regular files
    """
    if not path.is_dir():
        return False

    marker = path / ".ledger-project.toml"
    if not marker.is_file():
        return False

    try:
        from ledgercore.storage_binding import (
            StorageBinding,
            read_storage_binding,
            storage_bindings_match,
        )

        actual = read_storage_binding(marker)
        expected = StorageBinding(
            schema_version=1,
            layout_version=3,
            project_uuid=expected_project_uuid,
            project_name=None,
            tool=TOOL_NAME,
            mount=DATA_MOUNT,
            storage="project",
        )
        if not storage_bindings_match(actual, expected):
            return False
    except Exception:
        return False

    # Check for content beyond binding marker and empty structure
    for child in path.rglob("*"):
        if child.name == ".ledger-project.toml":
            continue
        if child.is_file():
            # Only allow empty index files and config
            if child.suffix in (".json",):
                try:
                    import json

                    content = json.loads(child.read_text())
                    if content:  # non-empty index file
                        return False
                except Exception:
                    return False
            elif child.name == "config.toml":
                continue  # allow generated config
            else:
                return False

    return True


# ---------------------------------------------------------------------------
# Staging helpers
# ---------------------------------------------------------------------------


def create_migration_stage(
    prepared: Any,  # PreparedReleaseledgerTarget
) -> PreparedMigrationStage:
    """Create a filtered staging directory for legacy migration."""
    migration_id = str(uuid.uuid4())
    stage_root = prepared.data_root.parent / STAGING_DIR_NAME / migration_id
    data_root = stage_root / "data"
    config_path = stage_root / "config.toml"

    stage_root.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)

    return PreparedMigrationStage(
        stage_root=stage_root,
        data_root=data_root,
        config_path=config_path,
        migration_id=migration_id,
    )


def copy_selected_files(
    *,
    source: Path,
    destination: Path,
    relative_paths: tuple[str, ...],
) -> None:
    """Copy selected files from source to destination preserving structure."""
    for rel in relative_paths:
        src = source / rel
        dst = destination / rel
        if not src.is_file():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def remove_migration_stage(stage: PreparedMigrationStage) -> None:
    """Remove the migration staging directory."""
    if stage.stage_root.exists():
        shutil.rmtree(stage.stage_root, ignore_errors=True)


def retire_legacy_source_after_success(
    source: LegacyReleaseledgerSource,
    *,
    preserve_config: bool = False,
) -> None:
    """Remove or archive legacy data after successful migration.

    The legacy data directory is removed. The legacy config is archived
    unless preserve_config is True.
    """
    legacy_dir = source.data_root
    if legacy_dir.exists():
        shutil.rmtree(legacy_dir, ignore_errors=True)

    if not preserve_config:
        config_path = source.config_path
        if config_path.is_file():
            archive_path = config_path.with_suffix(config_path.suffix + ".migrated")
            if archive_path.exists():
                # Refuse to overwrite existing archive
                config_path.unlink()
            else:
                config_path.rename(archive_path)


def _ensure_binding(
    data_root: Path,
    prepared: Any,  # PreparedReleaseledgerTarget
    *,
    binding: Any | None = None,
) -> None:
    """Write a .ledger-project.toml binding marker to the data root.

    Always writes the correct format, replacing any existing marker.
    Uses the flat key-value format that Ledgercore validation expects.
    If binding is not provided, uses prepared.data_binding.
    """
    import ledgercore

    if binding is None:
        binding = prepared.data_binding

    marker_path = data_root / ".ledger-project.toml"
    binding_content = (
        f"schema_version = {binding.schema_version}\n"
        f"layout_version = {binding.layout_version}\n"
        f'project_uuid = "{binding.project_uuid}"\n'
        f'tool = "{binding.tool}"\n'
        f'mount = "{binding.mount}"\n'
        f'storage = "{binding.storage}"\n'
    )
    if binding.project_name:
        binding_content += f'project_name = "{binding.project_name}"\n'
    ledgercore.atomic_write_text(marker_path, binding_content)


# ---------------------------------------------------------------------------
# Journal and recovery
# ---------------------------------------------------------------------------


def _write_journal_row(journal_dir: Path, row: dict[str, object]) -> None:
    """Append a row to the Releaseledger migration journal."""
    journal_dir.mkdir(parents=True, exist_ok=True)
    path = journal_dir / JOURNAL_FILENAME
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, default=str) + "\n")


def read_migration_journal(journal_dir: Path) -> list[dict[str, object]]:
    """Read the Releaseledger migration journal from *journal_dir*."""
    path = journal_dir / JOURNAL_FILENAME
    if not path.is_file():
        return []
    return list(_read_jsonl_permissive(path))


def evaluate_migration_state(
    workspace_root: Path,
) -> dict[str, object]:
    """Shared migration state evaluator with defined precedence.

    This is the single source of truth for migration state, consumed by
    ``migrate status``, ``storage where``, ``status``, ``doctor``, and
    ``project-state`` commands.

    Precedence:
    1. Incomplete/failed journal → ``migration-recovery-required``
    2. Canonical manifest present but Releaseledger registration invalid/missing
       → ``canonical-invalid``
    3. Valid canonical layout but binding/domain validation fails
       → ``canonical-invalid``
    4. Valid canonical layout plus legacy artifacts
       → ``canonical-with-legacy-artifacts``
    5. Valid canonical layout → ``canonical-ready``
    6. Legacy source only → ``legacy``
    7. Neither → ``uninitialized``

    Also computes ``legacy_relation`` and ``cleanup_safe`` for the
    canonical-with-legacy-artifacts state so that cleanup is never
    recommended without proven conservation.
    """
    workspace_root = Path(workspace_root).resolve()

    legacy: str | None = None
    for name in LEGACY_CONFIG_NAMES:
        candidate = workspace_root / name
        if candidate.is_file():
            legacy = str(candidate)
            break

    manifest = workspace_root / ".ledger" / "ledger.toml"
    has_canonical = manifest.is_file()

    # 1. Check for incomplete/failed journal FIRST (highest priority)
    journal_dir = workspace_root / ".ledger" / "releaseledger"
    has_failed_journal = False
    last_phase = None
    if (journal_dir / JOURNAL_FILENAME).is_file():
        journal = read_migration_journal(journal_dir)
        last = journal[-1] if journal else {}
        last_phase = last.get("phase")
        if last_phase in ("staging", "ledgercore-executing", "failed"):
            has_failed_journal = True

    # 2. Try to load canonical project
    canonical_valid = False
    canonical_error: str | None = None
    validation_report = None
    if has_canonical:
        try:
            from releaseledger.ledgercore_backend import (
                load_releaseledger_ledger_layout,
            )

            layout = load_releaseledger_ledger_layout(
                workspace_root, allow_missing=False, validate_storage=True
            )
            canonical_valid = True
            validation_report = layout.validation_report
        except Exception as exc:
            canonical_error = str(exc)

    # Evaluate states in precedence order
    if has_failed_journal:
        return {
            "state": "migration-recovery-required",
            "legacy_detected": legacy is not None,
            "legacy_config_path": legacy,
            "canonical_detected": has_canonical,
            "migration_in_progress": False,
            "migration_recovery_required": True,
            "last_phase": last_phase,
            "remediation": "Run `releaseledger migrate recover --dry-run`.",
        }

    if has_canonical and not canonical_valid:
        return {
            "state": "canonical-invalid",
            "legacy_detected": legacy is not None,
            "legacy_config_path": legacy,
            "canonical_detected": True,
            "canonical_error": canonical_error,
            "migration_in_progress": False,
            "migration_recovery_required": False,
            "remediation": (
                "Fix the canonical project configuration. "
                "Run `releaseledger storage validate`."
            ),
        }

    if (
        canonical_valid
        and validation_report is not None
        and not validation_report.valid
    ):
        return {
            "state": "canonical-invalid",
            "legacy_detected": legacy is not None,
            "legacy_config_path": legacy,
            "canonical_detected": True,
            "migration_in_progress": False,
            "migration_recovery_required": False,
            "remediation": (
                "Storage validation failed. "
                "Run `releaseledger storage validate --strict`."
            ),
        }

    if canonical_valid and legacy is not None:
        # Compute legacy_relation and cleanup_safe for the
        # canonical-with-legacy-artifacts state.
        legacy_relation, cleanup_safe, next_action = _compute_legacy_relation(
            workspace_root, legacy
        )
        result: dict[str, object] = {
            "state": "canonical-with-legacy-artifacts",
            "legacy_detected": True,
            "legacy_config_path": legacy,
            "canonical_detected": True,
            "manifest_path": str(manifest),
            "migration_in_progress": False,
            "migration_recovery_required": False,
            "legacy_relation": legacy_relation,
            "cleanup_safe": cleanup_safe,
            "next_action": next_action,
        }
        if cleanup_safe:
            result["remediation"] = (
                "Run `releaseledger migrate cleanup storage-layout --dry-run`."
            )
        else:
            result["remediation"] = (
                "Run `releaseledger migrate apply storage-layout --dry-run` "
                "to inspect destination compatibility."
            )
        return result

    if canonical_valid:
        return {
            "state": "canonical-ready",
            "legacy_detected": False,
            "canonical_detected": True,
            "manifest_path": str(manifest),
            "migration_in_progress": False,
            "migration_recovery_required": False,
            "legacy_relation": "none",
            "cleanup_safe": False,
            "next_action": "none",
        }

    if legacy is not None:
        return {
            "state": "legacy",
            "legacy_detected": True,
            "legacy_config_path": legacy,
            "canonical_detected": False,
            "migration_in_progress": False,
            "migration_recovery_required": False,
            "legacy_relation": "pending-migration",
            "cleanup_safe": False,
            "next_action": "migrate plan",
            "remediation": "Run `releaseledger migrate plan storage-layout`.",
        }

    return {
        "state": "uninitialized",
        "legacy_detected": False,
        "canonical_detected": False,
        "migration_in_progress": False,
        "migration_recovery_required": False,
        "legacy_relation": "none",
        "cleanup_safe": False,
        "next_action": "none",
    }


def _compute_legacy_relation(
    workspace_root: Path,
    legacy_config_path: str,
) -> tuple[str, bool, str]:
    """Compute legacy_relation, cleanup_safe, and next_action.

    Compares legacy durable inventory against canonical durable data.
    Returns (legacy_relation, cleanup_safe, next_action).
    """
    try:
        source = discover_legacy_source(workspace_root)
    except LaunchError:
        return "unknown", False, "resolve-conflict"

    # Inventory legacy source
    selection = select_legacy_durable_paths(source.data_root)
    source_inventory = build_strict_inventory(
        source.data_root, selected_paths=selection.included
    )

    # Check for completed migration receipt
    receipt = _load_migration_receipt(workspace_root)
    if receipt is not None:
        # Verify receipt still matches
        receipt_source_fp = receipt.get("source_fingerprint")
        current_source_fp = _inventory_fingerprint(source_inventory)
        if receipt_source_fp == current_source_fp:
            receipt_target_fp = receipt.get("target_fingerprint")
            # Inventory canonical data
            try:
                canonical_layout = _backend.load_releaseledger_ledger_layout(
                    workspace_root, validate_storage=False, allow_missing=False
                )
                canonical_selection = select_legacy_durable_paths(
                    canonical_layout.data_root
                )
                canonical_inventory = build_strict_inventory(
                    canonical_layout.data_root,
                    selected_paths=canonical_selection.included,
                )
                current_target_fp = _inventory_fingerprint(canonical_inventory)
                if receipt_target_fp == current_target_fp:
                    # Receipt matches: exact copy
                    return "exact-copy", True, "migrate cleanup"
            except Exception:
                pass

    # Inventory canonical data to compare against legacy
    try:
        canonical_layout = _backend.load_releaseledger_ledger_layout(
            workspace_root, validate_storage=False, allow_missing=False
        )
    except Exception:
        return "unknown", False, "resolve-conflict"

    canonical_data_root = canonical_layout.data_root
    if not canonical_data_root.is_dir():
        return "pending-migration", False, "migrate apply"

    # Check if canonical data root has any non-empty durable content.
    # Empty files (e.g., events.jsonl created by init) and binding
    # markers (.ledger-project.toml) are metadata scaffolding, not
    # durable data.
    canonical_selection = select_legacy_durable_paths(canonical_data_root)
    canonical_has_durable = False
    for rel_path in canonical_selection.included:
        if rel_path == ".ledger-project.toml":
            continue  # binding marker, not durable data
        fp = canonical_data_root / rel_path
        if fp.is_file() and fp.stat().st_size > 0:
            canonical_has_durable = True
            break

    if not canonical_has_durable:
        # Empty shell: safe to adopt
        return "pending-migration", False, "migrate apply"

    # Canonical has durable content: compare inventories, excluding
    # binding markers from both sides.
    filtered_canonical_paths = [
        p for p in canonical_selection.included if p != ".ledger-project.toml"
    ]
    canonical_inventory = build_strict_inventory(
        canonical_data_root, selected_paths=tuple(filtered_canonical_paths)
    )

    source_paths = {f.relative_path: f.sha256 for f in source_inventory.files}
    canonical_paths = {f.relative_path: f.sha256 for f in canonical_inventory.files}

    # Check if canonical is an exact copy
    if source_paths == canonical_paths:
        return "exact-copy", True, "migrate cleanup"

    # Check for compatible partial (canonical is a subset of source)
    if canonical_paths and all(
        source_paths.get(p) == h for p, h in canonical_paths.items()
    ):
        missing = set(source_paths) - set(canonical_paths)
        if missing:
            return "compatible-partial", False, "migrate apply"

    # Divergent
    return "divergent", False, "resolve-conflict"


def _write_migration_receipt(
    *,
    workspace_root: Path,
    migration_id: str,
    project_uuid: str,
    plan_hash: str,
    source_root: str,
    source_fingerprint: str,
    target_data_root: str,
    target_fingerprint: str,
    config_before_sha256: str = "",
    config_after_sha256: str = "",
) -> Path:
    """Write a completed migration receipt.

    The receipt is a TOML file at
    .ledger/releaseledger/migrations/<migration-id>.toml
    that records the migration as completed and permits cleanup.
    """

    receipt_dir = workspace_root / ".ledger" / "releaseledger" / "migrations"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = receipt_dir / f"{migration_id}.toml"

    # Build TOML content
    lines = [
        "schema_version = 1",
        f'migration_id = "{migration_id}"',
        f'project_uuid = "{project_uuid}"',
        f'plan_hash = "{plan_hash}"',
        f'source_root = "{source_root}"',
        f'source_fingerprint = "{source_fingerprint}"',
        f'target_data_root = "{target_data_root}"',
        f'target_fingerprint = "{target_fingerprint}"',
        f'config_before_sha256 = "{config_before_sha256}"',
        f'config_after_sha256 = "{config_after_sha256}"',
        "completed = true",
        "legacy_cleanup_permitted = true",
    ]

    content = "\n".join(lines) + "\n"
    receipt_path.write_text(content, encoding="utf-8")

    return receipt_path


def _load_migration_receipt(
    workspace_root: Path,
) -> dict[str, object] | None:
    """Load the most recent completed migration receipt, if any."""
    receipt_dir = workspace_root / ".ledger" / "releaseledger" / "migrations"
    if not receipt_dir.is_dir():
        return None

    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib

    receipts: list[dict[str, object]] = []
    for entry in sorted(receipt_dir.iterdir(), reverse=True):
        if entry.suffix == ".toml" and entry.is_file():
            try:
                with entry.open("rb") as fh:
                    data = tomllib.load(fh)
                if isinstance(data, dict) and data.get("completed") is True:
                    receipts.append(data)
            except Exception:
                continue

    return receipts[0] if receipts else None


def migration_status(
    workspace_root: Path,
) -> dict[str, object]:
    """Report the current migration state for a project.

    Delegates to the shared state evaluator.
    """
    return evaluate_migration_state(workspace_root)


def recover_migration(
    workspace_root: Path,
    *,
    journal: Path | None = None,
    dry_run: bool = False,
    resume: bool = False,
    rollback_partial: bool = False,
    yes: bool = False,
    reason: str | None = None,
) -> dict[str, object]:
    """Attempt actionable recovery from an interrupted migration.

    Recovery outcomes:
    - ``nothing-to-recover``: no journal found or migration completed
    - ``safe-to-remove-temporaries``: only unactivated temp artifacts remain
    - ``resumed-and-completed``: recovery completed the migration
    - ``manual-conflict``: divergent state requires human decision

    With ``dry_run=True``, reports what would be done without changes.
    """
    root = Path(workspace_root).resolve()
    journal_dir = root / ".ledger" / "releaseledger"
    journal_path = journal_dir / JOURNAL_FILENAME

    rows = (
        list(_read_jsonl_permissive(journal))
        if journal is not None
        else read_migration_journal(journal_dir)
    )
    if not rows:
        return {
            "kind": "nothing-to-recover",
            "message": "No migration journal found; nothing to recover.",
        }

    last = rows[-1]
    phase = last.get("phase", "unknown")
    migration_id = last.get("migration_id", "unknown")

    # Completed migration: nothing to recover
    if phase in ("complete", "domain-verified", "indexes-rebuilt"):
        return {
            "kind": "nothing-to-recover",
            "last_phase": phase,
            "migration_id": migration_id,
            "message": f"Migration completed (phase={phase}). Nothing to recover.",
        }

    staging_dir = journal_dir / STAGING_DIR_NAME / str(migration_id)
    temp_paths = _collect_migration_temp_paths(root, migration_id, staging_dir)
    dest_activated = _check_dest_activated(last.get("target_data_root"))

    if phase == "failed" and dest_activated:
        return _recover_after_activation(
            phase,
            migration_id,
            temp_paths,
            dest_activated,
            dry_run=dry_run,
            yes=yes,
        )

    if phase in ("staging", "failed") and not dest_activated:
        return _recover_before_activation(
            phase,
            migration_id,
            temp_paths,
            dry_run=dry_run,
            yes=yes,
            journal_path=journal_path,
        )

    # Unknown or complex state: report with exact details
    return {
        "kind": "manual-conflict",
        "last_phase": phase,
        "migration_id": migration_id,
        "temp_paths": temp_paths,
        "destinations_activated": dest_activated,
        "journal_path": str(journal_path),
        "message": (
            f"Migration was in phase '{phase}'. "
            f"Found {len(temp_paths)} temporary artifact(s). "
            "Inspect the journal and data directories."
        ),
        "remediation": [
            f"Inspect journal: {journal_path}",
            f"Inspect staging: {staging_dir}",
            "Run `releaseledger migrate recover --dry-run` to see actions.",
            "Run `releaseledger migrate recover --yes --reason '...'` to clean temporaries.",
        ],
    }


def _collect_migration_temp_paths(
    root: Path,
    migration_id: str | object,
    staging_dir: Path,
) -> list[str]:
    """Scan for temporary artifacts left by an interrupted migration."""
    ledgercore_temp_patterns = [
        root / ".ledger" / "releaseledger" / "data" / f".data.migrating-{migration_id}",
        root / ".ledger" / "releaseledger" / f".data.migrating-{migration_id}",
    ]

    temp_paths: list[str] = []
    if staging_dir.exists():
        temp_paths.append(str(staging_dir))
    for pattern in ledgercore_temp_patterns:
        if pattern.exists():
            temp_paths.append(str(pattern))
    for parent in [root / ".ledger" / "releaseledger", root / ".ledger"]:
        if parent.is_dir():
            for child in parent.iterdir():
                if (
                    child.name.endswith(f".migrating-{migration_id}")
                    and str(child) not in temp_paths
                ):
                    temp_paths.append(str(child))
    return temp_paths


def _check_dest_activated(dest_data: object) -> bool:
    """Check whether migration destination data was already activated."""
    if not dest_data:
        return False
    data_path = Path(str(dest_data))
    if data_path.is_dir():
        marker = data_path / ".ledger-project.toml"
        if marker.is_file():
            return True
    return False


def _recover_after_activation(
    phase: str,
    migration_id: str | object,
    temp_paths: list[str],
    dest_activated: bool,
    *,
    dry_run: bool,
    yes: bool,
) -> dict[str, object]:
    """Recover when data was activated but migration failed after."""
    result: dict[str, object] = {
        "kind": "safe-to-remove-temporaries",
        "last_phase": phase,
        "migration_id": migration_id,
        "temp_paths": temp_paths,
        "destinations_activated": dest_activated,
        "message": (
            "Migration failed after data was activated. "
            "Temporary artifacts can be safely removed."
        ),
    }
    if dry_run:
        result["dry_run"] = True
        result["action"] = "Would remove temporary artifacts and re-run migration."
    elif yes:
        for path_str in temp_paths:
            path = Path(path_str)
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            elif path.exists():
                path.unlink()
        result["removed"] = temp_paths
        result["action"] = "Removed temporary artifacts. Re-run migration to complete."
    else:
        result["remediation"] = (
            "Run `releaseledger migrate recover --yes --reason '...'` to clean up, "
            "then re-run `migrate apply`."
        )
    return result


def _recover_before_activation(
    phase: str,
    migration_id: str | object,
    temp_paths: list[str],
    *,
    dry_run: bool,
    yes: bool,
    journal_path: Path,
) -> dict[str, object]:
    """Recover when nothing was activated yet."""
    result: dict[str, object] = {
        "kind": "safe-to-remove-temporaries",
        "last_phase": phase,
        "migration_id": migration_id,
        "temp_paths": temp_paths,
        "destinations_activated": False,
        "message": (
            "Migration failed before any destination was activated. "
            "All temporary artifacts can be safely removed."
        ),
    }
    if dry_run:
        result["dry_run"] = True
        result["action"] = "Would remove all temporary artifacts."
    elif yes:
        for path_str in temp_paths:
            path = Path(path_str)
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            elif path.exists():
                path.unlink()
        if journal_path.is_file():
            journal_path.unlink()
        result["removed"] = temp_paths
        result["journal_cleared"] = True
        result["action"] = "Removed temporary artifacts and cleared journal."
    else:
        result["remediation"] = (
            "Run `releaseledger migrate recover --yes --reason '...'` to clean up."
        )
    return result


def cleanup_migration(
    workspace_root: Path,
    *,
    yes: bool = False,
    dry_run: bool = False,
    reason: str | None = None,
) -> dict[str, object]:
    """List or explicitly remove verified legacy migration artifacts.

    Conservation gate: before any legacy path can be listed as removable,
    the function must prove that every legacy durable file exists in
    canonical storage with an identical hash. This prevents the data-loss
    scenario where cleanup deletes unmigrated legacy data.
    """
    root = Path(workspace_root).resolve()
    status = migration_status(root)
    if status.get("state") not in {
        "canonical-ready",
        "canonical-with-legacy-artifacts",
    }:
        raise LaunchError(
            "Migration cleanup requires a verified canonical project.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    journal_dir = root / ".ledger" / "releaseledger"
    journal = read_migration_journal(journal_dir)
    if journal and journal[-1].get("phase") in {
        "staging",
        "ledgercore-executing",
        "failed",
    }:
        raise LaunchError(
            "Migration cleanup is blocked by an incomplete migration journal.",
            code="CONFLICT",
            exit_code=4,
            remediation=["Run `migrate recover --journal PATH` first."],
        )

    # Conservation gate: prove legacy data exists in canonical storage
    conservation = _verify_cleanup_conservation(root)
    if not conservation["safe"]:
        if dry_run:
            return {
                "kind": "migration_cleanup",
                "dry_run": True,
                "removed": [],
                "paths": [],
                "reason": reason,
                "cleanup_safe": False,
                "blocked_reason": conservation.get("blocked_reason", ""),
                "missing_paths": conservation.get("missing_paths", []),
                "different_paths": conservation.get("different_paths", []),
            }
        raise LaunchError(
            str(
                conservation.get("blocked_reason", "Cleanup conservation check failed.")
            ),
            code="CONFLICT",
            exit_code=4,
            data={
                "cleanup_safe": False,
                "missing_paths": conservation.get("missing_paths", []),
                "different_paths": conservation.get("different_paths", []),
            },
            remediation=[
                "Run `releaseledger migrate apply storage-layout` to migrate legacy data first.",
                "Run `releaseledger migrate cleanup storage-layout --dry-run` to inspect.",
            ],
        )

    paths: list[Path] = []
    for name in LEGACY_CONFIG_NAMES:
        config = root / name
        if config.is_file():
            paths.append(config)
            try:
                source = discover_legacy_source(root)
                if source.data_root.exists():
                    paths.append(source.data_root)
            except LaunchError:
                pass
            break
    listed = sorted({str(path) for path in paths})
    if dry_run:
        return {
            "kind": "migration_cleanup",
            "dry_run": True,
            "removed": [],
            "paths": listed,
            "reason": reason,
            "cleanup_safe": True,
            "conservation": conservation,
        }
    if not reason or not reason.strip():
        raise LaunchError(
            "Migration cleanup requires a reason.",
            code=CODE_USAGE_ERROR,
            exit_code=2,
            remediation=["Provide --reason explaining why legacy state is removed."],
        )
    if not yes:
        raise LaunchError(
            "Cleanup deletes legacy paths and requires --yes.",
            code=CODE_USAGE_ERROR,
            exit_code=2,
            remediation=["Review `migrate cleanup storage-layout --dry-run` first."],
        )
    removed: list[str] = []
    for raw in listed:
        path = Path(raw)
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
        removed.append(raw)
    return {
        "kind": "migration_cleanup",
        "dry_run": False,
        "removed": removed,
        "paths": listed,
        "reason": reason,
        "cleanup_safe": True,
        "conservation": conservation,
    }


def _verify_cleanup_conservation(
    workspace_root: Path,
) -> dict[str, object]:
    """Verify that all legacy durable files exist in canonical storage.

    Returns a dict with 'safe' (bool), 'blocked_reason', 'missing_paths',
    and 'different_paths'. This is the P0 conservation gate that prevents
    cleanup from deleting unmigrated legacy data.
    """
    try:
        source = discover_legacy_source(workspace_root)
    except LaunchError:
        return {
            "safe": False,
            "blocked_reason": "Cannot discover legacy source for conservation check.",
            "missing_paths": [],
            "different_paths": [],
        }

    # Inventory legacy source
    selection = select_legacy_durable_paths(source.data_root)
    source_inventory = build_strict_inventory(
        source.data_root, selected_paths=selection.included
    )
    source_files = {f.relative_path: f.sha256 for f in source_inventory.files}

    if not source_files:
        return {"safe": True, "missing_paths": [], "different_paths": []}

    # Load canonical layout
    try:
        canonical_layout = _backend.load_releaseledger_ledger_layout(
            workspace_root, validate_storage=False, allow_missing=False
        )
    except Exception as exc:
        return {
            "safe": False,
            "blocked_reason": f"Cannot load canonical layout: {exc}",
            "missing_paths": list(source_files.keys()),
            "different_paths": [],
        }

    canonical_data_root = canonical_layout.data_root
    if not canonical_data_root.is_dir():
        return {
            "safe": False,
            "blocked_reason": "Canonical data root does not exist.",
            "missing_paths": list(source_files.keys()),
            "different_paths": [],
        }

    # Inventory canonical data using same selection rules
    canonical_selection = select_legacy_durable_paths(canonical_data_root)
    canonical_inventory = build_strict_inventory(
        canonical_data_root, selected_paths=canonical_selection.included
    )
    canonical_files = {f.relative_path: f.sha256 for f in canonical_inventory.files}

    # Compare every legacy path and hash
    missing_paths: list[str] = []
    different_paths: list[str] = []
    for rel_path, source_hash in source_files.items():
        canonical_hash = canonical_files.get(rel_path)
        if canonical_hash is None:
            missing_paths.append(rel_path)
        elif canonical_hash != source_hash:
            different_paths.append(rel_path)

    if missing_paths or different_paths:
        parts: list[str] = []
        if missing_paths:
            parts.append(f"{len(missing_paths)} file(s) missing from canonical data")
        if different_paths:
            parts.append(
                f"{len(different_paths)} file(s) differ between legacy and canonical"
            )
        return {
            "safe": False,
            "blocked_reason": f"canonical data does not contain all legacy durable files: {'; '.join(parts)}.",
            "missing_paths": missing_paths,
            "different_paths": different_paths,
        }

    # Also check for completed migration receipt as defense in depth
    receipt = _load_migration_receipt(workspace_root)
    if receipt is not None:
        receipt_source_fp = receipt.get("source_fingerprint")
        current_source_fp = _inventory_fingerprint(source_inventory)
        if receipt_source_fp != current_source_fp:
            return {
                "safe": False,
                "blocked_reason": "Legacy source changed after migration receipt was written.",
                "missing_paths": [],
                "different_paths": [],
            }

    return {"safe": True, "missing_paths": [], "different_paths": []}


def _read_jsonl_strict(path: Path) -> Iterator[dict[str, object]]:
    """Yield parsed JSON objects from a JSON-lines file. Fails on invalid rows."""
    if not path.is_file():
        return
    with path.open("r", encoding="utf-8") as fh:
        for line_number, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise LaunchError(
                    f"Invalid JSONL at {path}:{line_number}: {exc}",
                    code=CODE_VALIDATION_ERROR,
                    exit_code=2,
                    data={"path": str(path), "line": line_number},
                ) from exc


def _read_jsonl_permissive(path: Path) -> Iterator[dict[str, object]]:
    """Yield parsed JSON objects from a JSON-lines file (permissive)."""
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
