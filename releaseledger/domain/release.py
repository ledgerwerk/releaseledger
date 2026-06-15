"""Release record domain model.

A :class:`ReleaseRecord` is persisted as ``release.md`` with YAML front matter
(schema/version/status metadata) and an optional Markdown body holding the
release note. The ``note`` field is the body and is therefore excluded from the
front-matter representation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import ledgercore

from releaseledger.domain.source_ref import normalize_source_ref
from releaseledger.domain.states import (
    RELEASE_STATUSES,
    RELEASELEDGER_FILE_VERSION,
    RELEASELEDGER_SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
)
from releaseledger.errors import CODE_VALIDATION_ERROR, LaunchError

__all__ = [
    "RELEASE_FRONT_MATTER_KEY_ORDER",
    "ReleaseRecord",
    "parse_release_version_tuple",
]

# Canonical key order used when writing release.md front matter.
RELEASE_FRONT_MATTER_KEY_ORDER = (
    "schema_version",
    "object_type",
    "file_version",
    "version",
    "status",
    "title",
    "created_at",
    "released_at",
    "previous_version",
    "canceled_at",
    "cancel_reason",
    "superseded_by",
    "changelog_file",
    "boundary_ref",
    "source_refs",
    "source_count",
    "entry_count",
    "artifact_count",
    "git_base_ref",
    "git_base_sha",
    "git_head_ref",
    "git_head_sha",
    "git_range",
    "git_commit_count",
)


@dataclass(slots=True, frozen=True)
class ReleaseRecord:
    """A single release tracked by releaseledger."""

    version: str
    status: str = "planned"
    title: str | None = None
    created_at: str = field(default_factory=ledgercore.utc_now_iso)
    released_at: str | None = None
    previous_version: str | None = None
    canceled_at: str | None = None
    cancel_reason: str | None = None
    superseded_by: str | None = None
    note: str | None = None
    changelog_file: str | None = None
    boundary_ref: str | None = None
    source_refs: tuple[str, ...] = ()
    source_count: int | None = None
    entry_count: int = 0
    artifact_count: int = 0
    git_base_ref: str | None = None
    git_base_sha: str | None = None
    git_head_ref: str | None = None
    git_head_sha: str | None = None
    git_range: str | None = None
    git_commit_count: int | None = None
    file_version: str = RELEASELEDGER_FILE_VERSION
    schema_version: int = RELEASELEDGER_SCHEMA_VERSION
    object_type: str = "release"

    def to_dict(self) -> dict[str, object]:
        """Full machine-readable representation (includes note)."""
        return {
            "schema_version": self.schema_version,
            "object_type": self.object_type,
            "file_version": self.file_version,
            "version": self.version,
            "status": self.status,
            "title": self.title,
            "created_at": self.created_at,
            "released_at": self.released_at,
            "previous_version": self.previous_version,
            "canceled_at": self.canceled_at,
            "cancel_reason": self.cancel_reason,
            "superseded_by": self.superseded_by,
            "note": self.note,
            "changelog_file": self.changelog_file,
            "boundary_ref": self.boundary_ref,
            "source_refs": list(self.source_refs),
            "source_count": self.source_count,
            "entry_count": self.entry_count,
            "artifact_count": self.artifact_count,
            "git_base_ref": self.git_base_ref,
            "git_base_sha": self.git_base_sha,
            "git_head_ref": self.git_head_ref,
            "git_head_sha": self.git_head_sha,
            "git_range": self.git_range,
            "git_commit_count": self.git_commit_count,
        }

    def to_front_matter(self) -> dict[str, object]:
        """Front-matter representation (note is the body, not front matter)."""
        data = self.to_dict()
        data.pop("note", None)
        return data


def _require_str(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise LaunchError(
            f"Release field {field_name!r} must be a string.",
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
            f"Release field {field_name!r} must be an integer.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    return value


def _require_optional_int(value: object, field_name: str) -> int | None:
    if value is None:
        return None
    return _require_int(value, field_name)


def _require_str_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise LaunchError(
            f"Release field {field_name!r} must be a list.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    refs: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise LaunchError(
                f"Release field {field_name!r} must contain only strings.",
                code=CODE_VALIDATION_ERROR,
                exit_code=2,
            )
        try:
            canonical = normalize_source_ref(item)
        except LaunchError as exc:
            raise LaunchError(
                f"Invalid release source ref {item!r}: {exc.message}",
                code=CODE_VALIDATION_ERROR,
                exit_code=2,
            ) from exc
        if canonical not in refs:
            refs.append(canonical)
    return tuple(refs)


def _require_optional_global_ref(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    raw = _require_str(value, field_name)
    try:
        return normalize_source_ref(raw)
    except LaunchError as exc:
        raise LaunchError(
            f"Invalid release {field_name} {raw!r}: {exc.message}",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        ) from exc


# Release-version-shaped optional fields (e.g. superseded_by). Mirrors the
# stricter validation in storage.store.validate_release_version without the
# circular import; domain validates shape, storage re-validates for safety.
_RELEASE_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")


def _require_optional_release_version(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    raw = _require_str(value, field_name)
    if raw != raw.strip() or any(ch.isspace() for ch in raw):
        raise LaunchError(
            f"Release {field_name} must not contain whitespace: {raw!r}.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    if "/" in raw or "\\" in raw or any(ord(ch) < 32 for ch in raw):
        raise LaunchError(
            f"Invalid release {field_name}: {raw!r}.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    if _RELEASE_VERSION_RE.fullmatch(raw) is None:
        raise LaunchError(
            f"Invalid release {field_name}: {raw!r}.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    return raw


# Internal semantic-version parser for ordering/inference. Handles the
# common ``v0.1.0`` / ``0.1.0`` / ``1.2`` shapes used by release tags.
# Returns None for non-parseable versions so callers can fall back to string
# ordering. No external runtime dependency.
_SEMVER_RE = re.compile(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[-+].*)?$")


def parse_release_version_tuple(version: str) -> tuple[int, int, int] | None:
    """Parse ``v1.2.3``-style versions into a comparable ``(major, minor, patch)``.

    Returns ``None`` when the version is not a recognizable semantic version so
    callers can fall back to lexicographic ordering for non-standard versions.
    """
    if not isinstance(version, str):
        return None
    match = _SEMVER_RE.match(version.strip())
    if match is None:
        return None
    major = int(match.group(1))
    minor = int(match.group(2) or 0)
    patch = int(match.group(3) or 0)
    return (major, minor, patch)


def release_from_dict(data: dict[str, object]) -> ReleaseRecord:
    """Build a :class:`ReleaseRecord` with strict validation."""
    if data.get("object_type") != "release":
        raise LaunchError(
            "Release record object_type must be 'release'.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    schema_version = data.get("schema_version")
    if (
        not isinstance(schema_version, int)
        or schema_version not in SUPPORTED_SCHEMA_VERSIONS
    ):
        raise LaunchError(
            f"Unsupported release schema_version: {schema_version!r}",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    version = data.get("version")
    if not isinstance(version, str) or not version.strip():
        raise LaunchError(
            "Release version must be a non-empty string.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    status = data.get("status")
    if status not in RELEASE_STATUSES:
        raise LaunchError(
            f"Unsupported release status: {status!r}",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    return ReleaseRecord(
        version=version,
        status=status,
        title=_require_optional_str(data.get("title"), "title"),
        created_at=_require_str(data.get("created_at", ""), "created_at"),
        released_at=_require_optional_str(data.get("released_at"), "released_at"),
        previous_version=_require_optional_str(
            data.get("previous_version"), "previous_version"
        ),
        canceled_at=_require_optional_str(data.get("canceled_at"), "canceled_at"),
        cancel_reason=_require_optional_str(data.get("cancel_reason"), "cancel_reason"),
        superseded_by=_require_optional_release_version(
            data.get("superseded_by"), "superseded_by"
        ),
        note=_require_optional_str(data.get("note"), "note"),
        changelog_file=_require_optional_str(
            data.get("changelog_file"), "changelog_file"
        ),
        boundary_ref=_require_optional_global_ref(
            data.get("boundary_ref"), "boundary_ref"
        ),
        source_refs=_require_str_tuple(data.get("source_refs", []), "source_refs"),
        source_count=_require_optional_int(data.get("source_count"), "source_count"),
        entry_count=_require_int(data.get("entry_count", 0), "entry_count"),
        artifact_count=_require_int(data.get("artifact_count", 0), "artifact_count"),
        git_base_ref=_require_optional_str(data.get("git_base_ref"), "git_base_ref"),
        git_base_sha=_require_optional_str(data.get("git_base_sha"), "git_base_sha"),
        git_head_ref=_require_optional_str(data.get("git_head_ref"), "git_head_ref"),
        git_head_sha=_require_optional_str(data.get("git_head_sha"), "git_head_sha"),
        git_range=_require_optional_str(data.get("git_range"), "git_range"),
        git_commit_count=_require_optional_int(
            data.get("git_commit_count"), "git_commit_count"
        ),
        file_version=_require_str(
            data.get("file_version", RELEASELEDGER_FILE_VERSION), "file_version"
        ),
        schema_version=schema_version,
        object_type="release",
    )
