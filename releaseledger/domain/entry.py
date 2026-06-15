"""Release entry domain model.

A :class:`ReleaseEntryRecord` is persisted as ``entry-NNNN.md`` inside a release
bundle. The ``body`` field is the Markdown body of the file and is excluded from
front matter.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import ledgercore

from releaseledger.domain.source_ref import normalize_source_ref
from releaseledger.domain.states import (
    ENTRY_KIND_ALIASES,
    ENTRY_KINDS,
    ENTRY_STATUSES,
    RELEASELEDGER_FILE_VERSION,
    RELEASELEDGER_SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
)
from releaseledger.errors import CODE_VALIDATION_ERROR, LaunchError

__all__ = [
    "ENTRY_FRONT_MATTER_KEY_ORDER",
    "ReleaseEntryRecord",
    "normalize_entry_kind",
    "normalize_entry_status",
    "normalize_scopes",
    "validate_source_refs",
]

ENTRY_FRONT_MATTER_KEY_ORDER = (
    "schema_version",
    "object_type",
    "file_version",
    "entry_id",
    "release_version",
    "kind",
    "summary",
    "created_at",
    "updated_at",
    "status",
    "audience",
    "scopes",
    "source_refs",
    "paths",
    "issues",
    "prs",
    "sources",
    "breaking",
    "internal",
    "order",
)


@dataclass(slots=True, frozen=True)
class ReleaseEntryRecord:
    """A single changelog entry attached to a release."""

    entry_id: str
    release_version: str
    kind: str
    summary: str
    body: str | None = None
    created_at: str = field(default_factory=ledgercore.utc_now_iso)
    updated_at: str | None = None
    status: str = "accepted"
    audience: str | None = None
    scopes: tuple[str, ...] = ()
    source_refs: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()
    issues: tuple[str, ...] = ()
    prs: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    breaking: bool = False
    internal: bool = False
    order: int | None = None
    file_version: str = RELEASELEDGER_FILE_VERSION
    schema_version: int = RELEASELEDGER_SCHEMA_VERSION
    object_type: str = "release_entry"

    def to_dict(self) -> dict[str, object]:
        """Full machine-readable representation (includes body)."""
        return {
            "schema_version": self.schema_version,
            "object_type": self.object_type,
            "file_version": self.file_version,
            "entry_id": self.entry_id,
            "release_version": self.release_version,
            "kind": self.kind,
            "summary": self.summary,
            "body": self.body,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "status": self.status,
            "audience": self.audience,
            "scopes": list(self.scopes),
            "source_refs": list(self.source_refs),
            "paths": list(self.paths),
            "issues": list(self.issues),
            "prs": list(self.prs),
            "sources": list(self.sources),
            "breaking": self.breaking,
            "internal": self.internal,
            "order": self.order,
        }

    def to_front_matter(self) -> dict[str, object]:
        """Front-matter representation (body is the file body, not front matter)."""
        data = self.to_dict()
        data.pop("body", None)
        return data


def _require_str(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise LaunchError(
            f"Entry field {field_name!r} must be a string.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    return value


def _require_optional_str(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _require_str(value, field_name)


def _require_str_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise LaunchError(
            f"Entry field {field_name!r} must be a list.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    items: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise LaunchError(
                f"Entry field {field_name!r} must contain only strings.",
                code=CODE_VALIDATION_ERROR,
                exit_code=2,
            )
        items.append(item)
    return tuple(items)


def _require_bool(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise LaunchError(
            f"Entry field {field_name!r} must be a boolean.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    return value


def _require_optional_int(value: object, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise LaunchError(
            f"Entry field {field_name!r} must be an integer.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    return value


def normalize_entry_kind(value: str) -> str:
    """Return the canonical persisted entry kind."""
    if not isinstance(value, str):
        raise LaunchError(
            "Entry kind must be a string.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    normalized = ENTRY_KIND_ALIASES.get(value.strip().lower(), value.strip().lower())
    if normalized not in ENTRY_KINDS:
        raise LaunchError(
            f"Unsupported entry kind: {value!r}",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    return normalized


def normalize_entry_status(value: str) -> str:
    """Validate and normalize an entry lifecycle status."""
    if not isinstance(value, str):
        raise LaunchError(
            "Entry status must be a string.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    normalized = value.strip().lower()
    if normalized not in ENTRY_STATUSES:
        raise LaunchError(
            f"Unsupported entry status: {value!r}",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    return normalized


def normalize_scopes(values: tuple[str, ...]) -> tuple[str, ...]:
    """Normalize scopes while preserving first-seen order."""
    normalized: list[str] = []
    for raw in values:
        value = raw.strip()
        if not value:
            raise LaunchError(
                "Entry scopes must not contain empty values.",
                code=CODE_VALIDATION_ERROR,
                exit_code=2,
            )
        if value not in normalized:
            normalized.append(value)
    return tuple(normalized)


def validate_source_refs(values: tuple[str, ...]) -> tuple[str, ...]:
    """Validate source refs and return their canonical spelling.

    Accepts ledgercore global refs (``tl:task-0006``, ``github:pr-42``) and
    git commit refs (``git:<7..40 hex>``). Routes through
    :func:`normalize_source_ref` so git symbolic/range markers are rejected
    with an actionable error.
    """
    validated: list[str] = []
    for raw in values:
        try:
            canonical = normalize_source_ref(raw)
        except LaunchError as exc:
            raise LaunchError(
                f"Invalid source ref {raw!r}: {exc.message}",
                code=CODE_VALIDATION_ERROR,
                exit_code=2,
            ) from exc
        if canonical not in validated:
            validated.append(canonical)
    return tuple(validated)


def _validate_paths(values: tuple[str, ...]) -> tuple[str, ...]:
    validated: list[str] = []
    for raw in values:
        try:
            value = ledgercore.validate_relative_posix_path(raw, field_name="paths")
        except ledgercore.PathValidationError as exc:
            raise LaunchError(
                str(exc),
                code=CODE_VALIDATION_ERROR,
                exit_code=2,
            ) from exc
        validated.append(value)
    return tuple(validated)


def entry_from_dict(data: dict[str, object]) -> ReleaseEntryRecord:
    """Build a :class:`ReleaseEntryRecord` with strict validation."""
    if data.get("object_type") != "release_entry":
        raise LaunchError(
            "Entry record object_type must be 'release_entry'.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    schema_version = data.get("schema_version")
    if (
        not isinstance(schema_version, int)
        or schema_version not in SUPPORTED_SCHEMA_VERSIONS
    ):
        raise LaunchError(
            f"Unsupported entry schema_version: {schema_version!r}",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    entry_id = data.get("entry_id")
    if not isinstance(entry_id, str) or not entry_id.strip():
        raise LaunchError(
            "Entry entry_id must be a non-empty string.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    release_version = data.get("release_version")
    if not isinstance(release_version, str) or not release_version.strip():
        raise LaunchError(
            "Entry release_version must be a non-empty string.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    kind = normalize_entry_kind(_require_str(data.get("kind"), "kind"))
    summary = data.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise LaunchError(
            "Entry summary must be a non-empty string.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    return ReleaseEntryRecord(
        entry_id=entry_id,
        release_version=release_version,
        kind=kind,
        summary=summary,
        body=_require_optional_str(data.get("body"), "body"),
        created_at=_require_str(data.get("created_at", ""), "created_at"),
        updated_at=_require_optional_str(data.get("updated_at"), "updated_at"),
        status=normalize_entry_status(
            _require_str(data.get("status", "accepted"), "status")
        ),
        audience=_require_optional_str(data.get("audience"), "audience"),
        scopes=normalize_scopes(_require_str_tuple(data.get("scopes", []), "scopes")),
        source_refs=validate_source_refs(
            _require_str_tuple(data.get("source_refs", []), "source_refs")
        ),
        paths=_validate_paths(_require_str_tuple(data.get("paths", []), "paths")),
        issues=_require_str_tuple(data.get("issues", []), "issues"),
        prs=_require_str_tuple(data.get("prs", []), "prs"),
        sources=_require_str_tuple(data.get("sources", []), "sources"),
        breaking=_require_bool(data.get("breaking", False), "breaking"),
        internal=_require_bool(data.get("internal", False), "internal"),
        order=_require_optional_int(data.get("order"), "order"),
        file_version=_require_str(
            data.get("file_version", RELEASELEDGER_FILE_VERSION), "file_version"
        ),
        schema_version=schema_version,
        object_type="release_entry",
    )
