"""Release service: create, tag, finalize, list, and show releases.

Services return plain dict payloads and raise :class:`LaunchError`. They never
print or call ``typer.Exit``. Every mutation persists the record, appends one
event, and rebuilds the indexes.
"""

from __future__ import annotations

import datetime
import json
import re
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import ledgercore
import yaml

from releaseledger.domain.event import (
    EVENT_RELEASE_CANCELED,
    EVENT_RELEASE_CHAIN_REPAIRED,
    EVENT_RELEASE_CREATED,
    EVENT_RELEASE_FINALIZED,
    EVENT_RELEASE_RENAMED,
    EVENT_RELEASE_TAGGED,
    EVENT_RELEASE_UPDATED,
)
from releaseledger.domain.release import (
    ReleaseRecord,
    parse_release_version_tuple,
)
from releaseledger.domain.source_ref import normalize_source_ref
from releaseledger.domain.states import RELEASE_STATUSES
from releaseledger.domain.versioning import bump_versioning
from releaseledger.errors import (
    CODE_CONFLICT,
    CODE_USAGE_ERROR,
    CODE_VALIDATION_ERROR,
    LaunchError,
)
from releaseledger.services.audit import (
    create_commit_audit_sheet,
    refresh_commit_audit_sheet,
    render_commit_audit_sheet,
)
from releaseledger.services.changelog_build import (
    find_release_section,
    remove_release_section,
    rename_release_section,
)
from releaseledger.services.events import append_event
from releaseledger.services.git_sources import (
    GIT_DEFAULT_HEAD,
    build_git_range_summary,
    generate_git_scaffold_batch,
    is_root_base_ref,
    release_snapshot_drift_report,
    resolve_base_sha,
    resolve_git_ref,
    resolve_release_snapshot,
)
from releaseledger.storage.paths import resolve_project_paths
from releaseledger.storage.store import (
    list_releases,
    load_commit_audit_sheet,
    load_entries,
    load_release,
    rebuild_indexes,
    release_markdown_path,
    rename_release_bundle,
    save_release,
    validate_release_version,
)

__all__ = [
    "cancel_release",
    "reconcile_releases",
    "check_release_chain",
    "create_release",
    "finalize_release",
    "list_release_records",
    "rename_release",
    "repair_release_chain",
    "prepare_release",
    "show_release",
    "tag_release",
    "update_release",
]

# Sentinel for clearable optional fields on ``update_release``: distinguishes
# "not supplied" (``UNSET``) from "explicitly clear to None".
UNSET: object = object()

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_FINALIZABLE_STATUSES = frozenset({"planned", "draft", "candidate"})

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_FINALIZABLE_STATUSES = frozenset({"planned", "draft", "candidate"})


def _today() -> str:
    return datetime.date.today().isoformat()


def _validate_date(value: str, field_name: str) -> str:
    if not _DATE_RE.fullmatch(value):
        raise LaunchError(
            f"{field_name} must be a YYYY-MM-DD date, got {value!r}.",
            code=CODE_USAGE_ERROR,
            exit_code=2,
        )
    # Validate real calendar date
    try:
        datetime.date.fromisoformat(value)
    except ValueError as exc:
        raise LaunchError(
            f"{field_name} must be a valid calendar date, got {value!r}.",
            code=CODE_USAGE_ERROR,
            exit_code=2,
        ) from exc
    return value


def _predecessor_key(
    version: str | None,
    released_at: str | None,
) -> tuple[str, tuple[int, int, int] | None, str]:
    """Return the legacy deterministic key used for display ordering."""
    return (
        released_at or "",
        parse_release_version_tuple(version or ""),
        version or "",
    )


def _compare_release_order(
    predecessor_version: str,
    predecessor_released_at: str | None,
    record_version: str,
    record_released_at: str | None,
) -> tuple[int | None, str]:
    """Compare two release records using a meaningful shared ordering basis."""
    if predecessor_released_at and record_released_at:
        if predecessor_released_at != record_released_at:
            return (
                1 if predecessor_released_at > record_released_at else -1,
                "release_date",
            )
        predecessor_semver = parse_release_version_tuple(predecessor_version)
        record_semver = parse_release_version_tuple(record_version)
        if predecessor_semver is not None and record_semver is not None:
            if predecessor_semver == record_semver:
                return 0, "release_date+semantic_version"
            return (
                1 if predecessor_semver > record_semver else -1,
                "release_date+semantic_version",
            )
        return None, "ambiguous_previous_order"
    predecessor_semver = parse_release_version_tuple(predecessor_version)
    record_semver = parse_release_version_tuple(record_version)
    if predecessor_semver is not None and record_semver is not None:
        if predecessor_semver == record_semver:
            return 0, "semantic_version"
        return (
            1 if predecessor_semver > record_semver else -1,
            "semantic_version",
        )
    return None, "ambiguous_previous_order"


def _is_strictly_newer(
    candidate_key: tuple[str, tuple[int, int, int] | None, str],
    reference_key: tuple[str, tuple[int, int, int] | None, str],
) -> bool:
    """Compatibility helper for callers using the old tuple key."""
    cand_date, cand_semver, cand_ver = candidate_key
    ref_date, ref_semver, ref_ver = reference_key
    if cand_date != ref_date:
        return cand_date > ref_date
    if cand_semver is not None and ref_semver is not None:
        return cand_semver > ref_semver
    return bool(cand_semver is None and ref_semver is None and cand_ver > ref_ver)


def _infer_previous_version(
    workspace_root: Path,
    *,
    candidate_version: str | None = None,
    candidate_released_at: str | None = None,
) -> tuple[str | None, list[str]]:
    """Infer the previous released release version for a new/edited release.

    Returns ``(previous_version_or_None, warnings)``. Excludes canceled
    releases and never infers a predecessor that is strictly newer than the
    candidate, so historical backfills (e.g. adding ``v0.1.0`` after
    ``v0.4.3``) no longer infer a future predecessor. Emits a warning when
    the ordering is ambiguous (same date, non-parseable versions).
    """
    warnings: list[str] = []
    released = [r for r in list_releases(workspace_root) if r.status == "released"]
    if not released:
        return None, warnings
    eligible: list[ReleaseRecord] = []
    ambiguity_seen = False
    for record in released:
        if candidate_version is not None and record.version == candidate_version:
            continue
        comparison, _basis = _compare_release_order(
            record.version,
            record.released_at,
            candidate_version or "",
            candidate_released_at,
        )
        if comparison is None:
            ambiguity_seen = True
            continue
        if comparison > 0:
            continue
        eligible.append(record)
    if not eligible:
        if ambiguity_seen:
            warnings.append(
                "Previous-version inference is ambiguous; pass --previous to "
                "set it explicitly."
            )
        return None, warnings
    eligible.sort(
        key=lambda r: (
            parse_release_version_tuple(r.version) is None,
            parse_release_version_tuple(r.version) or (),
            r.version,
        )
    )
    return eligible[-1].version, warnings


