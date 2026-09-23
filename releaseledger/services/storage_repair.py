"""Explicit repair operations for derived Releaseledger storage."""

from __future__ import annotations

import os
from pathlib import Path

from releaseledger.errors import CODE_CONFIG_ERROR, LaunchError
from releaseledger.ledgercore_backend import (
    StorageBinding,
    load_releaseledger_ledger_layout,
)
from releaseledger.services.entries import list_release_entries
from releaseledger.services.releases import list_release_records
from releaseledger.services.storage_health import (
    MountHealth,
    StorageHealth,
    assess_storage_layout,
)
from releaseledger.storage.store import rebuild_indexes


def _indexes_mount(health: StorageHealth) -> MountHealth:
    return next(mount for mount in health.mounts if mount.name == "indexes")


def _ledger_refs(root: Path, layout_root: Path, classification: str) -> tuple[str, ...]:
    from releaseledger.storage.paths import resolve_project_paths

    paths = resolve_project_paths(root)
    refs = {paths.ledger_ref}
    for ledger_root in (paths.project.data_root / "ledgers",):
        if ledger_root.is_dir():
            refs.update(
                child.name
                for child in ledger_root.iterdir()
                if child.is_dir() and not child.is_symlink()
            )
    if classification == "generated-current":
        index_ledgers = layout_root / "ledgers"
        if index_ledgers.is_dir():
            refs.update(
                child.name
                for child in index_ledgers.iterdir()
                if child.is_dir() and not child.is_symlink()
            )
    return tuple(sorted(refs))


def _binding_details(binding: StorageBinding | None) -> dict[str, object] | None:
    if binding is None:
        return None
    return {
        "schema_version": binding.schema_version,
        "layout_version": binding.layout_version,
        "project_uuid": binding.project_uuid,
        "project_name": binding.project_name,
        "tool": binding.tool,
        "mount": binding.mount,
        "storage": binding.storage,
    }


def _cache_counts(workspace_root: Path) -> tuple[int, int]:
    releases = list_release_records(workspace_root)
    entry_count = sum(
        len(list_release_entries(workspace_root, str(release["version"])))
        for release in releases
    )
    return len(releases), entry_count


def _quarantine_candidate(source: Path) -> Path:
    suffix = 1
    while True:
        name = f"{source.name}.quarantine" + (f"-{suffix}" if suffix > 1 else "")
        container = source.with_name(name)
        if not os.path.lexists(container):
            return container / source.name
        suffix += 1


def _quarantine_cache(source: Path) -> Path:
    """Move the complete cache into a newly reserved sibling quarantine."""
    while True:
        destination = _quarantine_candidate(source)
        container = destination.parent
        try:
            container.mkdir()
        except FileExistsError:
            continue
        try:
            os.rename(source, destination)
        except OSError:
            container.rmdir()
            raise
        return destination


def repair_index(
    workspace_root: Path,
    *,
    apply: bool = False,
    quarantine_foreign: bool = False,
) -> dict[str, object]:
    """Inspect or repair the disposable indexes cache without changing records."""
    root = Path(workspace_root).resolve()
    layout = load_releaseledger_ledger_layout(
        root, allow_missing=False, validate_storage=True
    )
    health = assess_storage_layout(layout)
    indexes = _indexes_mount(health)
    classification = indexes.classification or "unknown"
    source = layout.indexes_root
    unexpected = [path.as_posix() for path in indexes.unexpected_paths]
    blocked = not indexes.effective_valid
    needs_rebuild = classification in {
        "missing",
        "empty",
        "generated-current",
        "generated-legacy",
    }

    if blocked and not quarantine_foreign:
        action = "blocked"
    elif blocked:
        action = "quarantine-and-rebuild"
    elif needs_rebuild:
        action = "rebind-and-rebuild"
    else:
        action = "no-op"

    ledger_refs = _ledger_refs(root, source, classification)
    quarantine_path: Path | None = None
    if apply and blocked and not quarantine_foreign:
        raise LaunchError(
            "The indexes cache contains foreign, mismatched, or unsafe state. "
            "No files were changed.",
            code=CODE_CONFIG_ERROR,
            exit_code=2,
            data={
                "kind": "index_repair",
                "action": "blocked",
                "classification_before": classification,
                "source": str(source),
                "unexpected_paths": unexpected,
                "actual_binding": _binding_details(indexes.actual_binding),
                "applied": False,
            },
            remediation=[
                "Run `releaseledger repair index --dry-run` to inspect the cache.",
                "Use `--quarantine-foreign` only when preserving and replacing the entire cache is authorized.",
            ],
        )

    result: dict[str, object] = {
        "kind": "index_repair",
        "applied": apply,
        "action": action,
        "classification_before": classification,
        "source": str(source),
        "quarantine_path": None,
        "unexpected_paths": unexpected,
        "raw_layout_valid_before": health.raw_layout_valid,
        "mismatch": indexes.mismatch,
        "reason": indexes.reason,
        "binding_restored": indexes.raw_valid,
        "indexes_rebuilt": False,
        "release_count": 0,
        "entry_count": 0,
        "layout_valid_after": health.layout_valid,
    }
    if action == "blocked":
        result["next_command"] = "releaseledger repair index --dry-run"
        return result
    if action == "no-op":
        result["binding_restored"] = True
        return result

    if not apply:
        if blocked and quarantine_foreign:
            result["quarantine_path"] = str(_quarantine_candidate(source))
        return result

    if blocked:
        quarantine_path = _quarantine_cache(source)
        result["quarantine_path"] = str(quarantine_path)

    for ledger_ref in ledger_refs:
        rebuild_indexes(root, ledger_ref=ledger_ref)
    after_layout = load_releaseledger_ledger_layout(
        root, allow_missing=False, validate_storage=True
    )
    after_health = assess_storage_layout(after_layout)
    after_indexes = _indexes_mount(after_health)
    if not after_health.layout_valid or not after_indexes.raw_valid:
        raise LaunchError(
            "Index repair did not produce a valid storage layout.",
            code=CODE_CONFIG_ERROR,
            exit_code=1,
            data={
                "kind": "index_repair",
                "source": str(source),
                "quarantine_path": str(quarantine_path) if quarantine_path else None,
                "layout_valid_after": after_health.layout_valid,
            },
        )

    release_count, entry_count = _cache_counts(root)
    result.update(
        {"binding_restored": True, "indexes_rebuilt": True},
    )
    result.update(
        {
            "release_count": release_count,
            "entry_count": entry_count,
            "layout_valid_after": after_health.layout_valid,
            "raw_layout_valid_after": after_health.raw_layout_valid,
        }
    )
    return result
