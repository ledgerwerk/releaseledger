"""Persistence for release bundles, entries, events, and indexes.

Storage layout per project::

    .releaseledger/ledgers/<ledger_ref>/
        releases/<version>/release.md
        releases/<version>/entries/entry-NNNN.md
        events/events.jsonl
        indexes/releases.json
        indexes/entries.json

All public functions take ``workspace_root`` and resolve the project paths via
the config, mirroring the signatures in the implementation brief.
"""

from __future__ import annotations

import copy
import re
from dataclasses import replace
from pathlib import Path

import ledgercore
import yaml

from releaseledger.domain.audit import (
    CommitAuditSheetRecord,
    audit_sheet_from_dict,
    audit_sheet_to_dict,
)
from releaseledger.domain.entry import (
    ENTRY_FRONT_MATTER_KEY_ORDER,
    ReleaseEntryRecord,
    entry_from_dict,
)
from releaseledger.domain.release import (
    RELEASE_FRONT_MATTER_KEY_ORDER,
    ReleaseRecord,
    parse_release_version_tuple,
    release_from_dict,
)
from releaseledger.domain.versioning import versioning_from_dict
from releaseledger.domain.versioning import (
    RecordVersioning,
    bump_versioning,
    initial_versioning,
)
from releaseledger.errors import (
    CODE_CONFLICT,
    CODE_NOT_FOUND,
    CODE_USAGE_ERROR,
    CODE_VALIDATION_ERROR,
    LaunchError,
)
from releaseledger.storage.paths import ProjectPaths, resolve_project_paths

__all__ = [
    "commit_audit_path",
    "delete_commit_audit_sheet",
    "load_commit_audit_sheet",
    "load_entries",
    "load_release",
    "list_releases",
    "next_commit_audit_versioning",
    "next_entry_id",
    "rebuild_indexes",
    "release_audit_dir",
    "release_dir",
    "release_markdown_path",
    "rename_release_bundle",
    "save_commit_audit_sheet",
    "save_entries_for_release",
    "delete_entry",
    "save_entry",
    "save_release",
    "validate_release_version",
]

_VERSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*")


def validate_release_version(version: str) -> str:
    """Validate a release version string for safe use as a directory name.

    Rejects empty values, surrounding/internal whitespace, path separators,
    control characters, and unsupported characters. The value is used verbatim
    as a bundle directory name, so it must never become a traversal vector.
    """
    if not isinstance(version, str):
        raise LaunchError(
            "Release version must be a string.",
            code=CODE_USAGE_ERROR,
            exit_code=2,
        )
    normalized = version.strip()
    if not normalized:
        raise LaunchError(
            "Release version must not be empty.",
            code=CODE_USAGE_ERROR,
            exit_code=2,
        )
    if normalized != version or any(char.isspace() for char in normalized):
        raise LaunchError(
            "Release version must not contain whitespace.",
            code=CODE_USAGE_ERROR,
            exit_code=2,
        )
    if "/" in normalized or "\\" in normalized:
        raise LaunchError(
            "Release version must not contain path separators.",
            code=CODE_USAGE_ERROR,
            exit_code=2,
        )
    if any(ord(char) < 32 for char in normalized):
        raise LaunchError(
            "Release version must not contain control characters.",
            code=CODE_USAGE_ERROR,
            exit_code=2,
        )
    if _VERSION_RE.fullmatch(normalized) is None:
        raise LaunchError(
            f"Unsupported release version: {version!r}",
            code=CODE_USAGE_ERROR,
            exit_code=2,
        )
    return normalized


def release_dir(paths: ProjectPaths, version: str) -> Path:
    """Return the bundle directory for a validated version."""
    return paths.releases_dir / validate_release_version(version)


def release_markdown_path(paths: ProjectPaths, version: str) -> Path:
    """Return the ``release.md`` path for a validated version."""
    return release_dir(paths, version) / "release.md"


def _entries_dir(paths: ProjectPaths, version: str) -> Path:
    return release_dir(paths, version) / "entries"


def _resolve(workspace_root: Path) -> ProjectPaths:
    return resolve_project_paths(workspace_root)


