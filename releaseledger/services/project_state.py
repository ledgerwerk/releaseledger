"""Read-only project state aggregation for common CLI commands."""

from __future__ import annotations

from pathlib import Path

from releaseledger.services.config import config_show, storage_where
from releaseledger.services.entries import list_release_entries
from releaseledger.services.releases import list_release_records

_ACTIVE_STATUSES = {"planned", "draft", "candidate"}


def _records(root: Path) -> list[dict[str, object]]:
    try:
        return list_release_records(root)
    except Exception:
        return []


def _release_counts(records: list[dict[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        status = str(record.get("status", "unknown"))
        counts[status] = counts.get(status, 0) + 1
    return dict(sorted(counts.items()))


def _latest_released(records: list[dict[str, object]]) -> str | None:
    released = [
        record for record in records if str(record.get("status", "")) == "released"
    ]
    if not released:
        return None
    released.sort(
        key=lambda record: (
            str(record.get("released_at", "")),
            str(record.get("version", "")),
        )
    )
    return str(released[-1].get("version", ""))


def project_status(root: Path, *, check: bool = False) -> dict[str, object]:
    """Return a concise status without creating or repairing project state."""
    storage = storage_where(root)
    state = str(storage.get("migration_state", "uninitialized"))
    initialized = state == "canonical-ready"
    records = _records(root) if initialized else []
    active = [
        str(record.get("version", ""))
        for record in records
        if str(record.get("status", "")) in _ACTIVE_STATUSES
    ]
    active.sort()
    healthy = initialized and bool(storage.get("layout_valid", False))
    if not initialized:
        next_action = {
            "command": "init",
            "reason": "No canonical Releaseledger project is initialized.",
        }
    elif active:
        next_action = {
            "command": f"release review {active[0]}",
            "reason": "A planned release is active.",
        }
    else:
        next_action = {
            "command": "release create <version>",
            "reason": "No active planned release exists.",
        }
    result: dict[str, object] = {
        "kind": "project_status",
        "initialized": initialized,
        "state": "ready" if healthy else state,
        "project_root": str(storage.get("project_root", root.resolve())),
        "ledger_ref": str(storage.get("active_ledger_ref", "")),
        "migration_state": state,
        "release_counts": _release_counts(records),
        "active_releases": active,
        "latest_released": _latest_released(records),
        "health": "ok" if healthy else "unavailable",
        "next_action": next_action,
    }
    if check:
        result["passed"] = healthy
    return result


def project_info(root: Path) -> dict[str, object]:
    """Return the full deterministic read-only project inventory."""
    status = project_status(root)
    storage = storage_where(root)
    config = (
        config_show(root)
        if status["initialized"]
        else {
            "kind": "config_show",
            "project_root": str(root.resolve()),
            "config": {},
        }
    )
    records = _records(root)
    entry_counts: dict[str, int] = {}
    total_entries = 0
    for record in records:
        version = str(record.get("version", ""))
        entries = list_release_entries(root, version) if status["initialized"] else []
        entry_counts[version] = len(entries)
        total_entries += len(entries)
    return {
        "kind": "project_info",
        "status": status,
        "storage": storage,
        "config": config,
        "releases": records,
        "release_count": len(records),
        "entry_counts": entry_counts,
        "entry_count": total_entries,
    }


def project_doctor(root: Path, *, check: bool = False) -> dict[str, object]:
    """Run deterministic diagnostics without applying repairs."""
    storage = storage_where(root)
    checks: list[dict[str, object]] = []
    initialized = str(storage.get("migration_state")) == "canonical-ready"
    checks.append(
        {
            "code": "project_discovery",
            "status": "pass" if initialized else "fail",
            "message": "Canonical project discovered."
            if initialized
            else "No canonical project discovered.",
            "remediation": [] if initialized else ["Run `releaseledger init`."],
        }
    )
    layout_valid = bool(storage.get("layout_valid", False))
    checks.append(
        {
            "code": "storage_layout",
            "status": "pass" if layout_valid else "fail",
            "message": "Storage layout is valid."
            if layout_valid
            else "Storage layout is unavailable or invalid.",
            "remediation": []
            if layout_valid
            else ["Run `releaseledger storage validate --strict`."],
        }
    )
    release_ok = True
    if initialized:
        try:
            _records(root)
        except Exception:
            release_ok = False
    checks.append(
        {
            "code": "release_records",
            "status": "pass" if release_ok else "fail",
            "message": "Release records are parseable."
            if release_ok
            else "Release records could not be parsed.",
            "remediation": [],
        }
    )
    passed = all(item["status"] == "pass" for item in checks)
    result: dict[str, object] = {
        "kind": "doctor",
        "ok": passed,
        "checks": checks,
        "passed": passed if check else True,
    }
    return result


def next_action(root: Path) -> dict[str, object]:
    """Return one recommendation without executing it."""
    storage = storage_where(root)
    state = str(storage.get("migration_state", "uninitialized"))
    if state == "legacy":
        command = "migrate plan storage-layout"
        reason = "Legacy storage requires an explicit migration plan."
    elif state != "canonical-ready":
        command = "init"
        reason = "No canonical project is initialized."
    else:
        status = project_status(root)
        recommendation = status["next_action"]
        assert isinstance(recommendation, dict)
        command = str(recommendation["command"])
        reason = str(recommendation["reason"])
    return {
        "kind": "next_action",
        "command": command,
        "reason": reason,
        "project_root": str(storage.get("project_root", root.resolve())),
    }
