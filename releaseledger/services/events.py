"""Append-only release event log backed by ``events.jsonl``.

Every mutating release/entry operation appends one event so the ledger keeps a
durable, ordered audit trail. The whole file is rewritten atomically on each
append (events files are small); this keeps writes crash-safe and consistent
with the rest of releaseledger's atomic storage.
"""

from __future__ import annotations

from pathlib import Path

import ledgercore

from releaseledger.domain.event import ReleaseEvent, event_from_dict
from releaseledger.storage.paths import resolve_project_paths

__all__ = ["append_event", "load_events"]


def load_events(workspace_root: Path) -> list[ReleaseEvent]:
    """Load all events, oldest first. Missing file means no events."""
    paths = resolve_project_paths(workspace_root)
    if not paths.events_path.is_file():
        return []
    result = ledgercore.load_jsonl_objects(paths.events_path, missing="empty")
    events: list[ReleaseEvent] = []
    for row in result.rows:
        if isinstance(row, dict):
            events.append(event_from_dict(dict(row)))
    return events


def append_event(
    workspace_root: Path,
    *,
    event: str,
    release_version: str | None = None,
    entry_id: str | None = None,
    record_revisions: dict[str, int] | None = None,
    data: dict[str, object] | None = None,
    ledger_ref: str | None = None,
) -> ReleaseEvent:
    """Append a new event and return it. Assigns the next ``event-NNNN`` id."""
    paths = resolve_project_paths(workspace_root, ledger_ref=ledger_ref)
    existing = load_events(workspace_root)
    event_id = ledgercore.next_prefixed_id("event", [e.event_id for e in existing])
    record = ReleaseEvent(
        event_id=event_id,
        event=event,
        release_version=release_version,
        entry_id=entry_id,
        record_revisions=dict(record_revisions) if record_revisions else {},
        data=dict(data) if data else {},
    )
    rows = [e.to_dict() for e in existing]
    rows.append(record.to_dict())
    ledgercore.ensure_dir(paths.events_dir)
    ledgercore.write_jsonl_objects(paths.events_path, rows)
    return record