def ensure_release_bundle(paths: ProjectPaths, version: str) -> Path:
    """Create the release bundle directory and entries subdirectory."""
    bundle = release_dir(paths, version)
    ledgercore.ensure_dir(bundle)
    ledgercore.ensure_dir(_entries_dir(paths, version))
    return bundle


def save_release(
    workspace_root: Path,
    release: ReleaseRecord,
    *,
    overwrite: bool = False,
) -> ReleaseRecord:
    """Persist a release record as ``release.md`` (note becomes the body)."""
    paths = _resolve(workspace_root)
    version = validate_release_version(release.version)
    target = release_markdown_path(paths, version)
    if target.is_file() and not overwrite:
        raise LaunchError(
            f"Release version already exists: {version}",
            code=CODE_CONFLICT,
            exit_code=2,
            remediation=[f"Run `releaseledger release show {version}`."],
        )
    old_data: dict[str, object] | None = None
    old_body: str | None = None
    if target.is_file():
        old_front_matter, old_body = ledgercore.read_front_matter_document(target)
        old_data = dict(old_front_matter)
    new_data = release.to_front_matter()
    _validate_revision_transition(
        old_data=old_data,
        new_data=new_data,
        old_body=old_body,
        new_body=release.note,
        label=f"Release {version}",
    )
    ensure_release_bundle(paths, version)
    ledgercore.write_front_matter_document(
        target,
        new_data,
        body=release.note or "",
        body_mode="ensure-single-final-newline",
        key_order=RELEASE_FRONT_MATTER_KEY_ORDER,
    )
    return release


def load_release(workspace_root: Path, version: str) -> ReleaseRecord:
    """Load and validate a release record for ``version``."""
    paths = _resolve(workspace_root)
    safe_version = validate_release_version(version)
    target = release_markdown_path(paths, safe_version)
    if not target.is_file():
        raise LaunchError(
            f"Release not found: {version}",
            code=CODE_NOT_FOUND,
            exit_code=2,
            remediation=["Run `releaseledger release list` to see releases."],
        )
    front_matter, body = ledgercore.read_front_matter_document(target)
    data: dict[str, object] = dict(front_matter)
    data["note"] = body if body else None
    return release_from_dict(data)


def list_releases(workspace_root: Path) -> list[ReleaseRecord]:
    """Return all releases sorted deterministically.

    Sort key: released_at, then semantic version, then raw version.
    """
    paths = _resolve(workspace_root)
    if not paths.releases_dir.is_dir():
        return []
    records: list[ReleaseRecord] = []
    for child in sorted(paths.releases_dir.iterdir(), key=lambda p: p.name):
        if not child.is_dir():
            continue
        target = child / "release.md"
        if not target.is_file():
            continue
        try:
            front_matter, body = ledgercore.read_front_matter_document(target)
        except ledgercore.FrontMatterError:
            continue
        data: dict[str, object] = dict(front_matter)
        data["note"] = body if body else None
        try:
            records.append(release_from_dict(data))
        except LaunchError:
            continue
    records.sort(key=_release_sort_key)
    return records


def _release_sort_key(record: ReleaseRecord) -> tuple[object, ...]:
    released_at = record.released_at or ""
    semver = parse_release_version_tuple(record.version)
    # Parseable semantic versions sort by (0, major, minor, patch) so same-date
    # releases order naturally. Non-parseable versions sort after real semver.
    if semver is not None:
        semver_component: tuple[object, ...] = (0, *semver)
    else:
        semver_component = (1, record.version)
    return (released_at, semver_component, record.version)


def next_entry_id(workspace_root: Path, release_version: str) -> str:
    """Return the next ``entry-NNNN`` id for a release."""
    entries = load_entries(workspace_root, release_version)
    existing = [entry.entry_id for entry in entries]
    return ledgercore.next_prefixed_id("entry", existing)


