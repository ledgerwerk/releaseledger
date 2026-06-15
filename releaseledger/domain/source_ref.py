"""Source-reference normalization for git-first releaseledger.

Releaseledger is git-first: a ``git:<sha>`` commit ref is a first-class coverable
source ref, on equal footing with ledgercore global refs (``tl:task-0006``,
``github:pr-0042``). Taskledger and other global refs remain valid provenance and
keep their existing canonicalization. Git *symbolic* refs (``git:HEAD``,
``git:main``, ``git-range:*``, ``git-tag:*``, ``git-branch:*``) are range markers,
not coverable change identities, and are rejected as ``source_refs``.

This module is the single routing point for source-ref validation so the four
historical ``ledgercore.parse_global_ref`` call sites stay consistent.
"""

from __future__ import annotations

import re

import ledgercore

from releaseledger.errors import CODE_VALIDATION_ERROR, LaunchError

__all__ = [
    "GIT_COMMIT_REF_RE",
    "GIT_RANGE_PREFIXES",
    "NON_COVERABLE_BOUNDARY_PREFIXES",
    "GitSourceRefError",
    "is_coverable_boundary_ref",
    "is_git_commit_ref",
    "is_git_symbolic_or_range_ref",
    "normalize_source_ref",
]


# A coverable git commit source ref: ``git:<7-to-40 hex>``.
# We store the canonical lowercase spelling. releaseledger prefers the full
# 40-char SHA when it creates the ref (see services/git_sources.py), but existing
# abbreviated SHAs (>= 7 hex) are accepted for interoperability.
GIT_COMMIT_REF_RE = re.compile(r"^git:[0-9a-fA-F]{7,40}$")

# Git range/marker metadata prefixes. These are useful as release range metadata
# (e.g. ``boundary_ref = git-range:v0.1.0..HEAD``) but are NOT coverable change
# identities, so they must not produce a missing-coverage row and are rejected as
# entry/release ``source_refs``.
GIT_RANGE_PREFIXES = ("git-range:", "git-tag:", "git-branch:")


class GitSourceRefError(LaunchError):
    """Raised when a git-shaped source ref is not a coverable commit ref."""


def is_git_commit_ref(ref: str) -> bool:
    """Return True when ``ref`` is a coverable ``git:<7..40 hex>`` commit ref."""
    return isinstance(ref, str) and GIT_COMMIT_REF_RE.match(ref) is not None


def is_git_symbolic_or_range_ref(ref: str) -> bool:
    """Return True when ``ref`` is a git range/marker ref (non-coverable).

    Covers ``git-range:*``, ``git-tag:*``, ``git-branch:*`` and symbolic
    ``git:<name>`` refs such as ``git:HEAD``, ``git:main``, ``git:v1.0.0``
    (any ``git:<token>`` that is not a hex commit SHA).
    """
    if not isinstance(ref, str) or not ref.startswith("git"):
        return False
    for prefix in GIT_RANGE_PREFIXES:
        if ref.startswith(prefix):
            return True
    # git:<name> where <name> is not a hex SHA. ``git:HEAD`` is the canonical
    # example; ``git:main``/``git:v1.0.0`` are the same family.
    if ref.startswith("git:") and not is_git_commit_ref(ref):
        return True
    return False


def normalize_source_ref(raw: str) -> str:
    """Normalize and validate one coverable source ref.

    Accepted:
        - existing ledgercore global refs: ``tl:task-0006``, ``github:pr-42``
          (canonicalized by ``ledgercore.parse_global_ref``; width-padded etc.).
        - git commit refs: ``git:<7-to-40 hex>``, returned lowercase.

    Rejected (raises :class:`LaunchError`):
        - git symbolic refs: ``git:HEAD``, ``git:main``, ``git:v1.0.0``
        - git range/marker refs: ``git-range:*``, ``git-tag:*``, ``git-branch:*``
        - anything ``ledgercore.parse_global_ref`` rejects.
    """
    if not isinstance(raw, str):
        raise LaunchError(
            "Source ref must be a string.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    value = raw.strip()
    if not value:
        raise LaunchError(
            "Source ref must not be empty.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    # Reject git symbolic/range markers with an actionable message before
    # ledgercore sees them (ledgercore would reject ``git:HEAD`` for a different
    # reason and produce a confusing error).
    if is_git_symbolic_or_range_ref(value):
        kind = "symbolic ref" if value.startswith("git:") else "range marker"
        raise LaunchError(
            f"Git {kind} {value!r} is not a coverable source ref. "
            "Use a commit SHA like 'git:<sha>' or a global ref like 'tl:task-0006'.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
            remediation=[
                "Resolve the ref to a full SHA with `git rev-parse <ref>`.",
                "Store range metadata in release git_base_ref/git_head_ref, "
                "not in source_refs.",
            ],
        )
    if is_git_commit_ref(value):
        return value.lower()
    try:
        return ledgercore.parse_global_ref(value).global_ref
    except ledgercore.IdFormatError as exc:
        raise LaunchError(
            f"Invalid source ref {value!r}: {exc}",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        ) from exc


# Boundary refs that double as coverable work-item identities. These are the
# existing ledgercore global work-item refs (tl:*, al:*, github:pr-* and any
# other global ref ledgercore accepts). A git range marker used as a boundary
# (e.g. ``git-range:v0.1.0..HEAD``) is NOT coverable and must not create a
# missing-coverage row.
NON_COVERABLE_BOUNDARY_PREFIXES = GIT_RANGE_PREFIXES + ("git:HEAD",)


def is_coverable_boundary_ref(ref: str | None) -> bool:
    """Return True when ``ref`` should be treated as a coverable boundary identity.

    Coverable boundaries (still produce coverage rows):
        - ledgercore global work-item refs: ``tl:*``, ``al:*``, ``github:pr-*`` ...

    Non-coverable boundaries (range markers; no coverage row):
        - ``git-range:*``, ``git-tag:*``, ``git-branch:*``
        - ``git:HEAD`` and any symbolic ``git:<name>``

    A plain ``git:<sha>`` is coverable when explicitly stored in ``source_refs``,
    but it is not a default boundary marker, so a ``boundary_ref`` of
    ``git:<sha>`` is still classified here as coverable (it is a concrete commit
    identity). ``None`` and empty strings are non-coverable.
    """
    if not isinstance(ref, str) or not ref.strip():
        return False
    value = ref.strip()
    # Explicit non-coverable markers.
    for prefix in NON_COVERABLE_BOUNDARY_PREFIXES:
        if value == prefix or value.startswith(prefix):
            return False
    # git-range:/git-tag:/git-branch: already handled above. Any remaining
    # git:* value is either a commit SHA (coverable) or a symbolic ref. A
    # symbolic ref as a boundary is a range marker -> non-coverable.
    if value.startswith("git:") and not is_git_commit_ref(value):
        return False
    # Everything else is a ledgercore-style global work-item ref (coverable).
    return True
