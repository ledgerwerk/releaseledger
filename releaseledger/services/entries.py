"""Release entry lifecycle services."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import ledgercore

from releaseledger.domain.entry import (
    ReleaseEntryRecord,
    normalize_entry_kind,
    normalize_entry_status,
    normalize_scopes,
    validate_source_refs,
)
from releaseledger.domain.event import (
    EVENT_ENTRY_ADDED,
    EVENT_ENTRY_BATCH_ADDED,
    EVENT_ENTRY_IMPORTED,
    EVENT_ENTRY_UPDATED,
)
from releaseledger.domain.versioning import bump_versioning
from releaseledger.errors import (
    CODE_CONFLICT,
    CODE_NOT_FOUND,
    CODE_VALIDATION_ERROR,
    LaunchError,
)
from releaseledger.services.events import append_event
from releaseledger.storage.paths import resolve_project_paths
from releaseledger.storage.store import (
    delete_entry,
    load_entries,
    load_release,
    rebuild_indexes,
    save_entry,
    save_release,
)

__all__ = [
    "add_many_release_entries",
    "add_release_entry",
    "import_release_entry_file",
    "list_release_entries",
    "load_entry_batch_file",
    "show_release_entry",
    "update_release_entry",
]


def _validate_paths(paths: tuple[str, ...]) -> tuple[str, ...]:
    validated: list[str] = []
    for raw in paths:
        try:
            value = ledgercore.validate_relative_posix_path(raw, field_name="--path")
        except ledgercore.PathValidationError as exc:
            raise LaunchError(
                str(exc), code=CODE_VALIDATION_ERROR, exit_code=2
            ) from exc
        if value not in validated:
            validated.append(value)
    return tuple(validated)


def _require_summary(summary: object) -> str:
    if not isinstance(summary, str) or not summary.strip():
        raise LaunchError(
            "Entry summary must be a non-empty string.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    return summary.strip()


def _string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise LaunchError(
            f"Entry field {field_name!r} must be a list.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    if not all(isinstance(item, str) for item in value):
        raise LaunchError(
            f"Entry field {field_name!r} must contain only strings.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    return tuple(value)


def _optional_string(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise LaunchError(
            f"Entry field {field_name!r} must be a string.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    return value


def _boolean(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise LaunchError(
            f"Entry field {field_name!r} must be a boolean.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    return value


def _candidate(
    *,
    entry_id: str,
    release_version: str,
    order: int,
    kind: object,
    summary: object,
    body: object = None,
    status: object = "accepted",
    audience: object = None,
    scopes: object = (),
    source_refs: object = (),
    paths: object = (),
    issues: object = (),
    prs: object = (),
    sources: object = (),
    breaking: object = False,
    internal: object = False,
) -> ReleaseEntryRecord:
    if not isinstance(kind, str):
        raise LaunchError(
            "Entry kind must be a string.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    if not isinstance(status, str):
        raise LaunchError(
            "Entry status must be a string.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    return ReleaseEntryRecord(
        entry_id=entry_id,
        release_version=release_version,
        kind=normalize_entry_kind(kind),
        summary=_require_summary(summary),
        body=_optional_string(body, "body"),
        status=normalize_entry_status(status),
        audience=_optional_string(audience, "audience"),
        scopes=normalize_scopes(_string_tuple(scopes, "scopes")),
        source_refs=validate_source_refs(_string_tuple(source_refs, "source_refs")),
        paths=_validate_paths(_string_tuple(paths, "paths")),
        issues=_string_tuple(issues, "issues"),
        prs=_string_tuple(prs, "prs"),
        sources=_string_tuple(sources, "sources"),
        breaking=_boolean(breaking, "breaking"),
        internal=_boolean(internal, "internal"),
        order=order,
    )


def _payload(
    workspace_root: Path,
    release_version: str,
    record: ReleaseEntryRecord,
    *,
    kind: str = "release_entry",
    events: list[str] | None = None,
    written: bool = True,
) -> dict[str, object]:
    result: dict[str, object] = {
        "kind": kind,
        "ledger_ref": resolve_project_paths(workspace_root).ledger_ref,
        "release_version": release_version,
        "entry": record.to_dict(),
        "written": written,
    }
    if events:
        result["events"] = events
    return result


def next_entry_id_from(entries: list[ReleaseEntryRecord]) -> str:
    return ledgercore.next_prefixed_id("entry", [entry.entry_id for entry in entries])


def add_release_entry(
    workspace_root: Path,
    *,
    release_version: str,
    kind: str,
    summary: str,
    body: str | None = None,
    status: str = "accepted",
    audience: str | None = None,
    scopes: tuple[str, ...] = (),
    source_refs: tuple[str, ...] = (),
    paths: tuple[str, ...] = (),
    issues: tuple[str, ...] = (),
    prs: tuple[str, ...] = (),
    sources: tuple[str, ...] = (),
    breaking: bool = False,
    internal: bool = False,
    dry_run: bool = False,
) -> dict[str, object]:
    release = load_release(workspace_root, release_version)
    entries = load_entries(workspace_root, release_version)
    record = _candidate(
        entry_id=next_entry_id_from(entries),
        release_version=release.version,
        order=len(entries) + 1,
        kind=kind,
        summary=summary,
        body=body,
        status=status,
        audience=audience,
        scopes=scopes,
        source_refs=source_refs,
        paths=paths,
        issues=issues,
        prs=prs,
        sources=sources,
        breaking=breaking,
        internal=internal,
    )
    if dry_run:
        return _payload(
            workspace_root,
            release.version,
            record,
            kind="release_entry_preview",
            written=False,
        )
    save_entry(workspace_root, record)
    updated_release = replace(
        release,
        entry_count=len(entries) + 1,
        versioning=bump_versioning(release.versioning),
    )
    try:
        save_release(
            workspace_root,
            updated_release,
            overwrite=True,
        )
    except LaunchError:
        # Roll back the orphan entry file so a stale release revision
        # cannot leave a partial write: no entry-*.md and entry_count
        # is never bumped.
        delete_entry(workspace_root, release.version, record.entry_id)
        raise
    event = append_event(
        workspace_root,
        event=EVENT_ENTRY_ADDED,
        release_version=release.version,
        entry_id=record.entry_id,
        record_revisions={
            f"release:{release.version}": updated_release.versioning.revision,
            f"entry:{release.version}/{record.entry_id}": record.versioning.revision,
        },
        data={"kind": record.kind, "status": record.status},
    )
    rebuild_indexes(workspace_root)
    return _payload(workspace_root, release.version, record, events=[event.event_id])


def _find_entry(
    workspace_root: Path, release_version: str, entry_id: str
) -> ReleaseEntryRecord:
    load_release(workspace_root, release_version)
    for entry in load_entries(workspace_root, release_version):
        if entry.entry_id == entry_id:
            return entry
    raise LaunchError(
        f"Entry not found: {release_version}/{entry_id}",
        code=CODE_NOT_FOUND,
        exit_code=2,
    )


def show_release_entry(
    workspace_root: Path, release_version: str, entry_id: str
) -> dict[str, object]:
    record = _find_entry(workspace_root, release_version, entry_id)
    return _payload(workspace_root, release_version, record)


def update_release_entry(
    workspace_root: Path,
    *,
    release_version: str,
    entry_id: str,
    kind: str | None = None,
    summary: str | None = None,
    body: str | None = None,
    status: str | None = None,
    audience: str | None = None,
    scopes: tuple[str, ...] | None = None,
    source_refs: tuple[str, ...] | None = None,
    paths: tuple[str, ...] | None = None,
    issues: tuple[str, ...] | None = None,
    prs: tuple[str, ...] | None = None,
    breaking: bool | None = None,
    internal: bool | None = None,
) -> dict[str, object]:
    existing = _find_entry(workspace_root, release_version, entry_id)
    candidate = _candidate(
        entry_id=existing.entry_id,
        release_version=existing.release_version,
        order=existing.order or 0,
        kind=kind if kind is not None else existing.kind,
        summary=summary if summary is not None else existing.summary,
        body=body if body is not None else existing.body,
        status=status if status is not None else existing.status,
        audience=audience if audience is not None else existing.audience,
        scopes=scopes if scopes is not None else existing.scopes,
        source_refs=(source_refs if source_refs is not None else existing.source_refs),
        paths=paths if paths is not None else existing.paths,
        issues=issues if issues is not None else existing.issues,
        prs=prs if prs is not None else existing.prs,
        sources=existing.sources,
        breaking=breaking if breaking is not None else existing.breaking,
        internal=internal if internal is not None else existing.internal,
    )
    changes = {
        field: getattr(candidate, field)
        for field in (
            "kind",
            "summary",
            "body",
            "status",
            "audience",
            "scopes",
            "source_refs",
            "paths",
            "issues",
            "prs",
            "breaking",
            "internal",
        )
    }
    if all(getattr(existing, field) == value for field, value in changes.items()):
        raise LaunchError(
            "Entry update did not change any fields.",
            code=CODE_CONFLICT,
            exit_code=2,
        )
    updated = replace(
        existing,
        kind=candidate.kind,
        summary=candidate.summary,
        body=candidate.body,
        status=candidate.status,
        audience=candidate.audience,
        scopes=candidate.scopes,
        source_refs=candidate.source_refs,
        paths=candidate.paths,
        issues=candidate.issues,
        prs=candidate.prs,
        breaking=candidate.breaking,
        internal=candidate.internal,
        versioning=bump_versioning(existing.versioning),
    )
    save_entry(workspace_root, updated)
    event = append_event(
        workspace_root,
        event=EVENT_ENTRY_UPDATED,
        release_version=release_version,
        entry_id=entry_id,
        record_revisions={
            f"entry:{release_version}/{entry_id}": updated.versioning.revision
        },
        data={
            "fields": sorted(
                field
                for field, value in changes.items()
                if getattr(existing, field) != value
            )
        },
    )
    rebuild_indexes(workspace_root)
    return _payload(workspace_root, release_version, updated, events=[event.event_id])


def _globalize_legacy_ref(value: object, source_ledger: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise LaunchError(
            "Legacy source IDs must be strings.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    if source_ledger is None:
        raise LaunchError(
            "Legacy local task/run IDs require --source-ledger.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    try:
        return ledgercore.parse_resource_ref(
            value, default_ledger=source_ledger
        ).global_ref
    except ledgercore.IdFormatError as exc:
        raise LaunchError(
            f"Invalid legacy source ID {value!r}: {exc}",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        ) from exc


def import_release_entry_file(
    workspace_root: Path,
    *,
    release_version: str,
    source_path: Path,
    replace_existing: bool = False,
    source_ledger: str | None = None,
) -> dict[str, object]:
    release = load_release(workspace_root, release_version)
    try:
        metadata, body = ledgercore.read_front_matter_document(source_path)
    except (OSError, ledgercore.FrontMatterError) as exc:
        raise LaunchError(
            f"Cannot import entry file {source_path}: {exc}",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        ) from exc
    data = dict(metadata)
    object_type = data.get("object_type")
    if object_type == "changelog_entry":
        refs = list(_string_tuple(data.get("source_refs", []), "source_refs"))
        for field in ("task_id", "source_run_id"):
            ref = _globalize_legacy_ref(data.get(field), source_ledger)
            if ref is not None:
                refs.append(ref)
        data = {
            "entry_id": data.get("entry_id"),
            "kind": data.get("category", data.get("kind")),
            "summary": data.get("summary"),
            "body": body or data.get("body"),
            "status": data.get("status", "accepted"),
            "audience": data.get("audience"),
            "scopes": data.get("scopes", []),
            "source_refs": refs,
            "paths": data.get("paths", []),
            "issues": data.get("issues", []),
            "prs": data.get("prs", []),
            "breaking": data.get("breaking", False),
            "internal": data.get("internal", False),
        }
    elif object_type != "release_entry":
        raise LaunchError(
            "Imported entry object_type must be 'release_entry' or 'changelog_entry'.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    source_version = data.get("release_version")
    if source_version is not None and source_version != release_version:
        raise LaunchError(
            f"Imported release_version {source_version!r} does not match "
            f"{release_version!r}.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    entries = load_entries(workspace_root, release_version)
    raw_id = data.get("entry_id")
    entry_id = raw_id if isinstance(raw_id, str) else next_entry_id_from(entries)
    existing = next((entry for entry in entries if entry.entry_id == entry_id), None)
    if existing is not None and not replace_existing:
        raise LaunchError(
            f"Entry already exists: {entry_id}; pass --replace to overwrite.",
            code=CODE_CONFLICT,
            exit_code=2,
        )
    order = (
        existing.order if existing and existing.order is not None else len(entries) + 1
    )
    record = _candidate(
        entry_id=entry_id,
        release_version=release.version,
        order=order,
        kind=data.get("kind"),
        summary=data.get("summary"),
        body=body or data.get("body"),
        status=data.get("status", "accepted"),
        audience=data.get("audience"),
        scopes=data.get("scopes", []),
        source_refs=data.get("source_refs", []),
        paths=data.get("paths", []),
        issues=data.get("issues", []),
        prs=data.get("prs", []),
        sources=data.get("sources", []),
        breaking=data.get("breaking", False),
        internal=data.get("internal", False),
    )
    if existing is not None:
        record = replace(
            record,
            versioning=bump_versioning(existing.versioning),
        )
    save_entry(workspace_root, record)
    updated_release = release
    if existing is None:
        updated_release = replace(
            release,
            entry_count=len(entries) + 1,
            versioning=bump_versioning(release.versioning),
        )
        try:
            save_release(
                workspace_root,
                updated_release,
                overwrite=True,
            )
        except LaunchError:
            # Roll back the orphan entry file so a stale release revision
            # cannot leave a partial write (new-entry path only).
            delete_entry(workspace_root, release.version, record.entry_id)
            raise
    record_revisions = {
        f"entry:{release_version}/{entry_id}": record.versioning.revision
    }
    if existing is None:
        record_revisions[f"release:{release_version}"] = (
            updated_release.versioning.revision
        )
    event = append_event(
        workspace_root,
        event=EVENT_ENTRY_IMPORTED,
        release_version=release_version,
        entry_id=entry_id,
        record_revisions=record_revisions,
        data={"source_path": str(source_path), "replaced": existing is not None},
    )
    rebuild_indexes(workspace_root)
    return _payload(workspace_root, release_version, record, events=[event.event_id])


def load_entry_batch_file(source_path: Path) -> list[dict[str, object]]:
    try:
        payload = ledgercore.load_yaml_object(source_path, label="entry batch")
    except ledgercore.YamlStoreError as exc:
        raise LaunchError(str(exc), code=CODE_VALIDATION_ERROR, exit_code=2) from exc
    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, list):
        raise LaunchError(
            "Entry batch must contain an 'entries' list.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    if not all(isinstance(item, dict) for item in raw_entries):
        raise LaunchError(
            "Every entry batch item must be a mapping.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    return [dict(item) for item in raw_entries]


def add_many_release_entries(
    workspace_root: Path,
    *,
    release_version: str,
    entries: list[dict[str, object]],
    dry_run: bool = False,
    fail_on_warning: bool = True,
) -> dict[str, object]:
    del fail_on_warning  # Applied by the lint-aware CLI in the next layer.
    release = load_release(workspace_root, release_version)
    existing = load_entries(workspace_root, release_version)
    proposed: list[ReleaseEntryRecord] = []
    issues: list[dict[str, object]] = []
    ids = [entry.entry_id for entry in existing]
    for index, data in enumerate(entries):
        entry_id = ledgercore.next_prefixed_id("entry", ids)
        ids.append(entry_id)
        try:
            record = _candidate(
                entry_id=entry_id,
                release_version=release.version,
                order=len(existing) + index + 1,
                kind=data.get("kind"),
                summary=data.get("summary"),
                body=data.get("body"),
                status=data.get("status", "accepted"),
                audience=data.get("audience"),
                scopes=data.get("scopes", []),
                source_refs=data.get("source_refs", []),
                paths=data.get("paths", []),
                issues=data.get("issues", []),
                prs=data.get("prs", []),
                sources=data.get("sources", []),
                breaking=data.get("breaking", False),
                internal=data.get("internal", False),
            )
        except LaunchError as exc:
            field = next(
                (
                    name
                    for name in (
                        "summary",
                        "status",
                        "source_refs",
                        "scopes",
                        "paths",
                        "kind",
                        "audience",
                        "issues",
                        "prs",
                        "breaking",
                        "internal",
                    )
                    if name.replace("_", " ") in exc.message.lower()
                ),
                "entry",
            )
            issues.append(
                {
                    "index": index,
                    "entry_id": entry_id,
                    "field": field,
                    "severity": "error",
                    "message": exc.message,
                }
            )
            continue
        proposed.append(record)
    if issues:
        return {
            "kind": "release_entry_batch_preview",
            "ledger_ref": resolve_project_paths(workspace_root).ledger_ref,
            "release_version": release_version,
            "entries": [record.to_dict() for record in proposed],
            "entry_ids": [record.entry_id for record in proposed],
            "issues": issues,
            "written": False,
            "events": [],
        }
    if not dry_run:
        for record in proposed:
            save_entry(workspace_root, record)
        updated_release = replace(
            release,
            entry_count=len(existing) + len(proposed),
            versioning=bump_versioning(release.versioning),
        )
        try:
            save_release(
                workspace_root,
                updated_release,
                overwrite=True,
            )
        except LaunchError:
            # Roll back every just-written entry file so a stale release
            # revision cannot leave orphan entries or a bumped entry_count.
            for record in proposed:
                delete_entry(workspace_root, release.version, record.entry_id)
            raise
        event = append_event(
            workspace_root,
            event=EVENT_ENTRY_BATCH_ADDED,
            release_version=release_version,
            record_revisions={
                f"release:{release_version}": updated_release.versioning.revision,
                **{
                    f"entry:{release_version}/{record.entry_id}": (
                        record.versioning.revision
                    )
                    for record in proposed
                },
            },
            data={"entry_ids": [record.entry_id for record in proposed]},
        )
        rebuild_indexes(workspace_root)
        event_ids = [event.event_id]
    else:
        event_ids = []
    return {
        "kind": "release_entry_batch_preview" if dry_run else "release_entry_batch",
        "ledger_ref": resolve_project_paths(workspace_root).ledger_ref,
        "release_version": release_version,
        "entries": [record.to_dict() for record in proposed],
        "entry_ids": [record.entry_id for record in proposed],
        "issues": issues,
        "written": not dry_run,
        "events": event_ids,
    }


def list_release_entries(
    workspace_root: Path, release_version: str
) -> list[dict[str, object]]:
    load_release(workspace_root, release_version)
    return [entry.to_dict() for entry in load_entries(workspace_root, release_version)]
