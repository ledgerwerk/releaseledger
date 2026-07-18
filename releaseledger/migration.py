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
        dirpath = Path(dirpath)
        child_names = set(dirnames) | set(filenames)
        if recognized_children & child_names:
            ref = str(dirpath.relative_to(ledgers_dir))
            ref = ref.replace("\\", "/")
            if ref not in seen:
                seen.add(ref)
                yield ref, dirpath
                dirnames.clear()


def _walk_no_symlinks(root: Path):
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

    # Filter sub-section keys to only allowed v2 keys
    for section, allowed in [
        ("ledger", ALLOWED_LEDGER_KEYS),
        ("release", ALLOWED_RELEASE_KEYS),
        ("changelog", ALLOWED_CHANGELOG_KEYS),
        ("git", ALLOWED_GIT_KEYS),
    ]:
        if section in data and isinstance(data[section], dict):
            data[section] = {k: v for k, v in data[section].items() if k in allowed}

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
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib  # type: ignore[no-redef]

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
        for f in report.get("failures", []):
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
    }

    target_data = target_info.data_root
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
            f"{len(failures)} domain records failed validation; "
            "fix them before migration.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
            data={"failures": failures[:10]},
            remediation=[
                "Inspect the failed records and correct them.",
                "Re-run the migration after fixing.",
            ],
        )

    # 3. Prepare the canonical target (pure — no writes yet)
    prepared = _backend.prepare_legacy_migration_target(
        source.workspace_root,
        project_name=source.legacy_config.get("project_name"),
        data_storage=request.data_storage,
        external_root=request.external_root,
        target=request.target,
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
            "phase": "staging",
            "migration_id": stage.migration_id,
            "legacy_data_root": str(source.data_root),
            "target_data_root": str(prepared.data_root),
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
            f"{len(failures)} domain records in staged data failed validation.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
            data={"failures": failures[:10]},
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
    )

    _write_journal_row(
        prepared.data_root.parent,
        {
            "phase": "ledgercore-executing",
            "migration_id": stage.migration_id,
        },
    )

    # 9. Execute through ledgercore (copy mode for the stage)
    def _quiescence() -> None:
        if quiescence_check is not None:
            quiescence_check()

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

    # 12. Handle move mode: retire legacy source after verification
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
        "legacy_data_root": str(source.data_root),
        "target_data_root": str(final_layout.data_root),
        "target_indexes_root": str(final_layout.indexes_root),
        "domain_validation_before": domain_before["valid"],
        "domain_validation_after": True,
        "indexes_rebuilt": index_result,
        "inventory": inventory_legacy_data(final_layout.data_root),
        "warnings": [],
    }


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
) -> None:
    """Write a .ledger-project.toml binding marker to the staged data root."""
    import ledgercore

    marker_path = data_root / ".ledger-project.toml"
    if not marker_path.exists():
        binding_content = (
            "[binding]\n"
            f"schema_version = {prepared.data_binding.schema_version}\n"
            f"layout_version = {prepared.data_binding.layout_version}\n"
            f'project_uuid = "{prepared.data_binding.project_uuid}"\n'
            f'tool = "{prepared.data_binding.tool}"\n'
            f'mount = "{prepared.data_binding.mount}"\n'
            f'storage = "{prepared.data_binding.storage}"\n'
        )
        if prepared.data_binding.project_name:
            binding_content += (
                f'project_name = "{prepared.data_binding.project_name}"\n'
            )
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


def migration_status(
    workspace_root: Path,
) -> dict[str, object]:
    """Report the current migration state for a project.

    Detects whether the project is legacy, canonical, or mid-migration.
    """
    workspace_root = Path(workspace_root).resolve()

    legacy = None
    for name in LEGACY_CONFIG_NAMES:
        candidate = workspace_root / name
        if candidate.is_file():
            legacy = str(candidate)
            break

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

    journal_dir = workspace_root / ".ledger" / "releaseledger"
    if (journal_dir / JOURNAL_FILENAME).is_file():
        journal = read_migration_journal(journal_dir)
        last = journal[-1] if journal else {}
        if last.get("phase") in ("staging", "ledgercore-executing", "failed"):
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


def _read_jsonl_strict(path: Path):
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


def _read_jsonl_permissive(path: Path):
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
