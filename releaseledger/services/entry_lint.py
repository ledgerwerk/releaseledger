"""Validation and lint reporting for release entries."""

from __future__ import annotations

import re
from pathlib import Path

import ledgercore

from releaseledger.domain.entry import entry_from_dict
from releaseledger.errors import CODE_VALIDATION_ERROR, LaunchError
from releaseledger.storage.paths import resolve_project_paths
from releaseledger.storage.store import load_entries, load_release, release_dir

__all__ = [
    "assert_entry_summary_valid",
    "lint_entry_records",
    "lint_release_entries",
    "validate_entry_summary",
]

_ACTION_PREFIXES = (
    "Added ",
    "Changed ",
    "Deprecated ",
    "Removed ",
    "Fixed ",
    "Secured ",
    "Documented ",
    "Improved ",
)
_RAW_TASK_RE = re.compile(r"(?<!:)\btask-\d+\b", re.IGNORECASE)


def _issue(
    severity: str, message: str, *, code: str, field: str = "summary", **extra: object
) -> dict[str, object]:
    issue: dict[str, object] = {
        "severity": severity,
        "code": code,
        "field": field,
        "message": message,
    }
    issue.update(extra)
    return issue


def validate_entry_summary(summary: str) -> list[dict[str, object]]:
    """Return deterministic style and validity findings for a summary."""
    issues: list[dict[str, str]] = []
    if not isinstance(summary, str) or not summary.strip():
        return [_issue("error", "Summary must not be empty.", code="empty")]
    if "\n" in summary or "\r" in summary:
        issues.append(
            _issue("error", "Summary must be one line.", code="multiple_lines")
        )
    if len(summary) > 180:
        issues.append(
            _issue(
                "error",
                "Summary must not exceed 180 characters.",
                code="too_long",
                length=len(summary),
            )
        )
    elif len(summary) > 120:
        issues.append(
            _issue(
                "warning",
                "Summary exceeds the recommended 120 characters.",
                code="long",
                length=len(summary),
            )
        )
    if summary.lstrip().startswith("#"):
        issues.append(
            _issue("error", "Summary must not be a Markdown heading.", code="heading")
        )
    if "TODO" in summary.upper():
        issues.append(
            _issue("error", "Summary must not contain TODO markers.", code="todo")
        )
    if "[ ]" in summary:
        issues.append(
            _issue(
                "error",
                "Summary must not contain unchecked task markers.",
                code="unchecked",
            )
        )
    if _RAW_TASK_RE.search(summary):
        issues.append(
            _issue(
                "warning",
                "Summary should avoid raw local task IDs.",
                code="raw_task_id",
            )
        )
    if summary.rstrip().endswith("."):
        issues.append(
            _issue(
                "warning",
                "Summary should not end with a period.",
                code="trailing_period",
            )
        )
    if not summary.startswith(_ACTION_PREFIXES):
        issues.append(
            _issue(
                "warning",
                "Summary should start with an action phrase such as Added, "
                "Changed, Fixed, Documented, or Improved.",
                code="action_prefix",
            )
        )
    return issues


def lint_entry_records(
    entries: list[ReleaseEntryRecord],
    *,
    strict: bool = False,
) -> dict[str, object]:
    """Lint a prepared in-memory entry list without reading from disk."""
    issues: list[dict[str, object]] = []
    entry_results: list[dict[str, object]] = []
    for entry in entries:
        entry_issues: list[dict[str, object]] = []
        for issue in validate_entry_summary(entry.summary):
            enriched: dict[str, object] = {
                **issue,
                "entry_id": entry.entry_id,
                "summary_length": len(entry.summary),
            }
            issues.append(enriched)
            entry_issues.append(enriched)
        entry_results.append(
            {
                "entry_id": entry.entry_id,
                "status": entry.status,
                "issues": entry_issues,
            }
        )
    if not any(entry.status == "accepted" for entry in entries):
        issues.append(
            {
                "severity": "warning",
                "code": "no_accepted_entries",
                "field": "status",
                "message": "Release has no accepted entries.",
            }
        )
    errors = sum(issue["severity"] == "error" for issue in issues)
    warnings = sum(issue["severity"] == "warning" for issue in issues)
    return {
        "issues": issues,
        "entries": entry_results,
        "summary": {"errors": errors, "warnings": warnings},
        "passed": errors == 0 and (not strict or warnings == 0),
        "strict": strict,
    }


def assert_entry_summary_valid(summary: str, *, fail_on_warning: bool = True) -> None:
    issues = validate_entry_summary(summary)
    blocking = [
        issue
        for issue in issues
        if issue["severity"] == "error"
        or (fail_on_warning and issue["severity"] == "warning")
    ]
    if blocking:
        raise LaunchError(
            "; ".join(issue["message"] for issue in blocking),
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )


def lint_release_entries(
    workspace_root: Path,
    *,
    release_version: str,
    strict: bool = False,
    include_statuses: tuple[str, ...] | None = None,
) -> dict[str, object]:
    """Lint all selected entries in a release."""
    load_release(workspace_root, release_version)
    entries = load_entries(workspace_root, release_version)
    if include_statuses is not None:
        entries = [entry for entry in entries if entry.status in include_statuses]
    issues: list[dict[str, object]] = []
    paths = resolve_project_paths(workspace_root)
    entries_path = release_dir(paths, release_version) / "entries"
    checked_paths = sorted(entries_path.glob("entry-*.md"))
    valid_ids = {entry.entry_id for entry in entries}
    for path in checked_paths:
        try:
            metadata, body = ledgercore.read_front_matter_document(path)
            data = dict(metadata)
            data["body"] = body if body else None
            record = entry_from_dict(data)
            if record.release_version != release_version:
                raise LaunchError(
                    f"Entry release_version {record.release_version!r} does not "
                    f"match directory release {release_version!r}.",
                    code=CODE_VALIDATION_ERROR,
                    exit_code=2,
                )
        except (ledgercore.FrontMatterError, LaunchError) as exc:
            message = exc.message if isinstance(exc, LaunchError) else str(exc)
            lowered = message.lower()
            aliases = (
                ("schema_version", ("schema_version", "schema version")),
                ("release_version", ("release_version", "release version")),
                ("source_refs", ("source_refs", "source refs", "source ref")),
                ("status", ("status",)),
                ("paths", ("paths", "path")),
                ("kind", ("kind",)),
                ("front_matter", ("front matter",)),
            )
            field = next(
                (
                    name
                    for name, terms in aliases
                    if any(term in lowered for term in terms)
                ),
                "record",
            )
            issues.append(
                {
                    "severity": "error",
                    "code": "invalid_record",
                    "field": field,
                    "entry_id": path.stem,
                    "file": str(path),
                    "message": message,
                }
            )
            valid_ids.discard(path.stem)
    entries = [entry for entry in entries if entry.entry_id in valid_ids]
    lint_result = lint_entry_records(entries, strict=strict)
    issues.extend(lint_result["issues"])  # type: ignore[arg-type]
    entry_results = lint_result["entries"]  # type: ignore[assignment]
    summary = lint_result["summary"]
    assert isinstance(summary, dict)
    errors = int(summary["errors"])
    warnings = int(summary["warnings"])
    return {
        "kind": "entry_lint",
        "release_version": release_version,
        "checked_files": [str(path) for path in checked_paths],
        "entry_count": len(checked_paths),
        "summary": {"errors": errors, "warnings": warnings},
        "issues": issues,
        "entries": entry_results,
        "passed": bool(lint_result["passed"]),
        "strict": strict,
    }