def save_entry(workspace_root: Path, entry: ReleaseEntryRecord) -> ReleaseEntryRecord:
    """Persist an entry record as ``entry-NNNN.md`` inside its release bundle."""
    paths = _resolve(workspace_root)
    validate_release_version(entry.release_version)
    bundle = release_dir(paths, entry.release_version)
    if not bundle.is_dir():
        raise LaunchError(
            f"Release not found: {entry.release_version}",
            code=CODE_NOT_FOUND,
            exit_code=2,
        )
    entries_dir = _entries_dir(paths, entry.release_version)
    ledgercore.ensure_dir(entries_dir)
    target = entries_dir / f"{entry.entry_id}.md"
    old_data: dict[str, object] | None = None
    old_body: str | None = None
    if target.is_file():
        old_front_matter, old_body = ledgercore.read_front_matter_document(target)
        old_data = dict(old_front_matter)
    new_data = entry.to_front_matter()
    _validate_revision_transition(
        old_data=old_data,
        new_data=new_data,
        old_body=old_body,
        new_body=entry.body,
        label=f"Entry {entry.release_version}/{entry.entry_id}",
    )
    ledgercore.write_front_matter_document(
        target,
        new_data,
        body=entry.body or "",
        body_mode="ensure-single-final-newline",
        key_order=ENTRY_FRONT_MATTER_KEY_ORDER,
    )
    return entry


def delete_entry(workspace_root: Path, release_version: str, entry_id: str) -> None:
    """Delete an entry file if it exists; used to roll back partial writes.

    Safe to call when no file is present. Does not touch the release record or
    indexes; callers perform rollback immediately after a failed release save,
    before the entry was counted or indexed.
    """
    paths = _resolve(workspace_root)
    safe_version = validate_release_version(release_version)
    target = _entries_dir(paths, safe_version) / f"{entry_id}.md"
    if target.is_file():
        target.unlink()


def load_entries(
    workspace_root: Path, release_version: str
) -> list[ReleaseEntryRecord]:
    """Return all entries for a release, sorted by order then entry_id."""
    paths = _resolve(workspace_root)
    safe_version = validate_release_version(release_version)
    entries_dir = _entries_dir(paths, safe_version)
    if not entries_dir.is_dir():
        return []
    records: list[ReleaseEntryRecord] = []
    for child in sorted(entries_dir.glob("entry-*.md"), key=lambda p: p.name):
        try:
            front_matter, body = ledgercore.read_front_matter_document(child)
        except ledgercore.FrontMatterError:
            continue
        data: dict[str, object] = dict(front_matter)
        data["body"] = body if body else None
        try:
            records.append(entry_from_dict(data))
        except LaunchError:
            continue
    records.sort(key=_entry_sort_key)
    return records


def _entry_sort_key(entry: ReleaseEntryRecord) -> tuple[object, ...]:
    # Entries without an explicit order sort after ordered ones.
    order: object = entry.order if entry.order is not None else float("inf")
    return (order, entry.entry_id)


def _release_index_row(record: ReleaseRecord) -> dict[str, object]:
    return {
        "version": record.version,
        "status": record.status,
        "title": record.title,
        "released_at": record.released_at,
        "record_revision": record.versioning.revision,
        "previous_version": record.previous_version,
        "changelog_file": record.changelog_file,
        "boundary_ref": record.boundary_ref,
        "source_refs": list(record.source_refs),
        "source_count": record.source_count,
        "entry_count": record.entry_count,
        "artifact_count": record.artifact_count,
    }


def _entry_index_row(entry: ReleaseEntryRecord) -> dict[str, object]:
    row: dict[str, object] = {
        "entry_id": entry.entry_id,
        "release_version": entry.release_version,
        "kind": entry.kind,
        "summary": entry.summary,
        "order": entry.order,
        "internal": entry.internal,
        "status": entry.status,
        "audience": entry.audience,
        "scopes": list(entry.scopes),
        "source_refs": list(entry.source_refs),
        "breaking": entry.breaking,
        "record_revision": entry.versioning.revision,
    }
    if entry.sources:
        row["sources"] = list(entry.sources)
    return row