def _validate_source_metadata(
    *,
    boundary_ref: str | None,
    source_refs: tuple[str, ...],
    source_count: int | None,
) -> tuple[str | None, tuple[str, ...], int | None]:
    boundary: str | None = None
    if boundary_ref is not None:
        try:
            boundary = normalize_source_ref(boundary_ref)
        except LaunchError as exc:
            raise LaunchError(
                f"Invalid release boundary ref {boundary_ref!r}: {exc.message}",
                code=CODE_VALIDATION_ERROR,
                exit_code=2,
            ) from exc
    refs: list[str] = []
    for ref in source_refs:
        try:
            canonical = normalize_source_ref(ref)
        except LaunchError as exc:
            raise LaunchError(
                f"Invalid release source ref {ref!r}: {exc.message}",
                code=CODE_VALIDATION_ERROR,
                exit_code=2,
            ) from exc
        if canonical not in refs:
            refs.append(canonical)
    refs_tuple = tuple(refs)
    if source_count is not None and source_count < 0:
        raise LaunchError(
            "--source-count must be zero or greater.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    return boundary, refs_tuple, source_count


def _resolve_git_range(
    workspace_root: Path,
    *,
    existing: ReleaseRecord,
    git_base_ref: str | None,
    git_head_ref: str | None,
    clear_git_range: bool,
) -> dict[str, object]:
    """Resolve --git-base/--git-head into stored git metadata.

    Returns the six git_* fields (refs/SHAs/range/count). When neither ref is
    supplied and clear_git_range is False, returns the existing values unchanged
    (so absence does not wipe stored git metadata). Per design §7.2, resolving
    the range does NOT auto-add source refs.

    Raises LaunchError when the workspace is not a git worktree or a ref cannot
    be resolved (git is optional overall, but --git-base/--git-head are an
    explicit git operation).
    """
    keys = (
        "git_base_ref",
        "git_base_sha",
        "git_head_ref",
        "git_head_sha",
        "git_range",
        "git_commit_count",
    )
    if clear_git_range:
        return {key: None for key in keys}
    base_supplied = git_base_ref is not None and git_base_ref is not UNSET
    head_supplied = git_head_ref is not None and git_head_ref is not UNSET
    if not base_supplied and not head_supplied:
        return {key: getattr(existing, key) for key in keys}
    base = git_base_ref if base_supplied else existing.git_base_ref
    head = git_head_ref if head_supplied else existing.git_head_ref
    if base is None:
        raise LaunchError(
            "--git-head requires --git-base (or a previously stored git base).",
            code=CODE_USAGE_ERROR,
            exit_code=2,
            remediation=["Pass --git-base to set the release range base."],
        )
    if head is None:
        head = GIT_DEFAULT_HEAD
    base_sha = resolve_base_sha(workspace_root, str(base))
    head_sha = resolve_git_ref(workspace_root, str(head))
    summary = build_git_range_summary(
        workspace_root, base_ref=str(base), head_ref=str(head)
    )
    base_ref_display = ":root" if is_root_base_ref(str(base)) else str(base)
    return {
        "git_base_ref": base_ref_display,
        "git_base_sha": base_sha,
        "git_head_ref": str(head),
        "git_head_sha": head_sha,
        "git_range": summary["range"],
        "git_commit_count": summary["commit_count"],
    }


def _release_payload(
    workspace_root: Path,
    record: ReleaseRecord,
    event_id: str | None = None,
) -> dict[str, object]:
    paths = resolve_project_paths(workspace_root)
    payload: dict[str, object] = {
        "kind": "release",
        "ledger_ref": paths.ledger_ref,
        "release": record.to_dict(),
    }
    if event_id is not None:
        payload["events"] = [event_id]
    return payload


def _persist_new_release(
    workspace_root: Path,
    record: ReleaseRecord,
    *,
    event_name: str,
    warnings: list[str] | None = None,
) -> dict[str, object]:
    paths = resolve_project_paths(workspace_root)
    if release_markdown_path(paths, record.version).is_file():
        raise LaunchError(
            f"Release version already exists: {record.version}",
            code=CODE_CONFLICT,
            exit_code=2,
            remediation=[f"Run `releaseledger release show {record.version}`."],
        )
    save_release(workspace_root, record, overwrite=False)
    event = append_event(
        workspace_root,
        event=event_name,
        release_version=record.version,
        record_revisions={f"release:{record.version}": record.versioning.revision},
        data={"status": record.status},
    )
    rebuild_indexes(workspace_root)
    payload = _release_payload(workspace_root, record, event.event_id)
    if warnings:
        payload["warnings"] = list(warnings)
    return payload


def create_release(
    workspace_root: Path,
    *,
    version: str,
    title: str | None = None,
    status: str = "planned",
    note: str | None = None,
    previous_version: str | None = None,
    changelog_file: str | None = None,
    released_at: str | None = None,
    boundary_ref: str | None = None,
    source_refs: tuple[str, ...] = (),
    source_count: int | None = None,
) -> dict[str, object]:
    """Create a new release record. Fails if the version already exists."""
    validate_release_version(version)
    if status not in RELEASE_STATUSES:
        raise LaunchError(
            f"Unsupported release status: {status!r}",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    if released_at is not None:
        _validate_date(released_at, "--released-at")
    warnings: list[str] = []
    if previous_version is None:
        previous_version, warnings = _infer_previous_version(
            workspace_root,
            candidate_version=version,
            candidate_released_at=released_at,
        )
    boundary_ref, source_refs, source_count = _validate_source_metadata(
        boundary_ref=boundary_ref,
        source_refs=source_refs,
        source_count=source_count,
    )
    record = ReleaseRecord(
        version=version,
        status=status,
        title=title,
        released_at=released_at,
        previous_version=previous_version,
        note=note,
        changelog_file=changelog_file,
        boundary_ref=boundary_ref,
        source_refs=source_refs,
        source_count=source_count,
    )
    return _persist_new_release(
        workspace_root,
        record,
        event_name=EVENT_RELEASE_CREATED,
        warnings=warnings,
    )


def tag_release(
    workspace_root: Path,
    *,
    version: str,
    note: str | None = None,
    previous_version: str | None = None,
    changelog_file: str | None = None,
    released_at: str | None = None,
    boundary_ref: str | None = None,
    source_refs: tuple[str, ...] = (),
    source_count: int | None = None,
) -> dict[str, object]:
    """Create a release with status 'released' (released_at defaults to today)."""
    validate_release_version(version)
    if released_at is not None:
        _validate_date(released_at, "--released-at")
    else:
        released_at = _today()
    warnings: list[str] = []
    if previous_version is None:
        previous_version, warnings = _infer_previous_version(
            workspace_root,
            candidate_version=version,
            candidate_released_at=released_at,
        )
    boundary_ref, source_refs, source_count = _validate_source_metadata(
        boundary_ref=boundary_ref,
        source_refs=source_refs,
        source_count=source_count,
    )
    record = ReleaseRecord(
        version=version,
        status="released",
        title=f"Release {version}",
        released_at=released_at,
        previous_version=previous_version,
        note=note,
        changelog_file=changelog_file,
        boundary_ref=boundary_ref,
        source_refs=source_refs,
        source_count=source_count,
    )
    return _persist_new_release(
        workspace_root,
        record,
        event_name=EVENT_RELEASE_TAGGED,
        warnings=warnings,
    )


def finalize_release(
    workspace_root: Path,
    *,
    version: str,
    released_at: str | None = None,
    changelog_file: str | None = None,
) -> dict[str, object]:
    """Transition an existing planned/draft/candidate release to 'released'."""
    validate_release_version(version)
    existing = load_release(workspace_root, version)
    if existing.status not in _FINALIZABLE_STATUSES:
        raise LaunchError(
            f"Release {version} is already {existing.status!r}"
            " and cannot be finalized.",
            code=CODE_CONFLICT,
            exit_code=2,
        )
    if released_at is not None:
        _validate_date(released_at, "--released-at")
    else:
        released_at = _today()
    updated = replace(
        existing,
        status="released",
        released_at=released_at,
        changelog_file=changelog_file or existing.changelog_file,
        versioning=bump_versioning(existing.versioning),
    )
    save_release(workspace_root, updated, overwrite=True)
    event = append_event(
        workspace_root,
        event=EVENT_RELEASE_FINALIZED,
        release_version=version,
        record_revisions={f"release:{version}": updated.versioning.revision},
        data={"released_at": released_at},
    )
    rebuild_indexes(workspace_root)
    return _release_payload(workspace_root, updated, event.event_id)


def _resolve_optional_field(
    field_name: str,
    supplied: object,
    existing: object,
    *,
    clear: bool = False,
) -> Any:
    """Resolve a clearable optional field supplied through the update API.

    ``UNSET`` means "not supplied" (keep existing), ``clear=True" means clear
    to ``None`` (or empty for collections), and any other value is the new value.
    Supplying both a new value and ``clear=True`` is a usage error.
    """
    if clear:
        if supplied is not UNSET:
            raise LaunchError(
                f"--{field_name} conflicts with its clear flag; supply one.",
                code=CODE_USAGE_ERROR,
                exit_code=2,
            )
        return None
    if supplied is UNSET:
        return existing
    return supplied


def update_release(
    workspace_root: Path,
    *,
    version: str,
    title: str | None = None,
    status: str | None = None,
    note: str | None = None,
    previous_version: Any = UNSET,
    changelog_file: Any = UNSET,
    boundary_ref: Any = UNSET,
    source_refs: Any = UNSET,
    source_count: Any = UNSET,
    released_at: Any = UNSET,
    clear_previous: bool = False,
    clear_changelog_file: bool = False,
    clear_boundary_ref: bool = False,
    clear_source_refs: bool = False,
    clear_source_count: bool = False,
    clear_released_at: bool = False,
    git_base_ref: Any = UNSET,
    git_head_ref: Any = UNSET,
    clear_git_range: bool = False,
    force: bool = False,
) -> dict[str, object]:
    """Update explicitly supplied release metadata.

    Clearable optional fields use the ``UNSET`` sentinel to distinguish
    "not supplied" from "explicitly clear to None". Each ``--clear-*`` flag
    conflicts with its matching setter option. Clearing ``released_at`` on a
    release whose effective status is ``released`` is rejected unless
    ``force=True``.
    """
    existing = load_release(workspace_root, version)
    if status is not None and status not in RELEASE_STATUSES:
        raise LaunchError(
            f"Unsupported release status: {status!r}",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    effective_status = status if status is not None else existing.status
    if clear_released_at and effective_status == "released" and not force:
        raise LaunchError(
            "Clearing released_at on a released release requires --force.",
            code=CODE_USAGE_ERROR,
            exit_code=2,
        )
    # Resolve each clearable optional field against the UNSET sentinel.
    resolved_previous = _resolve_optional_field(
        "previous",
        previous_version,
        existing.previous_version,
        clear=clear_previous,
    )
    if resolved_previous is not None:
        resolved_previous = validate_release_version(str(resolved_previous))
    resolved_changelog_file = _resolve_optional_field(
        "changelog-file",
        changelog_file,
        existing.changelog_file,
        clear=clear_changelog_file,
    )
    resolved_boundary_raw = _resolve_optional_field(
        "boundary-ref",
        boundary_ref,
        existing.boundary_ref,
        clear=clear_boundary_ref,
    )
    resolved_source_refs_raw = _resolve_optional_field(
        "source-refs",
        source_refs,
        existing.source_refs,
        clear=clear_source_refs,
    )
    resolved_source_count = _resolve_optional_field(
        "source-count",
        source_count,
        existing.source_count,
        clear=clear_source_count,
    )
    resolved_released_at = _resolve_optional_field(
        "released-at",
        released_at,
        existing.released_at,
        clear=clear_released_at,
    )
    if resolved_released_at is not None:
        _validate_date(str(resolved_released_at), "--released-at")
    boundary, refs, count = _validate_source_metadata(
        boundary_ref=resolved_boundary_raw,
        source_refs=(
            resolved_source_refs_raw
            if isinstance(resolved_source_refs_raw, tuple)
            else existing.source_refs
        ),
        source_count=resolved_source_count,
    )
    if clear_source_refs:
        refs = ()
    # Git range resolution: when --git-base/--git-head are supplied, resolve
    # them to full SHAs, store the range metadata, and count commits. Does NOT
    # auto-add source refs (the user/agent curates entries from `git import`).
    resolved_git = _resolve_git_range(
        workspace_root,
        existing=existing,
        git_base_ref=git_base_ref,
        git_head_ref=git_head_ref,
        clear_git_range=clear_git_range,
    )
    values: dict[str, object] = {
        "title": title if title is not None else existing.title,
        "status": status if status is not None else existing.status,
        "note": note if note is not None else existing.note,
        "previous_version": resolved_previous,
        "changelog_file": resolved_changelog_file,
        "released_at": resolved_released_at,
        "boundary_ref": boundary,
        "source_refs": refs,
        "source_count": count,
        **resolved_git,
    }
    if all(getattr(existing, key) == value for key, value in values.items()):
        raise LaunchError(
            "Release update did not change any fields.",
            code=CODE_CONFLICT,
            exit_code=2,
        )
    updated = replace(
        existing,
        title=title if title is not None else existing.title,
        status=status if status is not None else existing.status,
        note=note if note is not None else existing.note,
        previous_version=resolved_previous,
        changelog_file=resolved_changelog_file,
        released_at=resolved_released_at,
        boundary_ref=boundary,
        source_refs=refs,
        source_count=count,
        git_base_ref=cast(str | None, resolved_git["git_base_ref"]),
        git_base_sha=cast(str | None, resolved_git["git_base_sha"]),
        git_head_ref=cast(str | None, resolved_git["git_head_ref"]),
        git_head_sha=cast(str | None, resolved_git["git_head_sha"]),
        git_range=cast(str | None, resolved_git["git_range"]),
        git_commit_count=cast(int | None, resolved_git["git_commit_count"]),
        versioning=bump_versioning(existing.versioning),
    )
    save_release(workspace_root, updated, overwrite=True)
    event = append_event(
        workspace_root,
        event=EVENT_RELEASE_UPDATED,
        release_version=version,
        record_revisions={f"release:{version}": updated.versioning.revision},
        data={
            "fields": sorted(
                key for key, value in values.items() if getattr(existing, key) != value
            )
        },
    )
    rebuild_indexes(workspace_root)
    return _release_payload(workspace_root, updated, event.event_id)


def list_release_records(workspace_root: Path) -> list[dict[str, object]]:
    """Return release dicts sorted deterministically."""
    return [record.to_dict() for record in list_releases(workspace_root)]


def show_release(workspace_root: Path, version: str) -> dict[str, object]:
    """Return a release with its entries for display."""
    record = load_release(workspace_root, version)
    entries = [entry.to_dict() for entry in load_entries(workspace_root, version)]
    payload = _release_payload(workspace_root, record)
    payload["entries"] = entries
    payload["entry_count"] = len(entries)
    drift = release_snapshot_drift_report(workspace_root, record)
    if drift is not None:
        payload["snapshot_drift"] = drift
    return payload


def prepare_release(
    workspace_root: Path,
    *,
    version: str,
    previous_version: str | None = None,
    released_at: str | None = None,
    git_base_ref: str | None = None,
    git_head_ref: str | None = None,
    output_dir: Path,
) -> dict[str, object]:
    """Create/update a planned release snapshot and export working artifacts."""
    workspace_root = workspace_root.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    try:
        load_release(workspace_root, version)
        release_exists = True
    except LaunchError as exc:
        if exc.code != "NOT_FOUND":
            raise
        release_exists = False
    if not release_exists:
        create_release(
            workspace_root,
            version=version,
            status="planned",
            previous_version=previous_version,
            released_at=released_at,
        )
    else:
        update_kwargs: dict[str, object] = {"version": version}
        if previous_version is not None:
            update_kwargs["previous_version"] = previous_version
        if released_at is not None:
            update_kwargs["released_at"] = released_at
        if len(update_kwargs) > 1:
            update_release(workspace_root, **update_kwargs)  # type: ignore[arg-type]
    if git_base_ref is not None or git_head_ref is not None:
        update_release(
            workspace_root,
            version=version,
            git_base_ref=git_base_ref if git_base_ref is not None else UNSET,
            git_head_ref=git_head_ref if git_head_ref is not None else UNSET,
        )
    release = load_release(workspace_root, version)
    snapshot = resolve_release_snapshot(workspace_root, release)
    range_summary = build_git_range_summary(
        workspace_root,
        base_ref=snapshot.base_spec,
        head_ref=snapshot.head_spec,
    )
    audit_exists = load_commit_audit_sheet(workspace_root, version) is not None
    audit_result = (
        refresh_commit_audit_sheet(workspace_root, version=version)
        if audit_exists
        else create_commit_audit_sheet(workspace_root, version=version)
    )
    audit_yaml = render_commit_audit_sheet(
        workspace_root, version=version, format_name="yaml"
    )
    assert isinstance(audit_yaml, str)
    scaffold = generate_git_scaffold_batch(
        workspace_root,
        release_version=version,
        base_ref=snapshot.base_spec,
        head_ref=snapshot.head_spec,
    )
    ledgercore.ensure_dir(output_dir)
    range_path = output_dir / "range.json"
    audit_path = output_dir / "audit.yaml"
    scaffold_path = output_dir / "entries.yaml"
    ledgercore.atomic_write_text(
        range_path,
        json.dumps(range_summary, indent=2, sort_keys=True) + "\n",
    )
    ledgercore.atomic_write_text(audit_path, audit_yaml)
    ledgercore.atomic_write_text(
        scaffold_path,
        yaml.safe_dump(scaffold, sort_keys=False, default_flow_style=False),
    )
    return {
        "kind": "release_prepare",
        "version": version,
        "release": load_release(workspace_root, version).to_dict(),
        "audit": audit_result,
        "outputs": {
            "range_json": str(range_path),
            "audit_yaml": str(audit_path),
            "entries_yaml": str(scaffold_path),
        },
    }


def _resolve_changelog_target(workspace_root: Path, target_file: Path) -> Path:
    path = Path(target_file)
    return path if path.is_absolute() else (workspace_root / path)


def _rewrite_changelog_section(
    target_file: Path,
    old_version: str,
    new_version: str | None,
    *,
    mode: str,
    replace_existing: bool = False,
    ignore_missing: bool = False,
) -> dict[str, object]:
    """Apply a changelog section rename/remove to ``target_file`` in place.

    ``mode='rename'`` rewrites the heading from ``old_version`` to
    ``new_version``; ``mode='remove'`` drops the section. Returns a
    deterministic result dict with the relative target path and outcome flags.
    """
    target = Path(target_file)
    text = target.read_text(encoding="utf-8") if target.is_file() else ""
    if mode == "rename":
        assert new_version is not None
        updated = rename_release_section(
            text,
            old_version,
            new_version,
            ignore_missing=ignore_missing,
            replace_existing=replace_existing,
        )
        outcome_key = "section_renamed"
    elif mode == "remove":
        updated = remove_release_section(
            text,
            old_version,
            ignore_missing=ignore_missing,
        )
        outcome_key = "section_removed"
    else:  # pragma: no cover - defensive
        raise LaunchError(
            f"Unsupported changelog section mode: {mode!r}",
            code=CODE_USAGE_ERROR,
            exit_code=2,
        )
    ledgercore.ensure_dir(target.parent)
    ledgercore.atomic_write_text(target, updated)
    return {
        "target_file": str(target),
        outcome_key: True,
        "old_version": old_version,
        "new_version": new_version,
    }


def rename_changelog_section(
    workspace_root: Path,
    *,
    old_version: str,
    new_version: str,
    target_file: Path,
    ignore_missing: bool = False,
    replace_existing: bool = False,
    dry_run: bool = False,
) -> dict[str, object]:
    """Rename a changelog section heading without touching release records."""
    target = _resolve_changelog_target(workspace_root, target_file)
    text = target.read_text(encoding="utf-8") if target.is_file() else ""
    updated = rename_release_section(
        text,
        old_version,
        new_version,
        ignore_missing=ignore_missing,
        replace_existing=replace_existing,
    )
    result: dict[str, object] = {
        "kind": "changelog_section_rename",
        "target_file": str(target),
        "old_version": old_version,
        "new_version": new_version,
        "section_renamed": find_release_section(text, old_version) is not None,
    }
    if dry_run:
        result["updated"] = False
        result["dry_run"] = True
        return result
    ledgercore.ensure_dir(target.parent)
    ledgercore.atomic_write_text(target, updated)
    result["updated"] = True
    return result


def remove_changelog_section(
    workspace_root: Path,
    *,
    version: str,
    target_file: Path,
    ignore_missing: bool = False,
    dry_run: bool = False,
) -> dict[str, object]:
    """Remove a changelog section without touching release records."""
    target = _resolve_changelog_target(workspace_root, target_file)
    text = target.read_text(encoding="utf-8") if target.is_file() else ""
    updated = remove_release_section(
        text,
        version,
        ignore_missing=ignore_missing,
    )
    result: dict[str, object] = {
        "kind": "changelog_section_remove",
        "target_file": str(target),
        "version": version,
        "section_removed": find_release_section(text, version) is not None,
    }
    if dry_run:
        result["updated"] = False
        result["dry_run"] = True
        return result
    ledgercore.ensure_dir(target.parent)
    ledgercore.atomic_write_text(target, updated)
    result["updated"] = True
    return result


def cancel_release(
    workspace_root: Path,
    *,
    version: str,
    reason: str | None = None,
    superseded_by: str | None = None,
    force_released_unshipped: bool = False,
    rewrite_successors: bool = False,
    successor_previous_version: Any = UNSET,
    target_file: Path | None = None,
    remove_changelog_section: bool = False,
    ignore_missing_section: bool = False,
    dry_run: bool = False,
) -> dict[str, object]:
    """Cancel an unshipped release without leaving invalid successor links."""
    existing = load_release(workspace_root, version)
    if existing.status == "canceled":
        raise LaunchError(
            f"Release {version} is already canceled.",
            code=CODE_CONFLICT,
            exit_code=2,
        )
    if existing.status == "released" and not force_released_unshipped:
        raise LaunchError(
            f"Release {version} is 'released'; canceling a shipped release "
            "requires --force-released-unshipped.",
            code=CODE_USAGE_ERROR,
            exit_code=2,
            remediation=[
                "Use release rename if the version number was wrong but it did ship.",
                "Pass --force-released-unshipped if it was recorded as released "
                "but never actually shipped.",
            ],
        )
    if superseded_by is not None:
        validate_release_version(superseded_by)
    successors = [
        record
        for record in list_releases(workspace_root)
        if record.previous_version == version and record.version != version
    ]
    if successors and not rewrite_successors:
        raise LaunchError(
            f"{len(successors)} release(s) reference {version} as their "
            "previous_version; pass --rewrite-successors to update them or "
            "correct them first.",
            code=CODE_CONFLICT,
            exit_code=2,
            data={"successors": sorted(record.version for record in successors)},
        )
    if successors:
        successor_target: str | None
        if successor_previous_version is not UNSET:
            successor_target = successor_previous_version
        else:
            successor_target = existing.previous_version
            target_record = next(
                (
                    r
                    for r in list_releases(workspace_root)
                    if r.version == successor_target
                ),
                None,
            )
            if target_record is None or target_record.status == "canceled":
                successor_target = None
        if successor_target == version:
            raise LaunchError(
                "Successor previous_version cannot point to the canceled release.",
                code=CODE_CONFLICT,
                exit_code=2,
            )
        if successor_target is not None:
            successor_target = validate_release_version(str(successor_target))
            target_record = load_release(workspace_root, successor_target)
            if target_record.status == "canceled":
                raise LaunchError(
                    f"Successor previous release {successor_target} is canceled.",
                    code=CODE_CONFLICT,
                    exit_code=2,
                )
        if successor_target is None and rewrite_successors:
            raise LaunchError(
                "Successor rewrite requires --successor-previous when the "
                "canceled release has no valid predecessor.",
                code=CODE_CONFLICT,
                exit_code=2,
            )
    else:
        successor_target = None
    changelog_result: dict[str, object] | None = None
    changelog_target: Path | None = None
    changelog_updated: str | None = None
    changelog_before: str | None = None
    if remove_changelog_section:
        if target_file is None:
            raise LaunchError(
                "--remove-changelog-section requires --target-file.",
                code=CODE_USAGE_ERROR,
                exit_code=2,
            )
        changelog_target = _resolve_changelog_target(workspace_root, target_file)
        changelog_before = (
            changelog_target.read_text(encoding="utf-8")
            if changelog_target.is_file()
            else ""
        )
        changelog_updated = remove_release_section(
            changelog_before, version, ignore_missing=ignore_missing_section
        )
        changelog_result = {
            "target_file": str(changelog_target),
            "section_removed": find_release_section(changelog_before, version)
            is not None,
            "updated": not dry_run,
        }
    canceled = replace(
        existing,
        status="canceled",
        cancel_reason=reason,
        superseded_by=superseded_by,
        versioning=bump_versioning(existing.versioning),
    )
    successor_changes = [
        {"version": successor.version, "from": version, "to": successor_target}
        for successor in successors
    ]
    if dry_run:
        return {
            "kind": "release_cancel",
            "release": canceled.to_dict(),
            "successor_changes": successor_changes,
            "rewrote_successors": bool(successors),
            "changelog": changelog_result,
            "dry_run": True,
            "written": False,
        }
    record_revisions = {f"release:{version}": canceled.versioning.revision}
    save_release(workspace_root, canceled, overwrite=True)
    for successor in successors:
        updated_successor = replace(
            successor,
            previous_version=successor_target,
            versioning=bump_versioning(successor.versioning),
        )
        save_release(workspace_root, updated_successor, overwrite=True)
        record_revisions[f"release:{successor.version}"] = (
            updated_successor.versioning.revision
        )
    if changelog_target is not None and changelog_updated is not None:
        ledgercore.ensure_dir(changelog_target.parent)
        ledgercore.atomic_write_text(changelog_target, changelog_updated)
    event = append_event(
        workspace_root,
        event=EVENT_RELEASE_CANCELED,
        release_version=version,
        record_revisions=record_revisions,
        data={
            "reason": reason,
            "previous_status": existing.status,
            "superseded_by": superseded_by,
            "force_released_unshipped": bool(force_released_unshipped),
            "rewrote_successors": bool(successors),
            "successor_versions": [successor.version for successor in successors],
            "successor_previous_version": successor_target,
            "changelog_section_removed": bool(
                changelog_result and changelog_result.get("section_removed")
            ),
        },
    )
    rebuild_indexes(workspace_root)
    payload = _release_payload(workspace_root, canceled, event.event_id)
    payload["successor_changes"] = successor_changes
    payload["rewrote_successors"] = bool(successors)
    payload["changelog"] = changelog_result
    return payload


def rename_release(
    workspace_root: Path,
    *,
    old_version: str,
    new_version: str,
    previous_version: Any = UNSET,
    title: str | None = None,
    released_at: Any = UNSET,
    force_released_unshipped: bool = False,
    rewrite_successors: bool = False,
    target_file: Path | None = None,
    rename_changelog_section: bool = False,
    replace_existing_section: bool = False,
) -> dict[str, object]:
    """Rename a release from ``old_version`` to ``new_version``.

    Moves the release bundle, rewrites the release and entry front matter to
    the new version, optionally rewrites successor ``previous_version``
    references, and appends a ``release.renamed`` event. Refuses a ``released``
    source unless ``force_released_unshipped`` is set (the version number was
    wrong but the release never actually shipped under that tag).
    """
    validate_release_version(old_version)
    validate_release_version(new_version)
    if old_version == new_version:
        raise LaunchError(
            "Rename source and target versions must differ.",
            code=CODE_USAGE_ERROR,
            exit_code=2,
        )
    existing = load_release(workspace_root, old_version)
    if existing.status == "released" and not force_released_unshipped:
        raise LaunchError(
            f"Release {old_version} is 'released'; renaming a shipped release"
            " requires --force-released-unshipped.",
            code=CODE_USAGE_ERROR,
            exit_code=2,
            remediation=[
                "Use release cancel to keep a tombstone if it never shipped.",
                "Pass --force-released-unshipped if the version number was wrong.",
            ],
        )
    paths = resolve_project_paths(workspace_root)
    if release_markdown_path(paths, new_version).is_file():
        raise LaunchError(
            f"Release version already exists: {new_version}",
            code=CODE_CONFLICT,
            exit_code=2,
            remediation=[f"Run `releaseledger release show {new_version}`."],
        )
    if released_at is not UNSET and released_at is not None:
        _validate_date(str(released_at), "--released-at")
    resolved_released_at = (
        released_at if released_at is not UNSET else existing.released_at
    )
    resolved_previous = (
        previous_version if previous_version is not UNSET else existing.previous_version
    )
    if resolved_previous is not None:
        resolved_previous = validate_release_version(str(resolved_previous))
    # Adjust a default tag title ("Release OLD") to the new version; keep a
    # custom title unless --title overrides it.
    resolved_title: str | None
    if title is not None:
        resolved_title = title
    elif existing.title == f"Release {old_version}":
        resolved_title = f"Release {new_version}"
    else:
        resolved_title = existing.title
    # Successor check: any release pointing at the old version as a predecessor.
    successors = [
        r
        for r in list_releases(workspace_root)
        if r.previous_version == old_version and r.version != old_version
    ]
    if successors and not rewrite_successors:
        raise LaunchError(
            f"{len(successors)} release(s) reference {old_version} as their"
            " previous_version; pass --rewrite-successors to update them or"
            " correct them first.",
            code=CODE_CONFLICT,
            exit_code=2,
            data={"successors": sorted(r.version for r in successors)},
        )
    new_record = ReleaseRecord(
        version=new_version,
        status=existing.status,
        title=resolved_title,
        versioning=bump_versioning(existing.versioning),
        released_at=resolved_released_at,
        previous_version=resolved_previous,
        cancel_reason=existing.cancel_reason,
        superseded_by=existing.superseded_by,
        note=existing.note,
        changelog_file=existing.changelog_file,
        boundary_ref=existing.boundary_ref,
        source_refs=existing.source_refs,
        source_count=existing.source_count,
        entry_count=existing.entry_count,
        artifact_count=existing.artifact_count,
    )
    rename_release_bundle(workspace_root, old_version, new_record)
    rewrote_successors = False
    record_revisions = {
        f"release:{new_version}": new_record.versioning.revision,
    }
    for entry in load_entries(workspace_root, new_version):
        record_revisions[f"entry:{new_version}/{entry.entry_id}"] = (
            entry.versioning.revision
        )
    if successors:
        for successor in successors:
            updated_successor = replace(
                successor,
                previous_version=new_version,
                versioning=bump_versioning(successor.versioning),
            )
            save_release(workspace_root, updated_successor, overwrite=True)
            record_revisions[f"release:{successor.version}"] = (
                updated_successor.versioning.revision
            )
        rewrote_successors = True
    event = append_event(
        workspace_root,
        event=EVENT_RELEASE_RENAMED,
        release_version=new_version,
        record_revisions=record_revisions,
        data={
            "old_release_version": old_version,
            "rewrote_successors": bool(rewrote_successors),
        },
    )
    rebuild_indexes(workspace_root)
    payload = _release_payload(workspace_root, new_record, event.event_id)
    if target_file is not None and rename_changelog_section:
        changelog_result = _rewrite_changelog_section(
            _resolve_changelog_target(workspace_root, target_file),
            old_version,
            new_version,
            mode="rename",
            replace_existing=replace_existing_section,
        )
        payload["changelog"] = changelog_result
    return payload


def reconcile_releases(
    workspace_root: Path,
    *,
    changelog_file: Path | None = None,
) -> dict[str, object]:
    """Compare release records, Git tags, and changelog headings read-only."""
    records = list_releases(workspace_root)
    by_version = {record.version: record for record in records}
    git_result = subprocess.run(
        ["git", "-C", str(workspace_root), "tag", "--list"],
        check=False,
        capture_output=True,
        text=True,
    )
    raw_tags = [line.strip() for line in git_result.stdout.splitlines() if line.strip()]
    tags_by_version: dict[str, list[str]] = {}
    for tag in raw_tags:
        normalized = tag[1:] if tag.startswith("v") else tag
        if parse_release_version_tuple(normalized) is None:
            continue
        tags_by_version.setdefault(normalized, []).append(tag)
    tag_dates: dict[str, str] = {}
    for tag in raw_tags:
        normalized = tag[1:] if tag.startswith("v") else tag
        if normalized not in tags_by_version:
            continue
        tag_result = subprocess.run(
            ["git", "-C", str(workspace_root), "log", "-1", "--format=%cs", tag],
            check=False,
            capture_output=True,
            text=True,
        )
        if tag_result.returncode == 0 and tag_result.stdout.strip():
            tag_dates[tag] = tag_result.stdout.strip()
    paths = resolve_project_paths(workspace_root)
    target = (
        Path(changelog_file)
        if changelog_file is not None
        else workspace_root / paths.config.changelog_output
    )
    if not target.is_absolute():
        target = workspace_root / target
    changelog_text = target.read_text(encoding="utf-8") if target.is_file() else ""
    heading_re = re.compile(r"^##\s+\[?\s*([^\]\s]+)", re.MULTILINE)
    headings: dict[str, list[str]] = {}
    for match in heading_re.finditer(changelog_text):
        heading = match.group(1)
        if heading.lower() == "unreleased":
            continue
        line_end = changelog_text.find("\n", match.start())
        line = changelog_text[
            match.start() : line_end if line_end >= 0 else len(changelog_text)
        ]
        headings.setdefault(heading, []).append(line)
    problems: list[dict[str, object]] = []
    for version, tags in sorted(tags_by_version.items()):
        if len(tags) > 1:
            problems.append(
                {"kind": "ambiguous_tag_version", "version": version, "tags": tags}
            )
        if version not in by_version:
            problems.append(
                {"kind": "tag_without_release", "version": version, "tags": tags}
            )
    for version in sorted(headings):
        if version not in by_version:
            problems.append(
                {
                    "kind": "changelog_without_release",
                    "version": version,
                    "target_file": str(target),
                }
            )
    for record in records:
        tags = tags_by_version.get(record.version, [])
        has_heading = bool(headings.get(record.version))
        if record.status in {"planned", "draft", "candidate"} and tags:
            problems.append(
                {
                    "kind": "planned_with_tag",
                    "version": record.version,
                    "status": record.status,
                    "tags": tags,
                }
            )
        if record.status == "released":
            if not tags and git_result.returncode == 0:
                problems.append(
                    {"kind": "release_without_tag", "version": record.version}
                )
            if record.released_at is None:
                problems.append(
                    {"kind": "released_without_date", "version": record.version}
                )
            if target.is_file() and not has_heading:
                problems.append(
                    {"kind": "released_without_changelog", "version": record.version}
                )
        if record.status == "canceled":
            if has_heading:
                problems.append(
                    {"kind": "canceled_with_changelog", "version": record.version}
                )
            if any(r.previous_version == record.version for r in records):
                problems.append(
                    {
                        "kind": "canceled_with_successor_reference",
                        "version": record.version,
                    }
                )
        if has_heading and record.released_at:
            heading = headings[record.version][0]
            heading_date = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", heading)
            for tag in tags:
                tag_date = tag_dates.get(tag)
                if tag_date and heading_date and tag_date != heading_date.group(1):
                    problems.append(
                        {
                            "kind": "tag_changelog_date_mismatch",
                            "version": record.version,
                            "tag": tag,
                            "tag_date": tag_date,
                            "changelog_date": heading_date.group(1),
                        }
                    )
            if heading_date and heading_date.group(1) != record.released_at:
                problems.append(
                    {
                        "kind": "release_changelog_date_mismatch",
                        "version": record.version,
                        "release_date": record.released_at,
                        "changelog_date": heading_date.group(1),
                    }
                )
    released = sorted(
        (record for record in records if record.status == "released"),
        key=lambda record: (
            parse_release_version_tuple(record.version) is None,
            parse_release_version_tuple(record.version) or (),
            record.version,
        ),
    )
    for index, record in enumerate(released):
        if index and record.previous_version != released[index - 1].version:
            problems.append(
                {
                    "kind": "noncanonical_previous",
                    "version": record.version,
                    "previous_version": record.previous_version,
                    "expected_previous": released[index - 1].version,
                }
            )
    problems.sort(key=lambda item: (str(item.get("kind")), str(item.get("version"))))
    return {
        "kind": "release_reconcile",
        "ok": not problems,
        "problem_count": len(problems),
        "problems": problems,
        "release_versions": sorted(by_version),
        "tags": {
            version: sorted(tags) for version, tags in sorted(tags_by_version.items())
        },
        "tag_dates": dict(sorted(tag_dates.items())),
        "changelog_file": str(target),
        "changelog_versions": sorted(headings),
    }


def check_release_chain(
    workspace_root: Path,
    *,
    allow_canceled_predecessors: bool = False,
) -> dict[str, object]:
    """Inspect release predecessor links and return deterministic health data."""
    releases = list_releases(workspace_root)
    by_version = {record.version: record for record in releases}
    problems: list[dict[str, object]] = []
    for record in releases:
        prev = record.previous_version
        if prev is None:
            continue
        if prev == record.version:
            problems.append(
                {
                    "kind": "self_previous",
                    "version": record.version,
                    "previous_version": prev,
                    "record_status": record.status,
                    "predecessor_status": record.status,
                    "comparison_basis": "none",
                    "detail": "Release points to itself as its previous_version.",
                }
            )
            continue
        predecessor = by_version.get(prev)
        if predecessor is None:
            problems.append(
                {
                    "kind": "missing_previous",
                    "version": record.version,
                    "previous_version": prev,
                    "record_status": record.status,
                    "predecessor_status": None,
                    "comparison_basis": "none",
                    "detail": f"previous_version {prev!r} has no matching release.",
                }
            )
            continue
        if predecessor.status == "canceled" and not allow_canceled_predecessors:
            problems.append(
                {
                    "kind": "canceled_previous",
                    "version": record.version,
                    "previous_version": prev,
                    "record_status": record.status,
                    "predecessor_status": predecessor.status,
                    "comparison_basis": "status",
                    "detail": "previous_version points at a canceled release.",
                }
            )
        comparison, basis = _compare_release_order(
            predecessor.version,
            predecessor.released_at,
            record.version,
            record.released_at,
        )
        if comparison is None:
            problems.append(
                {
                    "kind": "ambiguous_previous_order",
                    "version": record.version,
                    "previous_version": prev,
                    "record_status": record.status,
                    "predecessor_status": predecessor.status,
                    "comparison_basis": basis,
                    "detail": "Release order cannot be established from dates or semantic versions.",
                }
            )
        elif comparison > 0:
            problems.append(
                {
                    "kind": "future_previous",
                    "version": record.version,
                    "previous_version": prev,
                    "record_status": record.status,
                    "predecessor_status": predecessor.status,
                    "comparison_basis": basis,
                    "detail": "previous_version points at a newer release.",
                }
            )
    semver_releases = [
        r for r in releases if parse_release_version_tuple(r.version) is not None
    ]
    if semver_releases:
        root = min(
            semver_releases,
            key=lambda r: (parse_release_version_tuple(r.version), r.version),
        )
        if root.previous_version is not None:
            predecessor = by_version.get(root.previous_version)
            problems.append(
                {
                    "kind": "root_has_previous",
                    "version": root.version,
                    "previous_version": root.previous_version,
                    "record_status": root.status,
                    "predecessor_status": predecessor.status if predecessor else None,
                    "comparison_basis": "semantic_version",
                    "detail": "Earliest semantic release should have no predecessor.",
                }
            )
    problems.sort(key=lambda item: (str(item["kind"]), str(item["version"])))
    return {
        "kind": "release_chain_check",
        "ok": not problems,
        "problem_count": len(problems),
        "problems": problems,
    }


def repair_release_chain(
    workspace_root: Path,
    *,
    apply_changes: bool = False,
    allow_canceled_predecessors: bool = False,
) -> dict[str, object]:
    """Recompute predecessor links from release order and report/apply fixes.

    Builds the canonical chain by sorting non-canceled releases by
    (released_at, semantic version) and assigning each release's
    ``previous_version`` to the release immediately before it (the first gets
    ``None``). Canceled releases are left untouched. With ``apply_changes``
    False this is a dry run that reports the planned changes; with True it
    writes them, appends a ``release.chain_repaired`` event, and rebuilds
    indexes.
    """
    releases = list_releases(workspace_root)
    released = [r for r in releases if r.status == "released"]
    active = [r for r in releases if r.status not in {"released", "canceled"}]
    released.sort(
        key=lambda r: (
            r.released_at is None,
            r.released_at or "",
            parse_release_version_tuple(r.version) is None,
            parse_release_version_tuple(r.version) or (),
            r.version,
        )
    )
    active.sort(
        key=lambda r: (
            parse_release_version_tuple(r.version) is None,
            parse_release_version_tuple(r.version) or (),
            r.version,
        )
    )
    chain = [*released, *active]
    changes: list[dict[str, object]] = []
    for index, record in enumerate(chain):
        expected = chain[index - 1].version if index > 0 else None
        if record.previous_version != expected:
            changes.append(
                {
                    "version": record.version,
                    "from": record.previous_version,
                    "to": expected,
                }
            )
    payload: dict[str, object] = {
        "kind": "release_chain_repair",
        "applied": bool(apply_changes),
        "change_count": len(changes),
        "changes": changes,
        "ok": not changes,
    }
    if not apply_changes or not changes:
        return payload
    by_version = {record.version: record for record in chain}
    record_revisions: dict[str, int] = {}
    for change in changes:
        record = by_version[str(change["version"])]
        updated = replace(
            record,
            previous_version=change["to"],  # type: ignore[arg-type]
            versioning=bump_versioning(record.versioning),
        )
        save_release(workspace_root, updated, overwrite=True)
        record_revisions[f"release:{record.version}"] = updated.versioning.revision
    event = append_event(
        workspace_root,
        event=EVENT_RELEASE_CHAIN_REPAIRED,
        record_revisions=record_revisions,
        data={
            "changed_releases": [str(change["version"]) for change in changes],
        },
    )
    rebuild_indexes(workspace_root)
    payload["events"] = [event.event_id]
    return payload
