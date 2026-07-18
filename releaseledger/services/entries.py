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
    EVENT_ENTRY_DELETED,
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
from releaseledger.services.audit import (
    project_audit_entry_coverage,
    sync_audit_sheet_targets,
)
from releaseledger.services.entry_lint import lint_entry_records
from releaseledger.services.events import append_event
from releaseledger.storage.paths import resolve_project_paths
from releaseledger.storage.store import (
    delete_entry,
    load_commit_audit_sheet,
    load_entries,
    load_release,
    next_commit_audit_versioning,
    rebuild_indexes,
    save_commit_audit_sheet,
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
    "delete_release_entry",
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


def _entry_fingerprint(entry: ReleaseEntryRecord) -> str:
    parts = [
        entry.kind.strip().lower(),
        " ".join(entry.summary.strip().split()),
        "\n".join(sorted(entry.source_refs)),
        "\n".join(sorted(entry.paths)),
    ]
    return "\n".join(parts)


def _batch_result(
    workspace_root: Path,
    *,
    release_version: str,
    proposed: list[ReleaseEntryRecord],
    issues: list[dict[str, object]],
    lint: dict[str, object],
    coverage_projection: dict[str, object] | None,
    written: bool,
    events: list[str],
    audit_sync: dict[str, object] | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "kind": "release_entry_batch" if written else "release_entry_batch_preview",
        "ledger_ref": resolve_project_paths(workspace_root).ledger_ref,
        "release_version": release_version,
        "entries": [record.to_dict() for record in proposed],
        "entry_ids": [record.entry_id for record in proposed],
        "issues": issues,
        "lint": lint,
        "written": written,
        "events": events,
    }
    if coverage_projection is not None:
        result["coverage_projection"] = coverage_projection
    if audit_sync is not None:
        result["audit_sync"] = audit_sync
    return result


def _duplicate_batch_issues(
    *,
    existing: list[ReleaseEntryRecord],
    proposed: list[ReleaseEntryRecord],
) -> list[dict[str, object]]:
    issues: list[dict[str, object]] = []
    source_ref_owner: dict[str, str] = {}
    fingerprint_owner: dict[str, str] = {}
    for entry in existing:
        for ref in entry.source_refs:
            source_ref_owner.setdefault(ref, entry.entry_id)
        fingerprint_owner.setdefault(_entry_fingerprint(entry), entry.entry_id)
    for entry in proposed:
        for ref in entry.source_refs:
            owner = source_ref_owner.get(ref)
            if owner is not None:
                issues.append(
                    {
                        "entry_id": entry.entry_id,
                        "severity": "error",
                        "field": "source_refs",
                        "code": "duplicate_source_ref",
                        "message": (
                            f"Source ref {ref} is already the coverage identity of "
                            f"{owner}. For another changelog bullet from the same "
                            "commit, place the ref in `sources`, not `source_refs`."
                        ),
                    }
                )
            else:
                source_ref_owner[ref] = entry.entry_id
        fingerprint = _entry_fingerprint(entry)
        owner = fingerprint_owner.get(fingerprint)
        if owner is not None:
            issues.append(
                {
                    "entry_id": entry.entry_id,
                    "severity": "error",
                    "field": "summary",
                    "code": "duplicate_fingerprint",
                    "message": (
                        f"Entry content duplicates {owner}"
                        " by kind/summary/source refs/paths."
                    ),
                }
            )
        else:
            fingerprint_owner[fingerprint] = entry.entry_id
    return issues


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
    duplicate_issues = _duplicate_batch_issues(existing=entries, proposed=[record])
    if duplicate_issues:
        raise LaunchError(
            str(duplicate_issues[0]["message"]),
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
            data={"issues": duplicate_issues, "entry": record.to_dict()},
            remediation=[
                "Use `sources` for supporting provenance when another entry "
                "already owns the commit in `source_refs`."
            ],
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
    fail_on_warning: bool = False,
    sync_audit: bool = False,
) -> dict[str, object]:
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
                    "code": exc.code.lower(),
                    "message": exc.message,
                }
            )
            continue
        proposed.append(record)
    lint = lint_entry_records(proposed, strict=fail_on_warning)
    lint_issues = lint.get("issues", [])
    if isinstance(lint_issues, list):
        issues.extend(lint_issues)
    issues.extend(_duplicate_batch_issues(existing=existing, proposed=proposed))
    combined_entries = [*existing, *proposed]
    audit_sheet = load_commit_audit_sheet(workspace_root, release_version)
    coverage_projection = (
        project_audit_entry_coverage(
            audit_sheet, combined_entries, include_internal=True
        )
        if audit_sheet is not None
        else None
    )
    if sync_audit and audit_sheet is None:
        issues.append(
            {
                "severity": "error",
                "field": "audit",
                "code": "missing_audit_sheet",
                "message": (
                    "Cannot sync audit targets because the release has no audit sheet."
                ),
            }
        )
    blocking_issues = [
        issue
        for issue in issues
        if str(issue.get("severity", "error")) == "error"
        or (fail_on_warning and str(issue.get("severity", "error")) == "warning")
    ]
    if blocking_issues or dry_run:
        return _batch_result(
            workspace_root,
            release_version=release_version,
            proposed=proposed,
            issues=issues,
            lint=lint,
            coverage_projection=coverage_projection,
            written=False,
            events=[],
        )
    synced_audit = None
    synced_rows = 0
    if sync_audit and audit_sheet is not None:
        synced_audit, synced_rows = sync_audit_sheet_targets(
            audit_sheet, combined_entries
        )
    for record in proposed:
        save_entry(workspace_root, record)
    updated_release = replace(
        release,
        entry_count=len(existing) + len(proposed),
        versioning=bump_versioning(release.versioning),
    )
    saved_audit = None
    try:
        if synced_audit is not None:
            saved_audit = save_commit_audit_sheet(
                workspace_root,
                synced_audit,
                overwrite=True,
            )
        save_release(
            workspace_root,
            updated_release,
            overwrite=True,
        )
    except LaunchError:
        for record in proposed:
            delete_entry(workspace_root, release.version, record.entry_id)
        if saved_audit is not None and audit_sheet is not None:
            restored_audit = replace(
                audit_sheet,
                versioning=next_commit_audit_versioning(saved_audit, audit_sheet),
            )
            save_commit_audit_sheet(workspace_root, restored_audit, overwrite=True)
        raise
    record_revisions = {
        f"release:{release_version}": updated_release.versioning.revision,
        **{
            f"entry:{release_version}/{record.entry_id}": record.versioning.revision
            for record in proposed
        },
    }
    event_data: dict[str, object] = {
        "entry_ids": [record.entry_id for record in proposed]
    }
    audit_sync_block: dict[str, object] | None = None
    if saved_audit is not None:
        record_revisions["commit_audit_sheet"] = saved_audit.versioning.revision
        event_data["synced_audit_rows"] = synced_rows
        audit_sync_block = {
            "updated_rows": synced_rows,
            "revision": saved_audit.versioning.revision,
        }
    event = append_event(
        workspace_root,
        event=EVENT_ENTRY_BATCH_ADDED,
        release_version=release_version,
        record_revisions=record_revisions,
        data=event_data,
    )
    rebuild_indexes(workspace_root)
    return _batch_result(
        workspace_root,
        release_version=release_version,
        proposed=proposed,
        issues=[],
        lint=lint,
        coverage_projection=coverage_projection,
        written=True,
        events=[event.event_id],
        audit_sync=audit_sync_block,
    )


