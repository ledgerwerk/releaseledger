"""Commit audit sheet service layer.

Builds, renders, validates, and synchronizes the per-release commit audit sheet
(see :mod:`releaseledger.domain.audit`). The sheet is evidence/review state, not
changelog prose: commit subjects are stored as ``evidence_subject`` and the
subject-summary guard rejects entry summaries that equal (or trivially transform)
a commit subject.
"""

from __future__ import annotations

import re
import string
from dataclasses import replace
from pathlib import Path

import yaml

from releaseledger.domain.audit import (
    AUDIT_DECISIONS,
    AUDIT_PUBLIC_IMPACTS,
    CommitAuditRow,
    CommitAuditSheetRecord,
    CommitAuditStats,
    audit_sheet_from_dict,
    audit_sheet_to_dict,
    validate_sha,
)
from releaseledger.domain.entry import ReleaseEntryRecord
from releaseledger.domain.versioning import initial_versioning
from releaseledger.errors import (
    CODE_NOT_FOUND,
    CODE_USAGE_ERROR,
    CODE_VALIDATION_ERROR,
    LaunchError,
)
from releaseledger.services.events import append_event
from releaseledger.services.git_sources import (
    GIT_DEFAULT_HEAD,
    GIT_DEFAULT_INCLUDE_MERGES,
    GitSourceCandidate,
    build_git_range_summary,
    collect_git_candidates,
    resolve_release_snapshot,
)
from releaseledger.storage.paths import resolve_project_paths
from releaseledger.storage.store import (
    load_commit_audit_sheet,
    load_entries,
    load_release,
    next_commit_audit_versioning,
    save_commit_audit_sheet,
)

__all__ = [
    "apply_commit_audit_annotations",
    "create_commit_audit_sheet",
    "project_audit_entry_coverage",
    "refresh_commit_audit_sheet",
    "render_commit_audit_sheet",
    "sync_audit_sheet_targets",
    "sync_audit_targets_from_entries",
    "update_commit_audit_sheet",
    "validate_commit_audit_sheet",
    "subject_matches_evidence_subject",
]

_MUTABLE_ROW_FIELDS = frozenset(
    {
        "inspected",
        "inspected_paths",
        "observed_behavior",
        "public_impact",
        "decision",
        "target_entry_key",
        "target_entry_id",
        "notes",
    }
)
_COMPLETED_DECISIONS = frozenset({"accepted", "grouped", "internal", "rejected"})


def _as_int(value: object) -> int:
    """Coerce a report dict value to int for typed callers."""
    if isinstance(value, bool) or not isinstance(value, int):
        return int(str(value))
    return value


# ---------------------------------------------------------------------------
# Range resolution and sheet creation
# ---------------------------------------------------------------------------


def _resolve_range(
    workspace_root: Path,
    *,
    version: str,
    git_base: str | None,
    git_head: str | None,
) -> tuple[str, str, str, str]:
    """Resolve immutable audit snapshot specs and their display refs."""
    release = load_release(workspace_root, version)
    snapshot = resolve_release_snapshot(
        workspace_root,
        release,
        explicit_base=git_base,
        explicit_head=git_head,
        default_head=GIT_DEFAULT_HEAD,
    )
    return (
        snapshot.base_spec,
        snapshot.head_spec,
        snapshot.base_ref,
        snapshot.head_ref,
    )


def _row_from_candidate(candidate: GitSourceCandidate) -> CommitAuditRow:
    stats = CommitAuditStats(
        insertions=candidate.additions or 0,
        deletions=candidate.deletions or 0,
    )
    return CommitAuditRow(
        sha=validate_sha(candidate.sha),
        source_ref=candidate.source_ref,
        short_sha=candidate.short_sha,
        evidence_subject=candidate.subject,
        changed_paths=tuple(candidate.paths),
        stats=stats,
        inspected=False,
        inspected_paths=(),
        observed_behavior="",
        public_impact="unknown",
        decision="needs_review",
        target_entry_key=None,
        target_entry_id=None,
        notes=None,
    )


