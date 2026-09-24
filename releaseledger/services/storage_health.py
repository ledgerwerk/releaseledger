"""Releaseledger-specific interpretation of canonical storage validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from releaseledger.ledgercore_backend import (
    ReleaseledgerLedgerLayout,
    StorageBinding,
    StorageValidationReport,
    StorageValidationResult,
    classify_releaseledger_index_cache,
)


@dataclass(frozen=True, slots=True)
class MountHealth:
    """Normalized health for one expected Releaseledger storage mount."""

    name: str
    path: Path
    expected_storage: str
    raw_valid: bool
    effective_valid: bool
    repairable: bool
    reason: str
    actual_binding: StorageBinding | None
    classification: str | None = None
    unexpected_paths: tuple[Path, ...] = ()
    mismatch: bool = False


@dataclass(frozen=True, slots=True)
class StorageHealth:
    """Raw and effective storage validity for a resolved project layout."""

    raw_layout_valid: bool
    layout_valid: bool
    indexes_repairable: bool
    mounts: tuple[MountHealth, ...]


def _marker_exists_without_following(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def _expected_storage(layout: ReleaseledgerLedgerLayout, name: str) -> str:
    if name == "config":
        return "project"
    if name == "data":
        return layout.data_storage
    if name == "indexes":
        return "cache"
    return ""


def _validation_results(
    report: StorageValidationReport | None,
) -> dict[str, StorageValidationResult]:
    if report is None:
        return {}
    return {str(result.path): result for result in report.results}


def assess_storage_layout(layout: ReleaseledgerLedgerLayout) -> StorageHealth:
    """Apply Releaseledger's derived-index semantics without changing storage."""
    report = layout.validation_report
    results = _validation_results(report)
    paths = (
        (layout.config_path.parent, "config"),
        (layout.data_root, "data"),
        (layout.indexes_root, "indexes"),
    )
    mounts: list[MountHealth] = []
    for path, name in paths:
        result = results.get(str(path))
        raw_valid = result.valid if result is not None else True
        reason = result.reason or "" if result is not None else ""
        actual = result.binding if result is not None else None
        classification: str | None = None
        unexpected: tuple[Path, ...] = ()
        repairable = False
        mismatch = False
        effective_valid = raw_valid

        if name == "indexes":
            cache = classify_releaseledger_index_cache(path)
            classification = cache.state
            unexpected = cache.unexpected_paths
            marker_is_present = _marker_exists_without_following(
                layout.indexes_binding_path
            )
            unsafe_symlink = (
                path.is_symlink() or layout.indexes_binding_path.is_symlink()
            )
            if unsafe_symlink:
                effective_valid = False
                reason = "Indexes path or binding marker is a symlink and is blocked."
            elif raw_valid and actual is not None:
                classification = "bound"
            elif (
                actual is None
                and not marker_is_present
                and cache.state in {"generated-current", "generated-legacy"}
            ):
                repairable = True
                effective_valid = True
                reason = "Generated index cache is safe to rebind and rebuild."
            elif actual is not None and not raw_valid:
                mismatch = True
                reason = (
                    f"Indexes binding mismatch: found mount={actual.mount!r}, "
                    f"storage={actual.storage!r}, project_uuid={actual.project_uuid!r}."
                )
            elif cache.state == "foreign":
                reason = "Foreign or unsafe index cache content is blocked."
            elif not raw_valid and not reason:
                reason = "Indexes binding is invalid."

        mounts.append(
            MountHealth(
                name=name,
                path=path,
                expected_storage=_expected_storage(layout, name),
                raw_valid=raw_valid,
                effective_valid=effective_valid,
                repairable=repairable,
                reason=reason,
                actual_binding=actual,
                classification=classification,
                unexpected_paths=unexpected,
                mismatch=mismatch,
            )
        )

    raw_layout_valid = report is None or report.valid
    layout_valid = all(mount.effective_valid for mount in mounts)
    indexes_repairable = any(
        mount.name == "indexes" and mount.repairable for mount in mounts
    )
    return StorageHealth(
        raw_layout_valid=raw_layout_valid,
        layout_valid=layout_valid,
        indexes_repairable=indexes_repairable,
        mounts=tuple(mounts),
    )


def storage_health_to_dict(health: StorageHealth) -> dict[str, object]:
    """Serialize normalized storage health into stable CLI data."""
    bindings: dict[str, object] = {}
    for mount in health.mounts:
        actual = mount.actual_binding
        actual_binding: dict[str, object] | None = None
        if actual is not None:
            actual_binding = {
                "schema_version": actual.schema_version,
                "layout_version": actual.layout_version,
                "project_uuid": actual.project_uuid,
                "project_name": actual.project_name,
                "tool": actual.tool,
                "mount": actual.mount,
                "storage": actual.storage,
            }
        item: dict[str, object] = {
            "path": str(mount.path),
            "storage": mount.expected_storage,
            "raw_valid": mount.raw_valid,
            "valid": mount.effective_valid,
            "repairable": mount.repairable,
            "reason": mount.reason,
            "actual_binding": actual_binding,
            "mismatch": mount.mismatch,
        }
        if mount.classification is not None:
            item["classification"] = mount.classification
        if mount.unexpected_paths:
            item["unexpected_paths"] = [
                path.as_posix() for path in mount.unexpected_paths
            ]
        bindings[mount.name] = item
    indexes_mount = next(mount for mount in health.mounts if mount.name == "indexes")
    repair_command = (
        "releaseledger repair index --dry-run"
        if not indexes_mount.effective_valid
        else None
    )
    return {
        "raw_layout_valid": health.raw_layout_valid,
        "layout_valid": health.layout_valid,
        "indexes_repairable": health.indexes_repairable,
        "repair_command": repair_command,
        "bindings": bindings,
    }
