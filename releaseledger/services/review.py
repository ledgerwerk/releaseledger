"""Release review service.

Combines release state, entry coverage, orphan detection, entry lint, and a
strict changelog dry-run into one deterministic, read-only report so agents and
humans do not need to manually stitch together ``release show``, ``entry list``,
``entry lint``, ``changelog``, and ``build --dry-run``.

The function :func:`build_release_review` never mutates releaseledger state and
never writes the changelog target file. Git hashes remain optional evidence
carried on entry ``sources``; :class:`~releaseledger.domain.entry.ReleaseEntryRecord`
``source_refs`` plus entry ``status`` are the canonical change identity.
"""

from __future__ import annotations

from pathlib import Path

from releaseledger.domain.entry import ReleaseEntryRecord, normalize_entry_status
from releaseledger.domain.release import ReleaseRecord
from releaseledger.domain.source_ref import is_coverable_boundary_ref
from releaseledger.errors import LaunchError
from releaseledger.services.changelog_build import (
    build_changelog_file,
    render_changelog_section,
)
from releaseledger.services.entry_lint import lint_release_entries
from releaseledger.services.git_sources import (
    release_snapshot_drift_report,
    resolve_release_snapshot,
)
from releaseledger.services.releases import check_release_chain, reconcile_releases
from releaseledger.storage.store import (
    load_commit_audit_sheet,
    load_entries,
    load_release,
)

__all__ = [
    "build_release_review",
    "classify_source_ref",
    "compute_entry_fingerprint",
]

# Coverage classification labels, ordered from strongest to weakest.
COVERAGE_COVERED = "covered"
COVERAGE_DRAFT_ONLY = "draft_only"
COVERAGE_REJECTED_ONLY = "rejected_only"
COVERAGE_INTERNAL_ONLY = "internal_only"
COVERAGE_MISSING = "missing"


