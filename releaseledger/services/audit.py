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
from pathlib import Path

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
from releaseledger.domain.versioning import bump_versioning, initial_versioning
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
)
from releaseledger.storage.paths import resolve_project_paths
from releaseledger.storage.store import (
    load_commit_audit_sheet,
    load_entries,
    load_release,
    save_commit_audit_sheet,
)

__all__ = [
    "create_commit_audit_sheet",
    "render_commit_audit_sheet",
    "sync_audit_targets_from_entries",
    "update_commit_audit_sheet",
    "validate_commit_audit_sheet",
    "subject_matches_evidence_subject",
]


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
) -> tuple[str, str]:
    """Resolve the (base_ref, head_ref) pair for the audit sheet.

    Prefers explicit args, then stored release git metadata.
    """
    if git_base and git_head:
        return git_base, git_head
    if git_base and not git_head:
        return git_base, GIT_DEFAULT_HEAD
    release = load_release(workspace_root, version)
    base = git_base or release.git_base_ref
    head = git_head or release.git_head_ref
    if not base or not head:
        raise LaunchError(
            f"No git range for {version}. Pass --base/--head, or store the "
            "release range first.",
            code=CODE_USAGE_ERROR,
            exit_code=2,
            remediation=[
                f"Run `releaseledger release update {version} --git-base PREV "
                "--git-head HEAD` first, or pass --base/--head."
            ],
        )
    return base, head


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
    base_ref, head_ref = _resolve_range(
        workspace_root, version=version, git_base=git_base, git_head=git_head
    )
    summary = build_git_range_summary(
        workspace_root,
        base_ref=base_ref,
        head_ref=head_ref,
        include_merges=GIT_DEFAULT_INCLUDE_MERGES,
    )
    candidates = collect_git_candidates(
        workspace_root,
        base_ref=base_ref,
        head_ref=head_ref,
        include_merges=GIT_DEFAULT_INCLUDE_MERGES,
    )
    rows = tuple(_row_from_candidate(c) for c in candidates)
    # Preserve the intended short SHA spelling from git.
    sheet = CommitAuditSheetRecord(
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
    import ledgercore

    raw = ledgercore.load_yaml_object(file, label=f"audit update {version}")
    if not isinstance(raw, dict):
        raise LaunchError(
            f"Audit update file {file} must contain a mapping.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    incoming = audit_sheet_from_dict(dict(raw))
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
    updated = CommitAuditSheetRecord(
        release_version=incoming.release_version,
        versioning=bump_versioning(existing.versioning)
        if audit_sheet_to_dict(_strip_revision(incoming))
        != audit_sheet_to_dict(_strip_revision(existing))
        else existing.versioning,
        git_base_ref=incoming.git_base_ref or existing.git_base_ref,
        git_base_sha=incoming.git_base_sha or existing.git_base_sha,
        git_head_ref=incoming.git_head_ref or existing.git_head_ref,
        git_head_sha=incoming.git_head_sha or existing.git_head_sha,
        git_range=incoming.git_range or existing.git_range,
        commit_count=incoming.commit_count or existing.commit_count,
        rows=new_rows,
    )
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
        "revision": saved.versioning.revision,
    }


def _strip_revision(record: CommitAuditSheetRecord) -> CommitAuditSheetRecord:
    from dataclasses import replace

    return replace(record, versioning=initial_versioning())


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
    if format_name != "markdown":
        raise LaunchError(
            f"Unsupported --format {format_name!r}. Use markdown or json.",
            code=CODE_USAGE_ERROR,
            exit_code=2,
        )
    return _render_markdown(sheet)


def _render_markdown(sheet: CommitAuditSheetRecord) -> str:
    lines: list[str] = [
        f"# Commit audit sheet: {sheet.release_version}",
        "",
        f"- range: `{sheet.git_range}`" if sheet.git_range else "- range: (unknown)",
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


def validate_commit_audit_sheet(
    workspace_root: Path,
    *,
    version: str,
    strict: bool = False,
    include_internal: bool = False,
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
    coverage = _analyze_coverage(sheet, entries, include_internal=include_internal)

    row_count = len(sheet.rows)
    needs_review = [r for r in sheet.rows if r.decision == "needs_review"]
    uninspected = [r for r in sheet.rows if not r.inspected]
    missing_entry_coverage = coverage["missing_entry_coverage"]
    internal_only = coverage["internal_only"]
    internal_missing = coverage["internal_missing"]
    violations = coverage["subject_summary_violations"]

    checks = {
        "all_rows_inspected": len(uninspected) == 0,
        "all_public_decisions_covered": len(missing_entry_coverage) == 0,
        "all_internal_decisions_covered_when_requested": len(internal_missing) == 0,
        "no_summary_matches_commit_subject": len(violations) == 0,
    }
    ok = all(checks.values()) and not needs_review

    report: dict[str, object] = {
        "kind": "commit_audit_validation",
        "version": version,
        "ok": ok,
        "row_count": row_count,
        "needs_review_count": len(needs_review),
        "uninspected_count": len(uninspected),
        "missing_entry_coverage": missing_entry_coverage,
        "subject_summary_violations": violations,
        "internal_only": internal_only,
        "checks": checks,
    }
    if strict:
        blockers: list[str] = []
        if needs_review:
            blockers.append(f"{len(needs_review)} row(s) need review")
        if uninspected:
            blockers.append(f"{len(uninspected)} row(s) not inspected")
        if missing_entry_coverage:
            blockers.append(
                f"{len(missing_entry_coverage)} row(s) lack accepted entry coverage"
            )
        if violations:
            blockers.append(
                f"{len(violations)} entry summary/ies match a commit subject"
            )
        if blockers:
            append_event(
                workspace_root,
                event="audit.validated",
                release_version=version,
                record_revisions={"commit_audit_sheet": sheet.versioning.revision},
                data={"ok": False, "blockers": blockers},
            )
            raise LaunchError(
                f"Strict audit validation failed for {version}: "
                + "; ".join(blockers)
                + ".",
                code=CODE_VALIDATION_ERROR,
                exit_code=2,
            )
    append_event(
        workspace_root,
        event="audit.validated",
        release_version=version,
        record_revisions={"commit_audit_sheet": sheet.versioning.revision},
        data={"ok": ok},
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
    ref_to_entry: dict[str, str] = {}
    for entry in entries:
        for ref in entry.source_refs:
            ref_to_entry.setdefault(ref, entry.entry_id)
    changed = 0
    new_rows: list[CommitAuditRow] = []
    for row in existing.rows:
        target = ref_to_entry.get(row.source_ref)
        if target and target != row.target_entry_id:
            from dataclasses import replace

            new_rows.append(replace(row, target_entry_id=target))
            changed += 1
        else:
            new_rows.append(row)
    if not changed:
        return {
            "kind": "commit_audit_sync",
            "version": version,
            "updated_rows": 0,
            "revision": existing.versioning.revision,
        }
    updated = CommitAuditSheetRecord(
        release_version=existing.release_version,
        versioning=bump_versioning(existing.versioning),
        git_base_ref=existing.git_base_ref,
        git_base_sha=existing.git_base_sha,
        git_head_ref=existing.git_head_ref,
        git_head_sha=existing.git_head_sha,
        git_range=existing.git_range,
        commit_count=existing.commit_count,
        rows=tuple(new_rows),
    )
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
    base = release.git_base_ref
    head = release.git_head_ref
    if not base or not head:
        return []
    try:
        candidates = collect_git_candidates(
            workspace_root,
            base_ref=base,
            head_ref=head,
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
