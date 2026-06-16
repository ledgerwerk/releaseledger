"""Commit audit sheet domain model.

A :class:`CommitAuditSheetRecord` maps every commit in a release's git range to
a reviewer decision and, when applicable, to a release entry. It is per-release
*evidence/review state*, not changelog prose: commit subjects are stored as
``evidence_subject`` only and must never become entry summaries.

The canonical persistence format is YAML under each release's ``audit/``
directory (see :mod:`releaseledger.storage.store`). This module owns the
dataclasses and the dict<->record converters plus enum normalization. It reuses
:mod:`releaseledger.domain.versioning` for revision metadata, so audit sheets
follow the same schema_version/revision lifecycle as release and entry records.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from releaseledger.domain.source_ref import is_git_commit_ref
from releaseledger.domain.versioning import (
    RecordVersioning,
    initial_versioning,
    versioning_from_dict,
)
from releaseledger.errors import CODE_VALIDATION_ERROR, LaunchError

__all__ = [
    "AUDIT_SHEET_SCHEMA_VERSION",
    "AUDIT_DECISIONS",
    "AUDIT_PUBLIC_IMPACTS",
    "CommitAuditRow",
    "CommitAuditSheetRecord",
    "CommitAuditStats",
    "audit_sheet_from_dict",
    "audit_sheet_to_dict",
    "normalize_audit_decision",
    "normalize_public_impact",
    "validate_sha",
]

AUDIT_SHEET_SCHEMA_VERSION = 1

# Canonical decision vocabulary.
AUDIT_DECISIONS = (
    "needs_review",
    "accepted",
    "grouped",
    "internal",
    "rejected",
)

# Canonical public-impact vocabulary.
AUDIT_PUBLIC_IMPACTS = (
    "public",
    "docs",
    "internal",
    "none",
    "unknown",
)

_FULL_SHA_RE_TEXT = r"^[0-9a-fA-F]{40}$"


@dataclass(frozen=True, slots=True)
class CommitAuditStats:
    files_changed: int = 0
    insertions: int = 0
    deletions: int = 0


@dataclass(frozen=True, slots=True)
class CommitAuditRow:
    sha: str
    source_ref: str
    short_sha: str = ""
    evidence_subject: str | None = None
    changed_paths: tuple[str, ...] = ()
    stats: CommitAuditStats = field(default_factory=CommitAuditStats)
    inspected: bool = False
    inspected_paths: tuple[str, ...] = ()
    observed_behavior: str = ""
    public_impact: str = "unknown"
    decision: str = "needs_review"
    target_entry_key: str | None = None
    target_entry_id: str | None = None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class CommitAuditSheetRecord:
    release_version: str
    versioning: RecordVersioning
    git_base_ref: str | None = None
    git_base_sha: str | None = None
    git_head_ref: str | None = None
    git_head_sha: str | None = None
    git_range: str | None = None
    commit_count: int = 0
    rows: tuple[CommitAuditRow, ...] = ()
    schema_version: int = AUDIT_SHEET_SCHEMA_VERSION
    object_type: str = "commit_audit_sheet"


def validate_sha(value: str) -> str:
    """Validate and canonicalize a full 40-character git SHA."""
    import re

    if not isinstance(value, str):
        raise LaunchError(
            "Commit SHA must be a string.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    trimmed = value.strip()
    if not re.fullmatch(_FULL_SHA_RE_TEXT, trimmed):
        raise LaunchError(
            f"Invalid commit SHA {value!r}: expected a 40-character hex SHA.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    return trimmed.lower()


def normalize_audit_decision(value: str) -> str:
    """Return the canonical decision for ``value`` or raise."""
    if not isinstance(value, str):
        raise LaunchError(
            "Audit decision must be a string.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    normalized = value.strip().lower()
    if normalized not in AUDIT_DECISIONS:
        raise LaunchError(
            f"Unsupported audit decision {value!r}. "
            f"Expected one of: {', '.join(AUDIT_DECISIONS)}.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    return normalized


def normalize_public_impact(value: str) -> str:
    """Return the canonical public-impact value for ``value`` or raise."""
    if not isinstance(value, str):
        raise LaunchError(
            "Audit public_impact must be a string.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    normalized = value.strip().lower()
    if normalized not in AUDIT_PUBLIC_IMPACTS:
        raise LaunchError(
            f"Unsupported audit public_impact {value!r}. "
            f"Expected one of: {', '.join(AUDIT_PUBLIC_IMPACTS)}.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    return normalized


def _require_str(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise LaunchError(
            f"Audit field {field_name!r} must be a string.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    return value


def _require_optional_str(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _require_str(value, field_name)


def _require_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise LaunchError(
            f"Audit field {field_name!r} must be an integer.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    return value


def _require_str_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise LaunchError(
            f"Audit field {field_name!r} must be a list.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    return tuple(_require_str(item, field_name) for item in value)


def _stats_from_dict(value: object) -> CommitAuditStats:
    if value is None:
        return CommitAuditStats()
    if not isinstance(value, dict):
        raise LaunchError(
            "Audit row 'stats' must be a mapping.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    return CommitAuditStats(
        files_changed=_require_int(
            value.get("files_changed", 0), "stats.files_changed"
        ),
        insertions=_require_int(value.get("insertions", 0), "stats.insertions"),
        deletions=_require_int(value.get("deletions", 0), "stats.deletions"),
    )


def _row_from_dict(value: object) -> CommitAuditRow:
    if not isinstance(value, dict):
        raise LaunchError(
            "Audit row must be a mapping.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    sha = validate_sha(_require_str(value.get("sha"), "sha"))
    source_ref = f"git:{sha}"
    raw_source_ref = _require_str(value.get("source_ref", source_ref), "source_ref")
    if raw_source_ref != source_ref:
        raise LaunchError(
            f"Audit row source_ref {raw_source_ref!r} must equal git:{sha} "
            "after normalization.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    short_sha = _require_str(value.get("short_sha", sha[:7]), "short_sha")
    evidence_subject = _require_optional_str(
        value.get("evidence_subject"), "evidence_subject"
    )
    changed_paths = _require_str_tuple(value.get("changed_paths"), "changed_paths")
    stats = _stats_from_dict(value.get("stats"))
    inspected_raw = value.get("inspected", False)
    if not isinstance(inspected_raw, bool):
        raise LaunchError(
            "Audit field 'inspected' must be a boolean.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    inspected_paths = _require_str_tuple(
        value.get("inspected_paths"), "inspected_paths"
    )
    observed_behavior = _require_str(
        value.get("observed_behavior", ""), "observed_behavior"
    )
    public_impact = normalize_public_impact(
        _require_str(value.get("public_impact", "unknown"), "public_impact")
    )
    decision = normalize_audit_decision(
        _require_str(value.get("decision", "needs_review"), "decision")
    )
    target_entry_key = _require_optional_str(
        value.get("target_entry_key"), "target_entry_key"
    )
    target_entry_id = _require_optional_str(
        value.get("target_entry_id"), "target_entry_id"
    )
    notes = _require_optional_str(value.get("notes"), "notes")
    return CommitAuditRow(
        sha=sha,
        source_ref=source_ref,
        short_sha=short_sha,
        evidence_subject=evidence_subject,
        changed_paths=changed_paths,
        stats=stats,
        inspected=inspected_raw,
        inspected_paths=inspected_paths,
        observed_behavior=observed_behavior,
        public_impact=public_impact,
        decision=decision,
        target_entry_key=target_entry_key,
        target_entry_id=target_entry_id,
        notes=notes,
    )


def audit_sheet_from_dict(data: dict[str, object]) -> CommitAuditSheetRecord:
    """Build a :class:`CommitAuditSheetRecord` with strict validation."""
    if data.get("object_type") != "commit_audit_sheet":
        raise LaunchError(
            "Commit audit sheet object_type must be 'commit_audit_sheet'.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    schema_version = data.get("schema_version")
    if (
        not isinstance(schema_version, int)
        or schema_version != AUDIT_SHEET_SCHEMA_VERSION
    ):
        raise LaunchError(
            f"Unsupported audit sheet schema_version: {schema_version!r}",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    release_version = _require_str(data.get("release_version"), "release_version")
    if not release_version.strip():
        raise LaunchError(
            "Audit sheet release_version must be a non-empty string.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    versioning = versioning_from_dict(data.get("versioning"))
    git_base_ref = _require_optional_str(data.get("git_base_ref"), "git_base_ref")
    git_base_sha = _require_optional_str(data.get("git_base_sha"), "git_base_sha")
    git_head_ref = _require_optional_str(data.get("git_head_ref"), "git_head_ref")
    git_head_sha = _require_optional_str(data.get("git_head_sha"), "git_head_sha")
    git_range = _require_optional_str(data.get("git_range"), "git_range")
    commit_count = _require_int(data.get("commit_count", 0), "commit_count")
    raw_rows = data.get("rows", [])
    if not isinstance(raw_rows, (list, tuple)):
        raise LaunchError(
            "Audit sheet 'rows' must be a list.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    rows = [_row_from_dict(item) for item in raw_rows]
    seen: set[str] = set()
    for row in rows:
        if row.sha in seen:
            raise LaunchError(
                f"Duplicate audit row SHA {row.sha!r}.",
                code=CODE_VALIDATION_ERROR,
                exit_code=2,
            )
        seen.add(row.sha)
    return CommitAuditSheetRecord(
        release_version=release_version,
        versioning=versioning,
        git_base_ref=git_base_ref,
        git_base_sha=git_base_sha,
        git_head_ref=git_head_ref,
        git_head_sha=git_head_sha,
        git_range=git_range,
        commit_count=commit_count,
        rows=tuple(rows),
        schema_version=schema_version,
        object_type="commit_audit_sheet",
    )


def audit_sheet_to_dict(record: CommitAuditSheetRecord) -> dict[str, object]:
    """Render a :class:`CommitAuditSheetRecord` as a plain dict."""
    return {
        "schema_version": record.schema_version,
        "object_type": record.object_type,
        "versioning": record.versioning.to_dict(),
        "release_version": record.release_version,
        "git_base_ref": record.git_base_ref,
        "git_base_sha": record.git_base_sha,
        "git_head_ref": record.git_head_ref,
        "git_head_sha": record.git_head_sha,
        "git_range": record.git_range,
        "commit_count": record.commit_count,
        "rows": [_row_to_dict(row) for row in record.rows],
    }


def _row_to_dict(row: CommitAuditRow) -> dict[str, object]:
    data: dict[str, object] = {
        "sha": row.sha,
        "source_ref": row.source_ref,
        "short_sha": row.short_sha,
        "changed_paths": list(row.changed_paths),
        "stats": {
            "files_changed": row.stats.files_changed,
            "insertions": row.stats.insertions,
            "deletions": row.stats.deletions,
        },
        "inspected": row.inspected,
        "inspected_paths": list(row.inspected_paths),
        "observed_behavior": row.observed_behavior,
        "public_impact": row.public_impact,
        "decision": row.decision,
    }
    if row.evidence_subject is not None:
        data["evidence_subject"] = row.evidence_subject
    if row.target_entry_key is not None:
        data["target_entry_key"] = row.target_entry_key
    if row.target_entry_id is not None:
        data["target_entry_id"] = row.target_entry_id
    if row.notes is not None:
        data["notes"] = row.notes
    return data


def is_git_commit_sha(value: str) -> bool:
    """Return True when ``value`` is a full 40-char hex SHA (not a ref)."""
    import re

    return isinstance(value, str) and bool(re.fullmatch(_FULL_SHA_RE_TEXT, value))


# Re-export for service-layer callers that prefer this spelling.
__all__ += ["is_git_commit_sha", "initial_versioning", "is_git_commit_ref"]