def compute_entry_fingerprint(entry: ReleaseEntryRecord) -> str:
    """Return a stable fingerprint for fallback duplicate detection.

    The fingerprint normalizes ``kind``, ``summary``, sorted ``source_refs``,
    and sorted ``paths``. Stable upstream refs (``source_refs``) are stronger
    than this fingerprint; it is a fallback only.
    """
    import hashlib

    parts = [
        entry.kind.strip().lower(),
        " ".join(entry.summary.strip().split()),
        "\n".join(sorted(entry.source_refs)),
        "\n".join(sorted(entry.paths)),
    ]
    payload = "\n".join(parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _entry_visible(entry: ReleaseEntryRecord, *, include_internal: bool) -> bool:
    return include_internal or not entry.internal


def classify_source_ref(
    ref: str,
    matching: list[ReleaseEntryRecord],
    *,
    include_internal: bool,
) -> tuple[str, dict[str, list[str]]]:
    """Classify a single expected source ref against its matching entries.

    Classification reflects recorded entry state (it is independent of the
    ``include_statuses`` scope, which only affects lint/build/counts). Internal
    entries are visible only when ``include_internal`` is set.

    Returns ``(status_label, entry_id_breakdown)``. The breakdown keys are
    ``entry_ids``, ``accepted_entry_ids``, ``draft_entry_ids``, and
    ``rejected_entry_ids`` (each sorted and de-duplicated).
    """
    accepted: list[str] = []
    draft: list[str] = []
    rejected: list[str] = []
    accepted_visible = False
    only_internal = bool(matching)
    for entry in matching:
        if entry.status == "accepted":
            accepted.append(entry.entry_id)
            if _entry_visible(entry, include_internal=include_internal):
                accepted_visible = True
                only_internal = False
            elif not entry.internal:
                only_internal = False
        elif entry.status == "draft":
            draft.append(entry.entry_id)
            only_internal = False
        elif entry.status == "rejected":
            rejected.append(entry.entry_id)
            only_internal = False
    if accepted_visible:
        label = COVERAGE_COVERED
    elif only_internal:
        label = COVERAGE_INTERNAL_ONLY
    elif accepted:
        # Accepted entries exist but none are visible (all internal). This is
        # the same shape as internal_only, but keep the label explicit.
        label = COVERAGE_INTERNAL_ONLY
    elif draft:
        label = COVERAGE_DRAFT_ONLY
    elif rejected:
        label = COVERAGE_REJECTED_ONLY
    else:
        label = COVERAGE_MISSING
    breakdown: dict[str, list[str]] = {
        "entry_ids": _dedupe_preserve_order(accepted + draft + rejected),
        "accepted_entry_ids": _dedupe_preserve_order(accepted),
        "draft_entry_ids": _dedupe_preserve_order(draft),
        "rejected_entry_ids": _dedupe_preserve_order(rejected),
    }
    return label, breakdown


def _is_orphan(entry: ReleaseEntryRecord) -> bool:
    return not (entry.source_refs or entry.issues or entry.prs or entry.sources)


def _coverage_recommendation(ref: str, label: str) -> str | None:
    if label == COVERAGE_MISSING:
        return (
            f"Add an accepted entry covering {ref} or remove it from release"
            " source refs."
        )
    if label == COVERAGE_DRAFT_ONLY:
        return f"Review draft entry for {ref} and set status to accepted or rejected."
    if label == COVERAGE_REJECTED_ONLY:
        return f"Confirm {ref} is intentionally omitted; its only entry is rejected."
    if label == COVERAGE_INTERNAL_ONLY:
        return (
            f"{ref} is only covered by internal entries; expose an accepted"
            " user-facing entry or include internal entries."
        )
    return None


def _lint_summary(result: dict[str, object]) -> dict[str, object]:
    summary = result.get("summary", {})
    if not isinstance(summary, dict):
        return {"errors": 0, "warnings": 0}
    return {
        "errors": int(summary.get("errors", 0)),
        "warnings": int(summary.get("warnings", 0)),
    }


def _compute_git_expected_refs(
    workspace_root: Path,
    *,
    release: ReleaseRecord,
    expected_refs: list[str],
    git: bool,
    git_base: str | None,
    git_head: str | None,
    include_merges: str,
) -> tuple[dict[str, object] | None, list[str], dict[str, object], list[str]]:
    """Compute git-derived expected refs for review (design §10.2).

    Returns (git_block, git_warnings, git_ref_map, updated_expected_refs).
    When git is not enabled or no range can be resolved, returns
    (None, [], {}, expected_refs_unchanged).
    """
    from releaseledger.services.git_sources import (
        build_git_range_summary,
        collect_git_candidates,
        is_git_worktree,
    )

    git_block: dict[str, object] | None = None
    git_warnings: list[str] = []
    git_ref_map: dict[str, object] = {}
    git_enabled = git
    base_to_use: str | None = git_base
    head_to_use: str | None = git_head

    # Auto-enable git when the release has stored git metadata and the
    # workspace is a git worktree.
    if (
        not git_enabled
        and release.git_base_sha is not None
        and release.git_head_sha is not None
    ):
        git_enabled = is_git_worktree(workspace_root)

    if not git_enabled:
        return git_block, git_warnings, git_ref_map, expected_refs

    try:
        snapshot = resolve_release_snapshot(
            workspace_root,
            release,
            explicit_base=base_to_use,
            explicit_head=head_to_use,
        )
        candidates = collect_git_candidates(
            workspace_root,
            base_ref=snapshot.base_spec,
            head_ref=snapshot.head_spec,
            include_merges=include_merges,
        )
        summary = build_git_range_summary(
            workspace_root,
            base_ref=snapshot.base_spec,
            head_ref=snapshot.head_spec,
            include_merges=include_merges,
        )
        for cand in candidates:
            if cand.include_by_default:
                if cand.source_ref not in set(expected_refs):
                    expected_refs.append(cand.source_ref)
                git_ref_map[cand.source_ref] = cand
        expected_refs = _dedupe_preserve_order(expected_refs)
        _ms = summary.get("merge_commits_skipped", 0)
        merge_skipped = _ms if isinstance(_ms, int) else 0
        _tc = summary.get("commit_count", 0)
        total_commits = _tc if isinstance(_tc, int) else 0
        git_block = {
            "base_ref": snapshot.base_ref,
            "base_sha": summary.get("base_sha"),
            "head_ref": snapshot.head_ref,
            "head_sha": summary.get("head_sha"),
            "range": summary.get("range"),
            "commit_count": total_commits,
            "merge_commits_skipped": merge_skipped,
            "candidate_count": len(candidates),
            "include_merges": include_merges,
            "snapshot_source": snapshot.source,
        }
        drift = release_snapshot_drift_report(workspace_root, release)
        if drift is not None:
            git_block["snapshot_drift"] = drift
            if drift.get("status") == "drifted":
                git_warnings.append(
                    "Stored release snapshot drifted from its symbolic refs; "
                    "review used the pinned stored SHAs."
                )
        if merge_skipped:
            git_warnings.append(
                f"{merge_skipped} merge commit(s) excluded by"
                f" include_merges={include_merges} policy."
            )
    except LaunchError as exc:
        git_warnings.append(f"Git range scan failed: {exc.message}")
    return git_block, git_warnings, git_ref_map, expected_refs


def _compute_git_coverage(
    coverage: list[dict[str, object]],
    git_block: dict[str, object] | None,
) -> tuple[bool, int]:
    """Compute git coverage status and missing count for review checks."""
    if git_block is None:
        return True, 0
    git_coverage_ok = all(
        bool(row.get("gate_satisfied", row["status"] == COVERAGE_COVERED))
        for row in coverage
        if str(row.get("source_ref", "")).startswith("git:")
    )
    git_missing_count = sum(
        1
        for row in coverage
        if str(row.get("source_ref", "")).startswith("git:")
        and not bool(row.get("gate_satisfied", row["status"] == COVERAGE_COVERED))
    )
    return git_coverage_ok, git_missing_count


def _problem_next_action(
    problem: dict[str, object], *, target_file: str, version: str
) -> dict[str, str] | None:
    """Return one deterministic remediation for a reconciliation problem."""
    kind = str(problem.get("kind", ""))
    problem_version = str(problem.get("version", version))
    if kind == "changelog_without_release":
        return {
            "code": "remove_stale_changelog_section",
            "command": (
                "releaseledger changelog-section remove-section "
                f"{problem_version} --target-file {target_file}"
            ),
        }
    if kind == "release_without_tag":
        return {
            "code": "reconcile_release_tag",
            "command": "releaseledger release reconcile --strict",
        }
    if kind in {"released_without_changelog", "release_changelog_date_mismatch"}:
        return {
            "code": "rebuild_release_changelog",
            "command": (
                f"releaseledger changelog build {problem_version} "
                f"--output {target_file} --strict --replace-existing"
            ),
        }
    if kind in {
        "missing_previous",
        "future_previous",
        "root_has_previous",
        "noncanonical_previous",
    }:
        return {
            "code": "repair_release_chain",
            "command": "releaseledger release reconcile --strict",
        }
    return None


def _build_next_actions(
    *,
    version: str,
    target_file: Path | None,
    chain: dict[str, object],
    reconciliation: dict[str, object],
    audit: dict[str, object] | None,
    changelog: dict[str, object],
    checks: dict[str, object],
) -> list[dict[str, str]]:
    """Build stable, machine-actionable release-check next actions."""
    target_display = str(target_file or "CHANGELOG.md")
    actions: list[dict[str, str]] = []

    for block in (chain, reconciliation):
        problems = block.get("problems", [])
        if not isinstance(problems, list):
            continue
        for problem in problems:
            if not isinstance(problem, dict):
                continue
            action = _problem_next_action(
                problem, target_file=target_display, version=version
            )
            if action is not None and action not in actions:
                actions.append(action)

    if not bool(checks.get("changelog_ok", True)):
        action = {
            "code": "resolve_changelog_dry_run",
            "command": (
                f"releaseledger changelog build {version} --output "
                f"{target_display} --strict"
            ),
        }
        if action not in actions:
            actions.append(action)
    if audit is not None and not bool(audit.get("ok", False)):
        action = {
            "code": "complete_commit_audit",
            "command": f"releaseledger audit validate {version} --phase evidence --strict",
        }
        if action not in actions:
            actions.append(action)
    if not bool(checks.get("lint_ok", True)):
        action = {
            "code": "fix_entry_lint",
            "command": f"releaseledger entry lint {version}",
        }
        if action not in actions:
            actions.append(action)
    return actions


def _failed_checks(
    *,
    checks: dict[str, object],
    audit: dict[str, object] | None,
    git_block: dict[str, object] | None,
    strict: bool,
) -> list[str]:
    """Return stable identifiers for failed gates included in ``ok``."""
    failed: list[str] = []
    always = (
        ("entry_coverage", "coverage_ok"),
        ("entry_lint", "lint_ok"),
    )
    for identifier, key in always:
        if not bool(checks.get(key, False)):
            failed.append(identifier)
    if strict:
        for identifier, key in (
            ("changelog", "changelog_ok"),
            ("release_state", "release_state_ok"),
            ("chain", "chain_ok"),
            ("reconciliation", "reconciliation_ok"),
        ):
            if not bool(checks.get(key, False)):
                failed.append(identifier)
        if git_block is not None and not bool(checks.get("git_coverage_ok", True)):
            failed.append("git_coverage")
    if audit is not None:
        if not bool(checks.get("audit_evidence_ok", False)):
            failed.append("audit_evidence")
        if not bool(checks.get("audit_complete_ok", False)):
            failed.append("audit_complete")
    if not bool(checks.get("snapshot_ok", True)):
        failed.append("snapshot")
    return failed


def _phase_reconciliation(
    block: dict[str, object], *, phase: str
) -> dict[str, object]:
    """Apply release-check phase severity to reconciliation findings."""
    raw_problems = block.get("problems", [])
    problems = [problem for problem in raw_problems if isinstance(problem, dict)]
    filtered: list[dict[str, object]] = []
    for problem in problems:
        kind = str(problem.get("kind", ""))
        if phase == "finalize" and kind == "release_without_tag":
            continue
        annotated = dict(problem)
        annotated["severity"] = "failure"
        filtered.append(annotated)
    result = dict(block)
    result["phase"] = phase
    result["problems"] = filtered
    result["problem_count"] = len(filtered)
    result["ok"] = not filtered
    return result


def _build_review_recommendations(
    *,
    coverage: list[dict[str, object]],
    orphans: list[dict[str, str]],
    lint_summary: dict[str, object],
    changelog_block: dict[str, object],
    strict: bool,
    git_warnings: list[str],
    git_block: dict[str, object] | None,
    git_coverage_ok: bool,
    git_missing_count: int,
) -> list[str]:
    """Build deterministic review recommendations."""
    recommendations: list[str] = []
    for row in coverage:
        if row.get("gate_satisfied") is True:
            continue
        rec = _coverage_recommendation(str(row["source_ref"]), str(row["status"]))
        if rec is not None:
            recommendations.append(rec)
    for orphan in orphans:
        recommendations.append(
            f"Add source refs or provenance to orphan entry {orphan['entry_id']}."
        )
    lint_errors = lint_summary.get("errors", 0)
    lint_error_count = int(lint_errors) if isinstance(lint_errors, int) else 0
    if lint_error_count > 0:
        recommendations.append(f"Fix {lint_error_count} entry lint error(s).")
    if strict and not bool(changelog_block.get("dry_run_ok", True)):
        reason = str(changelog_block.get("reason") or "changelog build")
        recommendations.append(f"Resolve strict changelog build failure: {reason}.")
    for warn in git_warnings:
        recommendations.append(warn)
    if strict and git_block is not None and not git_coverage_ok:
        recommendations.append(
            f"{git_missing_count} git commit(s) in the release range"
            " are missing accepted entry coverage."
        )
    return recommendations


def _compute_coverage(
    expected_refs: list[str],
    entries: list[ReleaseEntryRecord],
    *,
    include_internal: bool,
    git_block: dict[str, object] | None = None,
    git_ref_map: dict[str, object] | None = None,
    audit_rows: dict[str, object] | None = None,
) -> list[dict[str, object]]:
    """Build coverage rows from expected refs and entries."""
    by_ref: dict[str, list[ReleaseEntryRecord]] = {}
    for entry in entries:
        for ref in entry.source_refs:
            by_ref.setdefault(ref, []).append(entry)

    coverage: list[dict[str, object]] = []
    for ref in expected_refs:
        matching = by_ref.get(ref, [])
        label, breakdown = classify_source_ref(
            ref, matching, include_internal=include_internal
        )
        row: dict[str, object] = {
            "source_ref": ref,
            "status": label,
            **breakdown,
        }
        public_accepted_ids = [
            entry.entry_id
            for entry in matching
            if entry.status == "accepted" and not entry.internal
        ]
        internal_accepted_ids = [
            entry.entry_id
            for entry in matching
            if entry.status == "accepted" and entry.internal
        ]
        row["public_accepted_entry_ids"] = _dedupe_preserve_order(
            public_accepted_ids
        )
        row["internal_accepted_entry_ids"] = _dedupe_preserve_order(
            internal_accepted_ids
        )
        if ref.startswith("git:") and audit_rows is not None:
            audit_row = audit_rows.get(ref)
            decision = getattr(audit_row, "decision", None)
            inspected = bool(getattr(audit_row, "inspected", False))
            inspected_paths = tuple(getattr(audit_row, "inspected_paths", ()))
            observed_behavior = str(getattr(audit_row, "observed_behavior", ""))
            evidence_complete = bool(
                audit_row is not None
                and decision != "needs_review"
                and inspected
                and inspected_paths
                and observed_behavior.strip()
            )
            row["audit_decision"] = decision
            if not evidence_complete:
                row["coverage_requirement"] = "unresolved"
                row["gate_satisfied"] = False
                row["accounted_as"] = "unresolved"
            elif decision in {"accepted", "grouped"}:
                row["coverage_requirement"] = "public_entry"
                row["gate_satisfied"] = bool(public_accepted_ids)
                row["accounted_as"] = (
                    "public_entry" if public_accepted_ids else "unresolved"
                )
            elif decision == "internal":
                if include_internal:
                    row["coverage_requirement"] = "internal_entry"
                    row["gate_satisfied"] = bool(internal_accepted_ids)
                    row["accounted_as"] = (
                        "internal_entry" if internal_accepted_ids else "unresolved"
                    )
                else:
                    row["coverage_requirement"] = "none_for_public"
                    row["gate_satisfied"] = True
                    row["accounted_as"] = "internal_audit"
            elif decision == "rejected":
                row["coverage_requirement"] = "none_for_public"
                row["gate_satisfied"] = True
                row["accounted_as"] = "rejected_audit"
            else:
                row["coverage_requirement"] = "unresolved"
                row["gate_satisfied"] = False
                row["accounted_as"] = "unresolved"
        if git_block is not None:
            cand_obj = git_ref_map.get(ref) if git_ref_map is not None else None
            if ref.startswith("git:") and cand_obj is not None:
                row["provider"] = "git"
                row["summary"] = getattr(cand_obj, "subject", None)
                row["paths"] = list(getattr(cand_obj, "paths", ()))
            elif ref.startswith("git:"):
                row["provider"] = "git"
        coverage.append(row)
    return coverage


def build_release_review(
    workspace_root: Path,
    *,
    version: str,
    include_internal: bool = False,
    include_statuses: tuple[str, ...] = ("accepted",),
    target_file: Path | None = None,
    strict: bool = False,
    git: bool = False,
    git_base: str | None = None,
    git_head: str | None = None,
    include_merges: str = "nontrivial",
    require_audit_sheet: bool = False,
    include_history_health: bool = False,
    phase: str = "current",
    proposed_released_at: str | None = None,
) -> dict[str, object]:
    """Build a deterministic, read-only release review for ``version``.

    The report exposes release metadata, expected-ref coverage, entry counts,
    orphan entries, entry lint, a strict changelog dry-run verdict, top-level
    ``checks`` and ``ok`` flags, and deterministic ``recommendations``. The
    function never mutates releaseledger state and never writes the changelog.

    When ``git=True`` (or when the release has stored ``git_base_sha``/``git_head_sha``
    and the workspace is a git worktree), expected refs also include ``git:<sha>``
    for every include_by_default candidate in the range. Strict mode fails on
    uncovered include_by_default commits, warns on merge-skipped and uncertain
    kind inference.

    Raises :class:`LaunchError` when the release does not exist (re-uses
    :func:`load_release`).
    """
    if phase not in {"current", "finalize", "published"}:
        raise LaunchError(
            f"Unsupported release check phase {phase!r}. "
            "Use current, finalize, or published.",
            code="USAGE_ERROR",
            exit_code=2,
        )
    workspace_root = workspace_root.expanduser().resolve()
    release = load_release(workspace_root, version)
    statuses = tuple(normalize_entry_status(value) for value in include_statuses)
    entries = load_entries(workspace_root, version)
    audit_sheet = load_commit_audit_sheet(workspace_root, version)
    audit_rows = (
        {row.source_ref: row for row in audit_sheet.rows}
        if audit_sheet is not None
        else None
    )

    # 1. Release payload.
    release_block: dict[str, object] = {
        "version": release.version,
        "status": release.status,
        "released_at": release.released_at,
        "previous_version": release.previous_version,
        "changelog_file": release.changelog_file,
        "boundary_ref": release.boundary_ref,
        "source_refs": list(release.source_refs),
        "source_count": release.source_count,
    }

    # 2. Expected refs: release.source_refs then coverable boundary_ref, de-duped.
    #    Git range markers (git-range:*, git-tag:*, git-branch:*, git:HEAD) are NOT
    #    coverable; they are range metadata, not release-note change identities.
    expected_refs = list(release.source_refs)
    if is_coverable_boundary_ref(release.boundary_ref) and release.boundary_ref:
        expected_refs.append(release.boundary_ref)
    expected_refs = _dedupe_preserve_order(expected_refs)
    # 2b. Git-derived expected refs when git is enabled.
    git_block, git_warnings, git_ref_map, expected_refs = _compute_git_expected_refs(
        workspace_root,
        release=release,
        expected_refs=expected_refs,
        git=git,
        git_base=git_base,
        git_head=git_head,
        include_merges=include_merges,
    )

    # 3. Coverage classification.
    coverage = _compute_coverage(
        expected_refs,
        entries,
        include_internal=include_internal,
        git_block=git_block,
        git_ref_map=git_ref_map,
        audit_rows=audit_rows,
    )

    # 4. Entry counts over all recorded entries (independent of include scope).
    entry_counts: dict[str, object] = {
        "accepted": sum(1 for e in entries if e.status == "accepted"),
        "draft": sum(1 for e in entries if e.status == "draft"),
        "rejected": sum(1 for e in entries if e.status == "rejected"),
        "internal": sum(1 for e in entries if e.internal),
    }

    # 5. Orphan entries: included and visible entries with no provenance.
    orphans: list[dict[str, str]] = []
    for entry in entries:
        if entry.status not in statuses:
            continue
        if not _entry_visible(entry, include_internal=include_internal):
            continue
        if _is_orphan(entry):
            orphans.append(
                {
                    "entry_id": entry.entry_id,
                    "status": entry.status,
                    "reason": (
                        f"{entry.status} entry has no source_refs, issues,"
                        " prs, or sources"
                    ),
                }
            )

    # 6. Lint over the included scope.
    lint_result = lint_release_entries(
        workspace_root,
        release_version=version,
        strict=False,
        include_statuses=statuses,
    )
    lint_summary = _lint_summary(lint_result)
    if include_history_health:
        chain_block = check_release_chain(workspace_root)
        reconciliation_block = reconcile_releases(
            workspace_root, changelog_file=target_file
        )
    else:
        chain_block = {"kind": "release_chain_check", "ok": True, "skipped": True}
        reconciliation_block = {
            "kind": "release_reconcile",
            "ok": True,
            "skipped": True,
        }

    # 7. Changelog dry-run. Try a strict dry-run when requested so the review
    #    reports exactly what `build --strict` would reject; fall back to a
    #    non-strict dry-run render to recover a section heading and a reason.
    changelog_block = _run_changelog_dry_run(
        workspace_root,
        version=version,
        release=release,
        include_internal=include_internal,
        statuses=statuses,
        target_file=target_file,
        strict=strict,
        release_date=proposed_released_at if phase == "finalize" else None,
    )
    # Coverage is satisfied when every expected ref is covered; with no
    # expected refs, coverage is trivially satisfied.
    coverage_ok = all(
        bool(row.get("gate_satisfied", row["status"] == COVERAGE_COVERED))
        for row in coverage
    )
    lint_ok = lint_summary["errors"] == 0
    changelog_ok = bool(changelog_block.get("dry_run_ok", False))

    git_coverage_ok, git_missing_count = _compute_git_coverage(coverage, git_block)
    if phase == "finalize":
        release_state_ok = release.status in {
            "planned",
            "draft",
            "candidate",
        } and bool(proposed_released_at or release.released_at)
    elif phase == "published":
        release_state_ok = release.status == "released" and bool(
            release.released_at
        )
    else:
        release_state_ok = not (release.released_at and release.status != "released")
    chain_ok = bool(chain_block.get("ok", False))
    reconciliation_ok = bool(reconciliation_block.get("ok", False))
    snapshot_ok = not (
        isinstance(git_block, dict)
        and isinstance(git_block.get("snapshot_drift"), dict)
        and git_block["snapshot_drift"].get("status") == "drifted"
    )

    # Commit audit sheet integration (opt-in via --require-audit-sheet).
    audit_block = _build_audit_block(
        workspace_root,
        version=version,
        include_internal=include_internal,
    )
    audit_evidence_ok = True
    audit_complete_ok = True
    if audit_block is not None:
        evidence = audit_block.get("evidence", {})
        complete = audit_block.get("complete", {})
        audit_evidence_ok = isinstance(evidence, dict) and bool(evidence.get("ok"))
        audit_complete_ok = isinstance(complete, dict) and bool(complete.get("ok"))

    if phase in {"finalize", "published"}:
        reconciliation_block = _phase_reconciliation(
            reconciliation_block, phase=phase
        )
        reconciliation_ok = bool(reconciliation_block.get("ok", False))
    checks: dict[str, object] = {
        "coverage_ok": coverage_ok,
        "lint_ok": lint_ok,
        "changelog_ok": changelog_ok,
        "release_state_ok": release_state_ok,
        "chain_ok": chain_ok,
        "reconciliation_ok": reconciliation_ok,
        "snapshot_ok": snapshot_ok,
        "audit_evidence_ok": audit_evidence_ok,
        "audit_complete_ok": audit_complete_ok,
        "phase": phase,
    }
    if git_block is not None:
        checks["git_coverage_ok"] = git_coverage_ok
    ok = (
        coverage_ok
        and lint_ok
        and (
            not strict
            or (changelog_ok and release_state_ok and chain_ok and reconciliation_ok)
        )
    )
    if strict and git_block is not None:
        ok = ok and git_coverage_ok
    if audit_block is not None:
        ok = ok and audit_evidence_ok and audit_complete_ok

    recommendations = _build_review_recommendations(
        coverage=coverage,
        orphans=orphans,
        lint_summary=lint_summary,
        changelog_block=changelog_block,
        strict=strict,
        git_warnings=git_warnings,
        git_block=git_block,
        git_coverage_ok=git_coverage_ok,
        git_missing_count=git_missing_count,
    )
    if not release_state_ok:
        recommendations.append(
            f"{release.version} has released_at={release.released_at}"
            f" but status={release.status}."
        )
    if not chain_ok:
        recommendations.append(
            "Repair the release predecessor chain before finalization."
        )
    if not reconciliation_ok:
        recommendations.append(
            "Run release reconcile and resolve release/tag/changelog mismatches."
        )

    failed_checks = _failed_checks(
        checks=checks,
        audit=audit_block,
        git_block=git_block,
        strict=strict,
    )
    next_actions = _build_next_actions(
        version=version,
        target_file=target_file,
        chain=chain_block,
        reconciliation=reconciliation_block,
        audit=audit_block,
        changelog=changelog_block,
        checks=checks,
    )

    result: dict[str, object] = {
        "kind": "release_review",
        "version": version,
        "release": release_block,
        "entry_counts": entry_counts,
        "coverage": coverage,
        "orphan_entries": orphans,
        "lint": lint_summary,
        "changelog": changelog_block,
        "checks": checks,
        "ok": ok,
        "strict": strict,
        "include_internal": bool(include_internal),
        "include_statuses": list(statuses),
        "phase": phase,
        "proposed_released_at": proposed_released_at,
        "recommendations": recommendations,
        "chain": chain_block,
        "reconciliation": reconciliation_block,
        "failed_checks": failed_checks,
        "next_actions": next_actions,
    }
    if git_block is not None:
        result["git"] = git_block

    if audit_block is not None:
        result["audit"] = audit_block
    elif require_audit_sheet:
        raise LaunchError(
            f"--require-audit-sheet set but no commit audit sheet exists "
            f"for {version}.",
            code="VALIDATION_ERROR",
            exit_code=2,
            remediation=[f"Run `releaseledger audit init {version}` first."],
        )
    return result


def _build_audit_block(
    workspace_root: Path,
    *,
    version: str,
    include_internal: bool,
) -> dict[str, object] | None:
    """Return a JSON-friendly audit summary block, or None if no sheet exists."""
    from releaseledger.services.audit import validate_commit_audit_sheet
    from releaseledger.storage.store import load_commit_audit_sheet

    sheet = load_commit_audit_sheet(workspace_root, version)
    if sheet is None:
        return None
    report = validate_commit_audit_sheet(
        workspace_root,
        version=version,
        phase="evidence",
        strict=False,
        include_internal=include_internal,
    )
    complete = validate_commit_audit_sheet(
        workspace_root,
        version=version,
        phase="complete",
        strict=False,
        include_internal=include_internal,
    )
    row_count_raw = report["row_count"]
    needs_raw = report["needs_review_count"]
    uninsp_raw = report["uninspected_count"]
    violations_raw = complete["subject_summary_violations"]
    row_count = row_count_raw if isinstance(row_count_raw, int) else 0
    uninspected_count = uninsp_raw if isinstance(uninsp_raw, int) else 0
    evidence_issues = report.get("issues", [])
    missing_evidence_fields = sorted(
        {
            str(issue.get("code"))
            for issue in evidence_issues
            if isinstance(issue, dict) and issue.get("code")
        }
    )
    return {
        "exists": True,
        "row_count": row_count,
        "inspected_count": max(row_count - uninspected_count, 0),
        "unresolved_count": needs_raw if isinstance(needs_raw, int) else 0,
        "completed_decision_count": max(
            row_count - (needs_raw if isinstance(needs_raw, int) else 0), 0
        ),
        "missing_evidence_fields": missing_evidence_fields,
        "needs_review_count": (needs_raw if isinstance(needs_raw, int) else 0),
        "uninspected_count": uninspected_count,
        "subject_summary_violations": [
            str(v) for v in (violations_raw if isinstance(violations_raw, list) else [])
        ],
        "evidence": report,
        "complete": complete,
        "ok": bool(report["ok"]) and bool(complete["ok"]),
    }


def _run_changelog_dry_run(
    workspace_root: Path,
    *,
    version: str,
    release: ReleaseRecord,
    include_internal: bool,
    statuses: tuple[str, ...],
    target_file: Path | None,
    strict: bool,
    release_date: str | None = None,
) -> dict[str, object]:
    """Run the changelog dry-run without writing and normalize the verdict.

    Tries ``build_changelog_file(..., dry_run=True, strict=strict)`` first; on
    a ``LaunchError`` records the failure and recovers a best-effort
    ``section_heading`` via a non-strict :func:`render_changelog_section`.
    """
    target_display = (
        str(target_file)
        if target_file is not None
        else (release.changelog_file or "CHANGELOG.md")
    )
    block: dict[str, object] = {
        "target_file": target_display,
        "dry_run_ok": True,
        "strict": strict,
        "section_heading": None,
        "reason": None,
    }
    try:
        result = build_changelog_file(
            workspace_root,
            version=version,
            target_file=target_file,
            include_internal=include_internal,
            release_date=release_date,
            dry_run=True,
            replace_existing=False,
            include_statuses=statuses,
            strict=strict,
            allow_empty=False,
        )
        block["dry_run_ok"] = True
        block["section_heading"] = result.get("section_heading")
    except LaunchError as exc:
        block["dry_run_ok"] = False
        block["reason"] = exc.message
        # Best-effort heading recovery from a non-strict render so the report
        # still exposes what the section *would* look like.
        try:
            rendered = render_changelog_section(
                workspace_root,
                version=version,
                include_internal=include_internal,
                include_statuses=statuses,
            )
            block["section_heading"] = rendered.get("section_heading")
        except LaunchError:
            block["section_heading"] = None
    return block