def _load_yaml_mapping(
    file: Path,
    *,
    label: str,
    remediation: list[str] | None = None,
) -> dict[str, object]:
    import ledgercore

    try:
        raw = ledgercore.load_yaml_object(file, label=label)
    except ledgercore.YamlStoreError as exc:
        raise LaunchError(
            str(exc),
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
            remediation=remediation or [],
        ) from exc
    if not isinstance(raw, dict):
        raise LaunchError(
            f"{label} must contain a mapping.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    return dict(raw)


def _sheet_with_next_versioning(
    existing: CommitAuditSheetRecord | None,
    candidate: CommitAuditSheetRecord,
) -> CommitAuditSheetRecord:
    return replace(
        candidate,
        versioning=next_commit_audit_versioning(existing, candidate),
    )


def _refresh_row_from_existing(
    candidate: GitSourceCandidate,
    existing: CommitAuditRow | None,
) -> CommitAuditRow:
    fresh = _row_from_candidate(candidate)
    if existing is None:
        return fresh
    return replace(
        fresh,
        inspected=existing.inspected,
        inspected_paths=existing.inspected_paths,
        observed_behavior=existing.observed_behavior,
        public_impact=existing.public_impact,
        decision=existing.decision,
        target_entry_key=existing.target_entry_key,
        target_entry_id=existing.target_entry_id,
        notes=existing.notes,
    )


def _validate_completed_rows(sheet: CommitAuditSheetRecord) -> list[dict[str, object]]:
    issues: list[dict[str, object]] = []
    for row in sheet.rows:
        if row.decision in _COMPLETED_DECISIONS and not row.observed_behavior.strip():
            issues.append(
                {
                    "sha": row.sha,
                    "source_ref": row.source_ref,
                    "field": "observed_behavior",
                    "code": "empty_observed_behavior",
                    "message": ("Completed audit rows require observed_behavior text."),
                }
            )
    return issues


def create_commit_audit_sheet(
    workspace_root: Path,
    *,
    version: str,
    git_base: str | None = None,
    git_head: str | None = None,
    overwrite: bool = False,
) -> dict[str, object]:
    """Create the canonical YAML sheet for ``version`` from the git range.

    Initial rows are ``decision=needs_review`` and ``inspected=false`` with blank
    observed behavior. Refuses an existing sheet unless ``overwrite`` is set.
    Emits an ``audit.created`` event.
    """
    workspace_root = workspace_root.expanduser().resolve()
    resolve_project_paths(workspace_root)
    existing = load_commit_audit_sheet(workspace_root, version)
    if existing is not None and not overwrite:
        raise LaunchError(
            f"Commit audit sheet already exists for {version}.",
            code=CODE_USAGE_ERROR,
            exit_code=2,
            remediation=["Pass --overwrite to regenerate the sheet."],
        )
    base_spec, head_spec, base_ref, head_ref = _resolve_range(
        workspace_root, version=version, git_base=git_base, git_head=git_head
    )
    summary = build_git_range_summary(
        workspace_root,
        base_ref=base_spec,
        head_ref=head_spec,
        include_merges=GIT_DEFAULT_INCLUDE_MERGES,
    )
    candidates = collect_git_candidates(
        workspace_root,
        base_ref=base_spec,
        head_ref=head_spec,
        include_merges=GIT_DEFAULT_INCLUDE_MERGES,
    )
    rows = tuple(_row_from_candidate(c) for c in candidates)
    candidate_sheet = CommitAuditSheetRecord(
        release_version=version,
        versioning=initial_versioning(),
        git_base_ref=base_ref,
        git_base_sha=str(summary["base_sha"]),
        git_head_ref=head_ref,
        git_head_sha=str(summary["head_sha"]),
        git_range=str(summary["range"]),
        commit_count=_as_int(summary["commit_count"]),
        rows=rows,
    )
    sheet = (
        _sheet_with_next_versioning(existing, candidate_sheet)
        if overwrite
        else candidate_sheet
    )
    saved = save_commit_audit_sheet(workspace_root, sheet, overwrite=overwrite)
    append_event(
        workspace_root,
        event="audit.created",
        release_version=version,
        record_revisions={
            "commit_audit_sheet": saved.versioning.revision,
        },
        data={"row_count": len(saved.rows)},
    )
    return {
        "kind": "commit_audit_sheet_created",
        "version": version,
        "row_count": len(saved.rows),
        "git_base_ref": saved.git_base_ref,
        "git_head_ref": saved.git_head_ref,
        "git_range": saved.git_range,
        "revision": saved.versioning.revision,
    }


# ---------------------------------------------------------------------------
# Update (import edited YAML)
# ---------------------------------------------------------------------------


def update_commit_audit_sheet(
    workspace_root: Path,
    *,
    version: str,
    file: Path,
) -> dict[str, object]:
    """Replace the sheet for ``version`` from an edited YAML ``file``.

    Validates decision/public_impact enums and that every existing row SHA is
    still present. Bumps revision only when content changed. Emits an
    ``audit.updated`` event.
    """
    workspace_root = workspace_root.expanduser().resolve()
    resolve_project_paths(workspace_root)
    existing = load_commit_audit_sheet(workspace_root, version)
    if existing is None:
        raise LaunchError(
            f"No commit audit sheet for {version}. "
            "Run `releaseledger audit init` first.",
            code=CODE_NOT_FOUND,
            exit_code=2,
        )
    incoming = audit_sheet_from_dict(
        _load_yaml_mapping(
            file,
            label=f"audit update {version}",
            remediation=[
                "Export a canonical audit file and edit only the mutable row fields.",
                f"Run `releaseledger audit show {version}"
                " --format yaml --output FILE`.",
            ],
        )
    )
    if incoming.release_version != version:
        raise LaunchError(
            f"Audit update release_version {incoming.release_version!r} must "
            f"match {version!r}.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    existing_shas = {row.sha for row in existing.rows}
    incoming_shas = {row.sha for row in incoming.rows}
    missing = sorted(existing_shas - incoming_shas)
    if missing:
        raise LaunchError(
            f"Audit update for {version} is missing {len(missing)} row(s): "
            + ", ".join(sha[:7] for sha in missing),
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
            remediation=[
                "Keep every original row. Add decisions/behavior but do not "
                "drop commits. Use --allow-missing when supported."
            ],
        )
    # Carry range metadata forward when the edited file dropped it.
    new_rows = incoming.rows
    candidate = CommitAuditSheetRecord(
        release_version=incoming.release_version,
        versioning=initial_versioning(),
        git_base_ref=incoming.git_base_ref or existing.git_base_ref,
        git_base_sha=incoming.git_base_sha or existing.git_base_sha,
        git_head_ref=incoming.git_head_ref or existing.git_head_ref,
        git_head_sha=incoming.git_head_sha or existing.git_head_sha,
        git_range=incoming.git_range or existing.git_range,
        commit_count=incoming.commit_count or existing.commit_count,
        rows=new_rows,
    )
    issues = _validate_completed_rows(candidate)
    if issues:
        raise LaunchError(
            f"Audit update for {version} has {len(issues)} invalid completed row(s).",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
            data={"issues": issues},
        )
    updated = _sheet_with_next_versioning(existing, candidate)
    saved = save_commit_audit_sheet(workspace_root, updated, overwrite=True)
    append_event(
        workspace_root,
        event="audit.updated",
        release_version=version,
        record_revisions={
            "commit_audit_sheet": saved.versioning.revision,
        },
        data={"row_count": len(saved.rows)},
    )
    return {
        "kind": "commit_audit_sheet_updated",
        "version": version,
        "row_count": len(saved.rows),
        "changed_rows": sum(
            left != right
            for left, right in zip(existing.rows, saved.rows, strict=False)
        ),
        "revision": saved.versioning.revision,
    }


def apply_commit_audit_annotations(
    workspace_root: Path,
    *,
    version: str,
    file: Path,
    dry_run: bool = False,
) -> dict[str, object]:
    """Merge row-annotation changes into the canonical audit sheet."""
    workspace_root = workspace_root.expanduser().resolve()
    resolve_project_paths(workspace_root)
    existing = load_commit_audit_sheet(workspace_root, version)
    if existing is None:
        raise LaunchError(
            f"No commit audit sheet for {version}."
            " Run `releaseledger audit init` first.",
            code=CODE_NOT_FOUND,
            exit_code=2,
        )
    raw = _load_yaml_mapping(
        file,
        label=f"audit apply {version}",
        remediation=[
            "Export the canonical audit sheet first if you need a safe scaffold.",
            f"Run `releaseledger audit show {version} --format yaml --output FILE`.",
        ],
    )
    raw_rows = raw.get("rows")
    if not isinstance(raw_rows, list):
        raise LaunchError(
            f"audit apply {version} must contain a top-level 'rows' list.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    existing_data = audit_sheet_to_dict(existing)
    existing_rows = existing_data.get("rows", [])
    assert isinstance(existing_rows, list)
    rows_by_sha = {
        str(row["sha"]): dict(row)
        for row in existing_rows
        if isinstance(row, dict) and isinstance(row.get("sha"), str)
    }
    seen: set[str] = set()
    changed_rows = 0
    for item in raw_rows:
        if not isinstance(item, dict):
            raise LaunchError(
                "Every audit apply row must be a mapping.",
                code=CODE_VALIDATION_ERROR,
                exit_code=2,
            )
        if "sha" not in item:
            raise LaunchError(
                "Every audit apply row must include a sha.",
                code=CODE_VALIDATION_ERROR,
                exit_code=2,
            )
        sha = validate_sha(str(item["sha"]))
        if sha in seen:
            raise LaunchError(
                f"Duplicate audit apply row for {sha[:7]}.",
                code=CODE_VALIDATION_ERROR,
                exit_code=2,
            )
        seen.add(sha)
        if sha not in rows_by_sha:
            raise LaunchError(
                f"Audit apply row references unknown commit {sha[:7]}.",
                code=CODE_VALIDATION_ERROR,
                exit_code=2,
            )
        extra = sorted(
            key for key in item if key not in _MUTABLE_ROW_FIELDS and key != "sha"
        )
        if extra:
            raise LaunchError(
                "Audit apply rows may only update mutable fields; got: "
                + ", ".join(extra),
                code=CODE_VALIDATION_ERROR,
                exit_code=2,
            )
        before = dict(rows_by_sha[sha])
        after = dict(before)
        for key in _MUTABLE_ROW_FIELDS:
            if key in item:
                after[key] = item[key]
        rows_by_sha[sha] = after
        if before != after:
            changed_rows += 1
    merged_rows = [
        rows_by_sha[str(row["sha"])] for row in existing_rows if isinstance(row, dict)
    ]
    updated_data = dict(existing_data)
    updated_data["rows"] = merged_rows
    candidate = audit_sheet_from_dict(updated_data)
    issues = _validate_completed_rows(candidate)
    if issues:
        raise LaunchError(
            f"Audit apply for {version} has {len(issues)} invalid completed row(s).",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
            data={"issues": issues},
        )
    updated = _sheet_with_next_versioning(existing, candidate)
    if dry_run:
        return {
            "kind": "commit_audit_apply",
            "version": version,
            "written": False,
            "row_count": len(existing.rows),
            "updated_rows": changed_rows,
            "revision": updated.versioning.revision,
        }
    if changed_rows == 0:
        return {
            "kind": "commit_audit_apply",
            "version": version,
            "written": False,
            "row_count": len(existing.rows),
            "updated_rows": 0,
            "revision": existing.versioning.revision,
        }
    saved = save_commit_audit_sheet(workspace_root, updated, overwrite=True)
    append_event(
        workspace_root,
        event="audit.updated",
        release_version=version,
        record_revisions={"commit_audit_sheet": saved.versioning.revision},
        data={"updated_rows": changed_rows},
    )
    return {
        "kind": "commit_audit_apply",
        "version": version,
        "written": True,
        "row_count": len(saved.rows),
        "updated_rows": changed_rows,
        "revision": saved.versioning.revision,
    }


def refresh_commit_audit_sheet(
    workspace_root: Path,
    *,
    version: str,
    git_base: str | None = None,
    git_head: str | None = None,
    allow_remove: bool = False,
) -> dict[str, object]:
    """Reconcile an existing audit sheet with a refreshed git snapshot."""
    workspace_root = workspace_root.expanduser().resolve()
    resolve_project_paths(workspace_root)
    existing = load_commit_audit_sheet(workspace_root, version)
    if existing is None:
        raise LaunchError(
            f"No commit audit sheet for {version}."
            " Run `releaseledger audit init` first.",
            code=CODE_NOT_FOUND,
            exit_code=2,
        )
    base_spec, head_spec, base_ref, head_ref = _resolve_range(
        workspace_root, version=version, git_base=git_base, git_head=git_head
    )
    summary = build_git_range_summary(
        workspace_root,
        base_ref=base_spec,
        head_ref=head_spec,
        include_merges=GIT_DEFAULT_INCLUDE_MERGES,
    )
    candidates = collect_git_candidates(
        workspace_root,
        base_ref=base_spec,
        head_ref=head_spec,
        include_merges=GIT_DEFAULT_INCLUDE_MERGES,
    )
    existing_by_sha = {row.sha: row for row in existing.rows}
    candidate_shas = {candidate.sha for candidate in candidates}
    removed = sorted(set(existing_by_sha) - candidate_shas)
    if removed and not allow_remove:
        raise LaunchError(
            f"Audit refresh for {version} would remove {len(removed)} row(s): "
            + ", ".join(sha[:7] for sha in removed),
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
            remediation=[
                "Pass --allow-remove to accept a rewritten range.",
                "Or keep the existing snapshot and audit the new commits explicitly.",
            ],
        )
    refreshed_rows = tuple(
        _refresh_row_from_existing(candidate, existing_by_sha.get(candidate.sha))
        for candidate in candidates
    )
    candidate_sheet = CommitAuditSheetRecord(
        release_version=version,
        versioning=initial_versioning(),
        git_base_ref=base_ref,
        git_base_sha=str(summary["base_sha"]),
        git_head_ref=head_ref,
        git_head_sha=str(summary["head_sha"]),
        git_range=str(summary["range"]),
        commit_count=_as_int(summary["commit_count"]),
        rows=refreshed_rows,
    )
    updated = _sheet_with_next_versioning(existing, candidate_sheet)
    preserved_reviewed = sum(
        1
        for sha, row in existing_by_sha.items()
        if sha in candidate_shas and row.inspected
    )
    new_rows = sum(
        1 for candidate in candidates if candidate.sha not in existing_by_sha
    )
    result = {
        "kind": "commit_audit_refresh",
        "version": version,
        "written": updated.versioning.revision != existing.versioning.revision,
        "row_count": len(refreshed_rows),
        "preserved_reviewed_rows": preserved_reviewed,
        "new_rows": new_rows,
        "removed_rows": len(removed),
        "removed_shas": removed,
        "revision": updated.versioning.revision,
        "git_base_ref": base_ref,
        "git_head_ref": head_ref,
        "git_range": str(summary["range"]),
    }
    if updated.versioning.revision == existing.versioning.revision:
        return result
    saved = save_commit_audit_sheet(workspace_root, updated, overwrite=True)
    append_event(
        workspace_root,
        event="audit.updated",
        release_version=version,
        record_revisions={"commit_audit_sheet": saved.versioning.revision},
        data={
            "preserved_reviewed_rows": preserved_reviewed,
            "new_rows": new_rows,
            "removed_rows": len(removed),
        },
    )
    result["revision"] = saved.versioning.revision
    result["written"] = True
    return result


# ---------------------------------------------------------------------------
# Render / show
# ---------------------------------------------------------------------------


def render_commit_audit_sheet(
    workspace_root: Path,
    *,
    version: str,
    format_name: str = "markdown",
) -> str | dict[str, object]:
    """Render the audit sheet for display/export."""
    workspace_root = workspace_root.expanduser().resolve()
    resolve_project_paths(workspace_root)
    sheet = load_commit_audit_sheet(workspace_root, version)
    if sheet is None:
        raise LaunchError(
            f"No commit audit sheet for {version}.",
            code=CODE_NOT_FOUND,
            exit_code=2,
            remediation=[f"Run `releaseledger audit init {version}`."],
        )
    if format_name == "json":
        return audit_sheet_to_dict(sheet)
    if format_name == "yaml":
        return yaml.safe_dump(
            audit_sheet_to_dict(sheet),
            sort_keys=False,
            default_flow_style=False,
            allow_unicode=True,
        )
    if format_name != "markdown":
        raise LaunchError(
            f"Unsupported --format {format_name!r}. Use markdown, json, or yaml.",
            code=CODE_USAGE_ERROR,
            exit_code=2,
        )
    return _render_markdown(sheet)


def _render_markdown(sheet: CommitAuditSheetRecord) -> str:
    lines: list[str] = [
        f"# Commit audit sheet: {sheet.release_version}",
        "",
        f"- range: `{sheet.git_range}`" if sheet.git_range else "- range: (unknown)",
        f"- revision: {sheet.versioning.revision}",
    ]
    if sheet.git_base_sha and sheet.git_head_sha:
        lines.append(
            f"- base: `{sheet.git_base_ref or sheet.git_base_sha[:7]}` "
            f"({sheet.git_base_sha[:7]})"
        )
        lines.append(
            f"- head: `{sheet.git_head_ref or sheet.git_head_sha[:7]}` "
            f"({sheet.git_head_sha[:7]})"
        )
    lines.append(f"- commit count: {sheet.commit_count}")
    lines.append(f"- rows: {len(sheet.rows)}")
    lines.append("")
    lines.append(
        "| sha | inspected | paths inspected | observed behavior | "
        "decision | target entry |"
    )
    lines.append("|---|---:|---|---|---|---|")
    for row in sheet.rows:
        paths = ", ".join(row.inspected_paths) or "-"
        behavior = (row.observed_behavior or "").replace("|", "\\|").strip()
        if len(behavior) > 80:
            behavior = behavior[:77] + "..."
        target = row.target_entry_id or row.target_entry_key or "-"
        lines.append(
            f"| `{row.short_sha or row.sha[:7]}` "
            f"| {'yes' if row.inspected else 'no'} "
            f"| {paths} | {behavior} | {row.decision} | {target} |"
        )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _normalize_summary(value: str) -> str:
    """Lowercase, strip punctuation/whitespace for subject-summary comparison."""
    cleaned = value.lower()
    cleaned = re.sub(rf"[{re.escape(string.punctuation)}\s]+", "", cleaned)
    return cleaned


def subject_matches_evidence_subject(summary: str, evidence_subject: str) -> bool:
    """Return True when ``summary`` equals or trivially transforms the subject.

    Catches exact matches and case/punctuation-only variants. A trivial
    title-case transform is also caught.
    """
    if not summary.strip() or not evidence_subject.strip():
        return False
    if _normalize_summary(summary) == _normalize_summary(evidence_subject):
        return True
    # Trivial title-case variant: "feat: add x" vs "Feat: Add X".
    if summary.strip().title().lower() == evidence_subject.strip().lower():
        return True
    return False


def _accepted_source_refs(
    entries: list[ReleaseEntryRecord],
    *,
    visible_internal: bool,
) -> set[str]:
    refs: set[str] = set()
    for entry in entries:
        if entry.status != "accepted":
            continue
        if entry.internal and not visible_internal:
            continue
        refs.update(entry.source_refs)
    return refs


def _analyze_coverage(
    sheet: CommitAuditSheetRecord,
    entries: list[ReleaseEntryRecord],
    *,
    include_internal: bool,
) -> dict[str, list[str]]:
    """Compute per-row coverage gaps and subject-summary violations."""
    public_refs = _accepted_source_refs(entries, visible_internal=False)
    internal_refs = _accepted_source_refs(entries, visible_internal=True) - public_refs
    missing: list[str] = []
    internal_only: list[str] = []
    for row in sheet.rows:
        ref = row.source_ref
        if row.decision in ("accepted", "grouped"):
            if ref not in public_refs:
                missing.append(ref)
        elif row.decision == "internal":
            if include_internal and ref not in internal_refs:
                missing.append(ref)
            elif (
                not include_internal and ref in internal_refs and ref not in public_refs
            ):
                internal_only.append(ref)
    internal_missing = [
        r.source_ref
        for r in sheet.rows
        if r.decision == "internal"
        and include_internal
        and r.source_ref not in internal_refs
    ]
    subjects = {
        r.source_ref: r.evidence_subject for r in sheet.rows if r.evidence_subject
    }
    violations: list[str] = []
    for entry in entries:
        if entry.status != "accepted":
            continue
        for ref in entry.source_refs:
            subject = subjects.get(ref)
            if subject and subject_matches_evidence_subject(entry.summary, subject):
                violations.append(ref)
    return {
        "missing_entry_coverage": missing,
        "internal_only": internal_only,
        "internal_missing": internal_missing,
        "subject_summary_violations": violations,
    }


def project_audit_entry_coverage(
    sheet: CommitAuditSheetRecord,
    entries: list[ReleaseEntryRecord],
    *,
    include_internal: bool = False,
) -> dict[str, object]:
    """Project audit coverage for an in-memory entry set."""
    coverage = _analyze_coverage(sheet, entries, include_internal=include_internal)
    covered_refs = [
        row.source_ref
        for row in sheet.rows
        if row.source_ref
        not in set(coverage["missing_entry_coverage"])
        | set(coverage["internal_missing"])
    ]
    return {
        "covered_refs": covered_refs,
        "missing_refs": coverage["missing_entry_coverage"],
        "internal_missing_refs": coverage["internal_missing"],
    }


def _row_issue(
    row: CommitAuditRow,
    *,
    field: str,
    code: str,
    message: str,
) -> dict[str, object]:
    return {
        "sha": row.sha,
        "source_ref": row.source_ref,
        "field": field,
        "code": code,
        "message": message,
    }


def _collect_row_issues(
    sheet: CommitAuditSheetRecord,
) -> list[dict[str, object]]:
    """Collect issues arising from individual sheet rows."""
    issues: list[dict[str, object]] = []
    for row in sheet.rows:
        if not row.inspected:
            issues.append(
                _row_issue(
                    row,
                    field="inspected",
                    code="uninspected",
                    message="Row must be marked inspected.",
                )
            )
        if row.decision == "needs_review":
            issues.append(
                _row_issue(
                    row,
                    field="decision",
                    code="needs_review",
                    message="Row decision must move beyond needs_review.",
                )
            )
        if row.inspected and not row.inspected_paths:
            issues.append(
                _row_issue(
                    row,
                    field="inspected_paths",
                    code="missing_inspected_paths",
                    message="Inspected rows must record inspected_paths.",
                )
            )
        if row.decision in _COMPLETED_DECISIONS and not row.observed_behavior.strip():
            issues.append(
                _row_issue(
                    row,
                    field="observed_behavior",
                    code="empty_observed_behavior",
                    message="Completed rows require observed_behavior text.",
                )
            )
    return issues


def _collect_coverage_issues(
    *,
    phase: str,
    missing_entry_coverage: list[str],
    internal_missing: list[str],
    violations: list[str],
) -> list[dict[str, object]]:
    """Collect issues that only apply when phase is ``complete``."""
    if phase != "complete":
        return []
    issues: list[dict[str, object]] = []
    for ref in missing_entry_coverage:
        issues.append(
            {
                "source_ref": ref,
                "field": "source_refs",
                "code": "missing_entry_coverage",
                "message": f"No accepted entry covers {ref}.",
            }
        )
    for ref in internal_missing:
        issues.append(
            {
                "source_ref": ref,
                "field": "source_refs",
                "code": "missing_internal_entry_coverage",
                "message": f"No accepted internal entry covers {ref}.",
            }
        )
    for ref in violations:
        issues.append(
            {
                "source_ref": ref,
                "field": "summary",
                "code": "summary_matches_commit_subject",
                "message": (f"An accepted entry summary matches commit subject {ref}."),
            }
        )
    return issues


def _build_checks(
    *,
    issues: list[dict[str, object]],
    phase: str,
    missing_entry_coverage: list[str],
    internal_missing: list[str],
    violations: list[str],
    uninspected: list[CommitAuditRow],
    needs_review: list[CommitAuditRow],
) -> dict[str, bool]:
    return {
        "all_rows_inspected": len(uninspected) == 0,
        "all_rows_decided": len(needs_review) == 0,
        "all_inspected_rows_have_paths": not any(
            issue["code"] == "missing_inspected_paths" for issue in issues
        ),
        "all_completed_rows_have_observed_behavior": not any(
            issue["code"] == "empty_observed_behavior" for issue in issues
        ),
        "all_public_decisions_covered": (
            phase != "complete" or len(missing_entry_coverage) == 0
        ),
        "all_internal_decisions_covered_when_requested": (
            phase != "complete" or len(internal_missing) == 0
        ),
        "no_summary_matches_commit_subject": (
            phase != "complete" or len(violations) == 0
        ),
    }


def _collect_strict_blockers(
    *,
    issues: list[dict[str, object]],
    phase: str,
    missing_entry_coverage: list[str],
    internal_missing: list[str],
    violations: list[str],
    uninspected: list[CommitAuditRow],
    needs_review: list[CommitAuditRow],
) -> list[str]:
    blockers: list[str] = []
    if needs_review:
        blockers.append(f"{len(needs_review)} row(s) need review")
    if uninspected:
        blockers.append(f"{len(uninspected)} row(s) not inspected")
    if any(issue["code"] == "missing_inspected_paths" for issue in issues):
        blockers.append("inspected row(s) are missing inspected_paths")
    if any(issue["code"] == "empty_observed_behavior" for issue in issues):
        blockers.append("completed row(s) are missing observed_behavior")
    if phase == "complete" and missing_entry_coverage:
        blockers.append(
            f"{len(missing_entry_coverage)} row(s) lack accepted entry coverage"
        )
    if phase == "complete" and internal_missing:
        blockers.append(
            f"{len(internal_missing)} internal row(s) lack accepted entry coverage"
        )
    if phase == "complete" and violations:
        blockers.append(f"{len(violations)} entry summary/ies match a commit subject")
    return blockers


def validate_commit_audit_sheet(
    workspace_root: Path,
    *,
    version: str,
    phase: str = "complete",
    strict: bool = False,
    include_internal: bool = False,
    record_event: bool = False,
) -> dict[str, object]:
    """Cross-check the audit sheet against release entries and git coverage.

    Returns a JSON-friendly ``commit_audit_validation`` report. In strict mode
    raises :class:`LaunchError` when any blocking condition holds.
    """
    workspace_root = workspace_root.expanduser().resolve()
    resolve_project_paths(workspace_root)
    sheet = load_commit_audit_sheet(workspace_root, version)
    if sheet is None:
        raise LaunchError(
            f"No commit audit sheet for {version}.",
            code=CODE_NOT_FOUND,
            exit_code=2,
        )
    entries = load_entries(workspace_root, version)
    if phase not in {"evidence", "complete"}:
        raise LaunchError(
            f"Unsupported audit validation phase {phase!r}. Use evidence or complete.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    coverage = _analyze_coverage(sheet, entries, include_internal=include_internal)

    needs_review = [r for r in sheet.rows if r.decision == "needs_review"]
    uninspected = [r for r in sheet.rows if not r.inspected]
    missing_entry_coverage = coverage["missing_entry_coverage"]
    internal_only = coverage["internal_only"]
    internal_missing = coverage["internal_missing"]
    violations = coverage["subject_summary_violations"]
    issues = _collect_row_issues(sheet)
    issues.extend(
        _collect_coverage_issues(
            phase=phase,
            missing_entry_coverage=missing_entry_coverage,
            internal_missing=internal_missing,
            violations=violations,
        )
    )
    checks = _build_checks(
        issues=issues,
        phase=phase,
        missing_entry_coverage=missing_entry_coverage,
        internal_missing=internal_missing,
        violations=violations,
        uninspected=uninspected,
        needs_review=needs_review,
    )
    ok = not issues

    report: dict[str, object] = {
        "kind": "commit_audit_validation",
        "version": version,
        "phase": phase,
        "ok": ok,
        "row_count": len(sheet.rows),
        "needs_review_count": len(needs_review),
        "uninspected_count": len(uninspected),
        "missing_entry_coverage": missing_entry_coverage,
        "internal_missing": internal_missing,
        "subject_summary_violations": violations,
        "internal_only": internal_only,
        "checks": checks,
        "issues": issues,
    }
    if strict:
        blockers = _collect_strict_blockers(
            issues=issues,
            phase=phase,
            missing_entry_coverage=missing_entry_coverage,
            internal_missing=internal_missing,
            violations=violations,
            uninspected=uninspected,
            needs_review=needs_review,
        )
        if blockers:
            if record_event:
                append_event(
                    workspace_root,
                    event="audit.validated",
                    release_version=version,
                    record_revisions={"commit_audit_sheet": sheet.versioning.revision},
                    data={"ok": False, "phase": phase, "blockers": blockers},
                )
            raise LaunchError(
                f"Strict audit validation failed for {version}: "
                + "; ".join(blockers)
                + ".",
                code=CODE_VALIDATION_ERROR,
                exit_code=2,
                data={"phase": phase, "issues": issues},
            )
    if record_event:
        append_event(
            workspace_root,
            event="audit.validated",
            release_version=version,
            record_revisions={"commit_audit_sheet": sheet.versioning.revision},
            data={"ok": ok, "phase": phase},
        )
    return report


# ---------------------------------------------------------------------------
# Sync targets from entries
# ---------------------------------------------------------------------------


def sync_audit_targets_from_entries(
    workspace_root: Path,
    *,
    version: str,
) -> dict[str, object]:
    """Fill ``target_entry_id`` on rows whose source ref an entry covers.

    Returns a JSON-friendly ``commit_audit_sync`` report with the count of rows
    updated. Emits an ``audit.updated`` event when the sheet changed.
    """
    workspace_root = workspace_root.expanduser().resolve()
    resolve_project_paths(workspace_root)
    existing = load_commit_audit_sheet(workspace_root, version)
    if existing is None:
        raise LaunchError(
            f"No commit audit sheet for {version}.",
            code=CODE_NOT_FOUND,
            exit_code=2,
        )
    entries = load_entries(workspace_root, version)
    updated, changed = sync_audit_sheet_targets(existing, entries)
    if not changed:
        return {
            "kind": "commit_audit_sync",
            "version": version,
            "updated_rows": 0,
            "revision": existing.versioning.revision,
        }
    saved = save_commit_audit_sheet(workspace_root, updated, overwrite=True)
    append_event(
        workspace_root,
        event="audit.updated",
        release_version=version,
        record_revisions={
            "commit_audit_sheet": saved.versioning.revision,
        },
        data={"synced_rows": changed},
    )
    return {
        "kind": "commit_audit_sync",
        "version": version,
        "updated_rows": changed,
        "revision": saved.versioning.revision,
    }


def sync_audit_sheet_targets(
    existing: CommitAuditSheetRecord,
    entries: list[ReleaseEntryRecord],
) -> tuple[CommitAuditSheetRecord, int]:
    """Return an updated audit sheet with target_entry_id filled from entries."""
    ref_to_entry: dict[str, str] = {}
    for entry in entries:
        for ref in entry.source_refs:
            ref_to_entry.setdefault(ref, entry.entry_id)
    changed = 0
    new_rows: list[CommitAuditRow] = []
    for row in existing.rows:
        target = ref_to_entry.get(row.source_ref)
        if target and target != row.target_entry_id:
            new_rows.append(replace(row, target_entry_id=target))
            changed += 1
        else:
            new_rows.append(row)
    candidate = CommitAuditSheetRecord(
        release_version=existing.release_version,
        versioning=initial_versioning(),
        git_base_ref=existing.git_base_ref,
        git_base_sha=existing.git_base_sha,
        git_head_ref=existing.git_head_ref,
        git_head_sha=existing.git_head_sha,
        git_range=existing.git_range,
        commit_count=existing.commit_count,
        rows=tuple(new_rows),
    )
    return _sheet_with_next_versioning(existing, candidate), changed


# Re-export enum vocabularies for CLI help/validation.
DECISIONS = AUDIT_DECISIONS
PUBLIC_IMPACTS = AUDIT_PUBLIC_IMPACTS


def collect_commit_subjects(
    workspace_root: Path,
    *,
    version: str,
) -> list[str]:
    """Return commit subjects to guard against in entry summaries.

    Prefers the audit sheet's ``evidence_subject`` values; falls back to the
    release's stored git range when no audit sheet exists.
    """
    sheet = load_commit_audit_sheet(workspace_root, version)
    if sheet is not None:
        return [r.evidence_subject for r in sheet.rows if r.evidence_subject]
    # Fall back to the stored release git range.
    release = load_release(workspace_root, version)
    try:
        snapshot = resolve_release_snapshot(workspace_root, release)
    except LaunchError:
        return []
    try:
        candidates = collect_git_candidates(
            workspace_root,
            base_ref=snapshot.base_spec,
            head_ref=snapshot.head_spec,
            include_merges=GIT_DEFAULT_INCLUDE_MERGES,
        )
    except LaunchError:
        return []
    return [c.subject for c in candidates if c.subject]


def guard_entry_summaries(
    summaries: list[str],
    subjects: list[str],
) -> list[str]:
    """Return the list of summaries that match a commit subject.

    Used by ``entry add-many --guard-commit-subjects`` to reject batches that
    copy or trivially transform a commit subject into an entry summary.
    """
    violations: list[str] = []
    for summary in summaries:
        for subject in subjects:
            if subject_matches_evidence_subject(summary, subject):
                violations.append(summary)
                break
    return violations