def rebuild_indexes(workspace_root: Path) -> None:
    """Rebuild ``releases.json`` and ``entries.json`` from on-disk records."""
    paths = _resolve(workspace_root)
    releases = list_releases(workspace_root)
    release_rows = [_release_index_row(record) for record in releases]

    entry_rows: list[dict[str, object]] = []
    for record in releases:
        for entry in load_entries(workspace_root, record.version):
            entry_rows.append(_entry_index_row(entry))
    entry_rows.sort(key=lambda row: (row.get("order"), row.get("entry_id")))

    ledgercore.ensure_dir(paths.indexes_dir)
    ledgercore.write_json(paths.releases_index_path, release_rows)
    ledgercore.write_json(paths.entries_index_path, entry_rows)


def save_entries_for_release(
    workspace_root: Path,
    release_version: str,
    entries: list[ReleaseEntryRecord],
) -> None:
    """Replace the entries for ``release_version`` with ``entries``.

    Writes each entry with its ``release_version`` rewritten to the supplied
    version and removes any stale entry files left from the prior bundle. Used
    by :func:`rename_release_bundle` to move entries across versions while
    preserving entry ids and order.
    """
    paths = _resolve(workspace_root)
    validate_release_version(release_version)
    entries_dir = _entries_dir(paths, release_version)
    ledgercore.ensure_dir(entries_dir)
    wanted = {f"{entry.entry_id}.md" for entry in entries}
    for child in entries_dir.glob("entry-*.md"):
        if child.name not in wanted:
            try:
                child.unlink()
            except OSError:
                # Best-effort cleanup; stale files are overwritten below.
                pass
    for entry in entries:
        rewritten = replace(
            entry,
            release_version=release_version,
            versioning=replace(
                entry.versioning, revision=entry.versioning.revision + 1
            ),
        )
        save_entry(workspace_root, rewritten)


def rename_release_bundle(
    workspace_root: Path,
    old_version: str,
    new_record: ReleaseRecord,
) -> ReleaseRecord:
    """Move the release bundle from ``old_version`` to ``new_record.version``.

    Persists ``new_record`` (with its rewritten front matter) under the new
    version directory, rewrites every entry's ``release_version`` front matter
    to the new version (preserving entry ids and order), and removes the old
    bundle directory. Returns the persisted ``new_record``.
    """
    paths = _resolve(workspace_root)
    validate_release_version(old_version)
    validate_release_version(new_record.version)
    old_bundle = release_dir(paths, old_version)
    if not old_bundle.is_dir():
        raise LaunchError(
            f"Release not found: {old_version}",
            code=CODE_NOT_FOUND,
            exit_code=2,
        )
    if old_version == new_record.version:
        save_release(workspace_root, new_record, overwrite=True)
        return new_record
    # Load entries before touching the filesystem so a rewrite failure leaves
    # the original bundle intact.
    entries = load_entries(workspace_root, old_version)
    new_bundle = release_dir(paths, new_record.version)
    if new_bundle.exists():
        raise LaunchError(
            f"Release version already exists: {new_record.version}",
            code=CODE_CONFLICT,
            exit_code=2,
        )
    old_bundle.rename(new_bundle)
    save_release(workspace_root, new_record, overwrite=True)
    save_entries_for_release(workspace_root, new_record.version, entries)
    return new_record


def _strip_revision(data: dict[str, object]) -> dict[str, object]:
    clone = copy.deepcopy(data)
    versioning = clone.get("versioning")
    if isinstance(versioning, dict):
        versioning.pop("revision", None)
    return clone