def list_release_entries(
    workspace_root: Path, release_version: str
) -> list[dict[str, object]]:
    load_release(workspace_root, release_version)
    return [entry.to_dict() for entry in load_entries(workspace_root, release_version)]


def delete_release_entry(
    workspace_root: Path,
    *,
    release_version: str,
    entry_id: str,
    reason: str,
    force_accepted: bool = False,
    detach_audit: bool = False,
    dry_run: bool = False,
) -> dict[str, object]:
    """Delete one entry through the full release lifecycle."""
    if not isinstance(reason, str) or not reason.strip():
        raise LaunchError(
            "Entry deletion requires a non-empty reason.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    release = load_release(workspace_root, release_version)
    entry = _find_entry(workspace_root, release.version, entry_id)
    if entry.status == "accepted" and not force_accepted:
        raise LaunchError(
            f"Accepted entry {entry_id} requires --force-accepted to delete.",
            code=CODE_CONFLICT,
            exit_code=2,
        )
    audit_sheet = load_commit_audit_sheet(workspace_root, release.version)
    affected_rows: list[dict[str, object]] = []
    detached_sheet = audit_sheet
    if audit_sheet is not None:
        targeted = [
            row
            for row in audit_sheet.rows
            if row.target_entry_id == entry_id
            or row.target_entry_key in {entry_id, f"{release.version}/{entry_id}"}
        ]
        affected_rows = [
            {
                "sha": row.sha,
                "target_entry_key": row.target_entry_key,
                "target_entry_id": row.target_entry_id,
                "decision": row.decision,
            }
            for row in targeted
        ]
        if targeted and not detach_audit:
            raise LaunchError(
                f"Entry {entry_id} is targeted by {len(targeted)} audit row(s); "
                "pass --detach-audit to clear those targets.",
                code=CODE_CONFLICT,
                exit_code=2,
                data={"audit_rows": affected_rows},
            )
        if targeted and detach_audit:
            detached_rows = tuple(
                replace(
                    row,
                    target_entry_key=None,
                    target_entry_id=None,
                    decision="needs_review",
                )
                if row in targeted
                else row
                for row in audit_sheet.rows
            )
            detached_sheet = replace(
                audit_sheet,
                rows=detached_rows,
                versioning=bump_versioning(audit_sheet.versioning),
            )
    updated_release = replace(
        release,
        entry_count=max(0, release.entry_count - 1),
        versioning=bump_versioning(release.versioning),
    )
    result: dict[str, object] = {
        "kind": "release_entry_delete",
        "release_version": release.version,
        "entry": entry.to_dict(),
        "release": updated_release.to_dict(),
        "deleted": True,
        "written": not dry_run,
        "dry_run": dry_run,
        "audit_rows": affected_rows,
        "detached_audit": bool(affected_rows and detach_audit),
    }
    if dry_run:
        return result
    delete_entry(workspace_root, release.version, entry.entry_id)
    save_release(workspace_root, updated_release, overwrite=True)
    if detached_sheet is not None and detached_sheet is not audit_sheet:
        save_commit_audit_sheet(workspace_root, detached_sheet, overwrite=True)
    event = append_event(
        workspace_root,
        event=EVENT_ENTRY_DELETED,
        release_version=release.version,
        entry_id=entry.entry_id,
        record_revisions={
            f"release:{release.version}": updated_release.versioning.revision,
            f"entry:{release.version}/{entry.entry_id}": entry.versioning.revision,
            **(
                {f"audit:{release.version}": detached_sheet.versioning.revision}
                if detached_sheet is not None and detached_sheet is not audit_sheet
                else {}
            ),
        },
        data={
            "reason": reason.strip(),
            "force_accepted": bool(force_accepted),
            "detach_audit": bool(detach_audit),
            "affected_audit_rows": [row["sha"] for row in affected_rows],
        },
    )
    rebuild_indexes(workspace_root)
    result["events"] = [event.event_id]
    return result
