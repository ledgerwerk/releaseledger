"""Unit tests for releaseledger.domain.source_ref (Phase 1)."""

from __future__ import annotations

import pytest

from releaseledger.domain.source_ref import (
    GIT_COMMIT_REF_RE,
    is_coverable_boundary_ref,
    is_git_commit_ref,
    is_git_symbolic_or_range_ref,
    normalize_source_ref,
)
from releaseledger.errors import LaunchError

# --- is_git_commit_ref -----------------------------------------------------


@pytest.mark.parametrize(
    "ref",
    [
        "git:abcdef0",
        "git:0123456789abcdef0123456789abcdef01234567",
        "git:ABCDEF0",
        "git:" + "a" * 7,
        "git:" + "f" * 40,
    ],
)
def test_is_git_commit_ref_accepts_hex_shas(ref: str) -> None:
    assert is_git_commit_ref(ref) is True


@pytest.mark.parametrize(
    "ref",
    [
        "git:abc",  # too short (<7)
        "git:" + "a" * 41,  # too long (>40)
        "git:abcdefg",  # non-hex
        "git:HEAD",
        "git:main",
        "git:v1.0.0",
        "git-range:v0.1.0..HEAD",
        "git-tag:v0.1.0",
        "git-branch:main",
        "tl:task-0006",
        "github:pr-42",
        "",
        "git:",  # empty sha
    ],
)
def test_is_git_commit_ref_rejects_non_sha(ref: str) -> None:
    assert is_git_commit_ref(ref) is False


def test_git_commit_ref_re_pattern_bounds() -> None:
    assert GIT_COMMIT_REF_RE.match("git:abcdef0") is not None
    assert GIT_COMMIT_REF_RE.match("git:abcdef") is None  # 6 hex
    assert GIT_COMMIT_REF_RE.match("git:" + "a" * 41) is None


# --- is_git_symbolic_or_range_ref ------------------------------------------


@pytest.mark.parametrize(
    "ref",
    [
        "git:HEAD",
        "git:main",
        "git:v1.0.0",
        "git-range:v0.1.0..HEAD",
        "git-tag:v0.1.0",
        "git-branch:feature-a",
    ],
)
def test_is_git_symbolic_or_range_ref_detects_markers(ref: str) -> None:
    assert is_git_symbolic_or_range_ref(ref) is True


@pytest.mark.parametrize(
    "ref",
    [
        "git:abcdef0",
        "git:" + "a" * 40,
        "tl:task-0006",
        "github:pr-42",
        "",
        "HEAD",
    ],
)
def test_is_git_symbolic_or_range_ref_rejects_commit_and_global_refs(
    ref: str,
) -> None:
    assert is_git_symbolic_or_range_ref(ref) is False


# --- normalize_source_ref --------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("git:ABCDEF01", "git:abcdef01"),  # lowercased
        (
            "git:0123456789abcdef0123456789abcdef01234567",
            "git:0123456789abcdef0123456789abcdef01234567",
        ),
        ("  git:AbCdEf0123  ", "git:abcdef0123"),  # stripped + lowered
        ("tl:task-0006", "tl:task-0006"),  # global ref preserved
        ("github:pr-42", "github:pr-0042"),  # global ref canonicalized
    ],
)
def test_normalize_source_ref_accepts_git_and_global_refs(
    raw: str, expected: str
) -> None:
    assert normalize_source_ref(raw) == expected


@pytest.mark.parametrize(
    "ref",
    [
        "git:HEAD",
        "git:main",
        "git:v1.0.0",
        "git-range:v0.1.0..HEAD",
        "git-tag:v0.1.0",
        "git-branch:main",
    ],
)
def test_normalize_source_ref_rejects_symbolic_and_range_refs(ref: str) -> None:
    with pytest.raises(LaunchError) as exc_info:
        normalize_source_ref(ref)
    msg = exc_info.value.message
    assert "not a coverable source ref" in msg
    # Remediation hints are actionable.
    assert exc_info.value.remediation


def test_normalize_source_ref_rejects_empty_and_non_string() -> None:
    with pytest.raises(LaunchError):
        normalize_source_ref("")
    with pytest.raises(LaunchError):
        normalize_source_ref("   ")


def test_normalize_source_ref_rejects_invalid_global_ref() -> None:
    # ledgercore rejects malformed global refs.
    with pytest.raises(LaunchError):
        normalize_source_ref("not-a-ref!!")


def test_normalize_source_ref_rejects_too_short_sha() -> None:
    with pytest.raises(LaunchError):
        normalize_source_ref("git:abc")


# --- is_coverable_boundary_ref ---------------------------------------------


@pytest.mark.parametrize(
    "ref",
    [
        "tl:task-0006",
        "tl:task-0008",
        "al:task-0001",
        "github:pr-42",
        "github:issue-7",
        "git:abcdef0",  # explicit commit ref is coverable
        "git:" + "a" * 40,
    ],
)
def test_is_coverable_boundary_ref_coverable(ref: str) -> None:
    assert is_coverable_boundary_ref(ref) is True


@pytest.mark.parametrize(
    "ref",
    [
        None,
        "",
        "   ",
        "git-range:v0.1.0..HEAD",
        "git-tag:v0.1.0",
        "git-branch:main",
        "git:HEAD",
        "git:main",
        "git:v1.0.0",
    ],
)
def test_is_coverable_boundary_ref_non_coverable(ref: str | None) -> None:
    assert is_coverable_boundary_ref(ref) is False