def _validate_revision_transition(
    *,
    old_data: dict[str, object] | None,
    new_data: dict[str, object],
    old_body: str | None,
    new_body: str | None,
    label: str,
) -> None:
    new_revision = versioning_from_dict(new_data.get("versioning")).revision
    if old_data is None:
        if new_revision != 1:
            raise LaunchError(
                f"{label} revision must start at 1.",
                code=CODE_VALIDATION_ERROR,
                exit_code=2,
            )
        return
    old_revision = versioning_from_dict(old_data.get("versioning")).revision
    changed = _strip_revision(old_data) != _strip_revision(new_data) or (
        old_body or ""
    ) != (new_body or "")
    expected = old_revision + 1 if changed else old_revision
    if new_revision != expected:
        raise LaunchError(
            f"{label} revision must be {expected}; got {new_revision}.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )


# ---------------------------------------------------------------------------
# Commit audit sheet persistence
# ---------------------------------------------------------------------------


def release_audit_dir(paths: ProjectPaths, version: str) -> Path:
    """Return the per-release ``audit/`` directory."""
    return release_dir(paths, version) / "audit"


def commit_audit_path(paths: ProjectPaths, version: str) -> Path:
    """Return the canonical ``commit-audit.yaml`` path for ``version``."""
    return release_audit_dir(paths, version) / "commit-audit.yaml"


def _audit_label(version: str) -> str:
    return f"Commit audit sheet {version}"


def load_commit_audit_sheet(
    workspace_root: Path, version: str
) -> CommitAuditSheetRecord | None:
    """Load the commit audit sheet for ``version``; return None if absent."""
    paths = _resolve(workspace_root)
    safe_version = validate_release_version(version)
    target = commit_audit_path(paths, safe_version)
    if not target.is_file():
        return None
    try:
        data = ledgercore.load_yaml_object(target, label=_audit_label(version))
    except ledgercore.YamlStoreError as exc:
        raise LaunchError(
            f"Failed to read commit audit sheet for {version}: {exc}",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        ) from exc
    if not isinstance(data, dict):
        raise LaunchError(
            f"Commit audit sheet for {version} must be a mapping.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    return audit_sheet_from_dict(dict(data))


def save_commit_audit_sheet(
    workspace_root: Path,
    sheet: CommitAuditSheetRecord,
    *,
    overwrite: bool = True,
) -> CommitAuditSheetRecord:
    """Persist ``sheet`` as ``commit-audit.yaml`` under its release audit dir.

    Validates the revision transition like release/entry records: the first
    write must start at revision 1, and subsequent writes must advance by
    exactly one when content changed (or stay equal when unchanged).
    """
    paths = _resolve(workspace_root)
    version = validate_release_version(sheet.release_version)
    if version != sheet.release_version:
        raise LaunchError(
            f"Audit sheet release_version {sheet.release_version!r} must match "
            "a valid release version.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    target = commit_audit_path(paths, version)
    old_data: dict[str, object] | None = None
    if target.is_file():
        if not overwrite:
            raise LaunchError(
                f"Commit audit sheet already exists for {version}.",
                code=CODE_CONFLICT,
                exit_code=2,
                remediation=["Pass --overwrite to replace the sheet."],
            )
        loaded = ledgercore.load_yaml_object(target, label=_audit_label(version))
        if isinstance(loaded, dict):
            old_data = dict(loaded)
    new_data = audit_sheet_to_dict(sheet)
    _validate_revision_transition(
        old_data=old_data,
        new_data=new_data,
        old_body=None,
        new_body=None,
        label=_audit_label(version),
    )
    audit_dir = release_audit_dir(paths, version)
    ledgercore.ensure_dir(audit_dir)
    text = yaml.safe_dump(
        new_data,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )
    try:
        ledgercore.atomic_write_text(target, text)
    except (ledgercore.AtomicWriteError, OSError) as exc:
        raise LaunchError(
            f"Failed to write commit audit sheet for {version}: {exc}",
            code=CODE_USAGE_ERROR,
            exit_code=2,
        ) from exc
    return sheet


def next_commit_audit_versioning(
    existing: CommitAuditSheetRecord | None,
    candidate: CommitAuditSheetRecord,
) -> RecordVersioning:
    """Return the correct next versioning for an audit sheet candidate."""
    if existing is None:
        return initial_versioning()
    old_data = audit_sheet_to_dict(existing)
    new_data = audit_sheet_to_dict(candidate)
    changed = _strip_revision(old_data) != _strip_revision(new_data)
    return bump_versioning(existing.versioning) if changed else existing.versioning


def delete_commit_audit_sheet(workspace_root: Path, version: str) -> bool:
    """Delete the commit audit sheet for ``version``. Return True if removed."""
    paths = _resolve(workspace_root)
    safe_version = validate_release_version(version)
    target = commit_audit_path(paths, safe_version)
    if target.is_file():
        target.unlink()
        return True
    return False
