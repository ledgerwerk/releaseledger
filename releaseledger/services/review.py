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
from releaseledger.storage.store import load_entries, load_release

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

    # Fall back to stored release metadata.
    if base_to_use is None and release.git_base_sha is not None:
        base_to_use = release.git_base_sha
    if head_to_use is None and release.git_head_sha is not None:
        head_to_use = release.git_head_sha

    # Auto-enable git when the release has stored git metadata and the
    # workspace is a git worktree.
    if not git_enabled and base_to_use is not None and head_to_use is not None:
        git_enabled = is_git_worktree(workspace_root)

    if not (git_enabled and base_to_use is not None and head_to_use is not None):
        return git_block, git_warnings, git_ref_map, expected_refs

    try:
        candidates = collect_git_candidates(
            workspace_root,
            base_ref=base_to_use,
            head_ref=head_to_use,
            include_merges=include_merges,
        )
        summary = build_git_range_summary(
            workspace_root,
            base_ref=base_to_use,
            head_ref=head_to_use,
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
            "base_ref": summary.get("base_ref"),
            "base_sha": summary.get("base_sha"),
            "head_ref": summary.get("head_ref"),
            "head_sha": summary.get("head_sha"),
            "range": summary.get("range"),
            "commit_count": total_commits,
            "merge_commits_skipped": merge_skipped,
            "candidate_count": len(candidates),
            "include_merges": include_merges,
        }
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
        row["status"] == COVERAGE_COVERED
        for row in coverage
        if str(row.get("source_ref", "")).startswith("git:")
    )
    git_missing_count = sum(
        1
        for row in coverage
        if str(row.get("source_ref", "")).startswith("git:")
        and row["status"] != COVERAGE_COVERED
    )
    return git_coverage_ok, git_missing_count


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
    workspace_root = workspace_root.expanduser().resolve()
    release = load_release(workspace_root, version)
    statuses = tuple(normalize_entry_status(value) for value in include_statuses)
    entries = load_entries(workspace_root, version)

    # 1. Release payload.
    release_block: dict[str, object] = {
        "version": release.version,
        "status": release.status,
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

    # 3. Index entries by source_ref for coverage classification.
    by_ref: dict[str, list[ReleaseEntryRecord]] = {}
    for entry in entries:
        for ref in entry.source_refs:
            by_ref.setdefault(ref, []).append(entry)
        # boundary_ref coverage may be expressed without an explicit source_ref
        # on the entry; entries without any source_ref are still indexed under
        # their own fingerprint-free keys elsewhere (orphans). Coverage uses the
        # explicit refs only.

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
        # Enrich git-derived rows with provider and commit metadata.
        if git_block is not None:
            cand_obj = git_ref_map.get(ref)
            if ref.startswith("git:") and cand_obj is not None:
                row["provider"] = "git"
                row["summary"] = getattr(cand_obj, "subject", None)
                row["paths"] = list(getattr(cand_obj, "paths", ()))
            elif ref.startswith("git:"):
                row["provider"] = "git"
        coverage.append(row)

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
    )
    # Coverage is satisfied when every expected ref is covered; with no
    # expected refs, coverage is trivially satisfied.
    coverage_ok = all(row["status"] == COVERAGE_COVERED for row in coverage)
    lint_ok = lint_summary["errors"] == 0
    changelog_ok = bool(changelog_block.get("dry_run_ok", False))

    git_coverage_ok, git_missing_count = _compute_git_coverage(coverage, git_block)
    checks: dict[str, object] = {
        "coverage_ok": coverage_ok,
        "lint_ok": lint_ok,
        "changelog_ok": changelog_ok,
    }
    if git_block is not None:
        checks["git_coverage_ok"] = git_coverage_ok
    # `ok` aggregates coverage + lint always, plus changelog and git only in
    # strict mode.
    ok = coverage_ok and lint_ok and (not strict or changelog_ok)
    if strict and git_block is not None:
        ok = ok and git_coverage_ok

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
        "recommendations": recommendations,
    }
    if git_block is not None:
        result["git"] = git_block
    return result


def _run_changelog_dry_run(
    workspace_root: Path,
    *,
    version: str,
    release: ReleaseRecord,
    include_internal: bool,
    statuses: tuple[str, ...],
    target_file: Path | None,
    strict: bool,
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
