"""Acceptance tests for releaseledger.services.git_sources (Phase 2, design §14).

These build real temporary git repositories and exercise the range scanner so
coverage matches what actually shipped (merges, rebases, squashes, amends).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from releaseledger.errors import LaunchError
from releaseledger.services.git_sources import (
    build_git_range_summary,
    collect_git_candidates,
    is_git_worktree,
    net_diff_paths,
    resolve_git_ref,
)

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    env = {
        **os.environ,
        # Deterministic, reproducible commits with no per-user config.
        "GIT_AUTHOR_NAME": "Tester",
        "GIT_AUTHOR_EMAIL": "tester@example.com",
        "GIT_COMMITTER_NAME": "Tester",
        "GIT_COMMITTER_EMAIL": "tester@example.com",
        "GIT_CONFIG_NOSYSTEM": "1",
        "HOME": str(repo),
    }
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )
    return result.stdout


def _commit(repo: Path, message: str, *, content_name: str | None = None) -> str:
    """Create a commit and return its full SHA."""
    if content_name is not None:
        (repo / content_name).write_text(f"content for {content_name}\n")
        _git(repo, "add", content_name)
    else:
        # touch a unique file so each commit is real
        name = f"file-{abs(hash(message)) % 100000}.txt"
        (repo / name).write_text(message + "\n")
        _git(repo, "add", name)
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD").strip()


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {
        **os.environ,
        "GIT_CONFIG_NOSYSTEM": "1",
        "HOME": str(repo),
    }
    subprocess.run(
        ["git", "init", "-q", "-b", "main", str(repo)],
        check=True,
        text=True,
        env=env,
    )
    return repo


# --------------------------------------------------------------------------
# is_git_worktree
# --------------------------------------------------------------------------


def test_is_git_worktree_true_for_repo(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    assert is_git_worktree(repo) is True


def test_is_git_worktree_false_for_plain_dir(tmp_path: Path) -> None:
    assert is_git_worktree(tmp_path) is False


# --------------------------------------------------------------------------
# resolve_git_ref
# --------------------------------------------------------------------------


def test_resolve_git_ref_returns_full_sha(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    sha = _commit(repo, "init", content_name="README.md")
    assert resolve_git_ref(repo, "HEAD") == sha


def test_resolve_git_ref_rejects_unresolvable(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _commit(repo, "init", content_name="README.md")
    with pytest.raises(LaunchError):
        resolve_git_ref(repo, "no-such-ref-xyz")


def test_resolve_git_ref_rejects_non_worktree(tmp_path: Path) -> None:
    with pytest.raises(LaunchError):
        resolve_git_ref(tmp_path, "HEAD")


# --------------------------------------------------------------------------
# §14.1 basic range
# --------------------------------------------------------------------------


def test_basic_range_tag_to_head(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _commit(repo, "root", content_name="README.md")
    _git(repo, "tag", "v0.1.0")
    a = _commit(repo, "feat: add a", content_name="a.txt")
    b = _commit(repo, "feat: add b", content_name="b.txt")
    candidates = collect_git_candidates(repo, base_ref="v0.1.0", head_ref="HEAD")
    shas = [c.sha for c in candidates]
    assert shas == [a, b]
    assert {c.source_ref for c in candidates} == {f"git:{a}", f"git:{b}"}
    # inferred kind from conventional prefix
    kinds = {c.sha: c.inferred_kind for c in candidates}
    assert kinds[a] == "added" and kinds[b] == "added"


def test_basic_range_empty_when_base_equals_head(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _commit(repo, "root", content_name="README.md")
    _git(repo, "tag", "v0.1.0")
    candidates = collect_git_candidates(repo, base_ref="v0.1.0", head_ref="HEAD")
    assert candidates == []


# --------------------------------------------------------------------------
# §14.2 user branch graph — a, b, c, d (NOT first-parent)
# --------------------------------------------------------------------------


def test_user_branch_graph_includes_all_branch_commits(tmp_path: Path) -> None:
    """
    main: tag v0.1.0
    branch f1
    f1: commit a, commit b
    main: commit c
    merge f1 (no-ff)
    main: commit d
    """
    repo = _init_repo(tmp_path)
    _commit(repo, "root", content_name="README.md")
    _git(repo, "tag", "v0.1.0")
    # commit c on main
    c = _commit(repo, "feat: add c", content_name="c.txt")
    # branch f1 from v0.1.0
    _git(repo, "branch", "f1", "v0.1.0")
    _git(repo, "checkout", "-q", "f1")
    a = _commit(repo, "feat: add a", content_name="a.txt")
    b = _commit(repo, "feat: add b", content_name="b.txt")
    _git(repo, "checkout", "-q", "main")
    # merge f1 into main (no-ff creates a merge commit)
    _git(repo, "merge", "--no-ff", "f1", "-m", "Merge branch f1")
    d = _commit(repo, "feat: add d", content_name="d.txt")

    candidates = collect_git_candidates(
        repo, base_ref="v0.1.0", head_ref="HEAD", include_merges="never"
    )
    shas = [cand.sha for cand in candidates]
    # a, b, c, d must ALL be present (first-parent would miss a and b).
    for expected in (a, b, c, d):
        assert expected in shas, f"missing {expected} in {shas}"
    # merge commit excluded by policy.
    assert all(not cand.is_merge for cand in candidates)


def test_user_branch_graph_nontrivial_skips_merge_keeps_commits(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    _commit(repo, "root", content_name="README.md")
    _git(repo, "tag", "v0.1.0")
    c = _commit(repo, "feat: add c", content_name="c.txt")
    _git(repo, "branch", "f1", "v0.1.0")
    _git(repo, "checkout", "-q", "f1")
    a = _commit(repo, "feat: add a", content_name="a.txt")
    b = _commit(repo, "feat: add b", content_name="b.txt")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "--no-ff", "f1", "-m", "Merge branch f1")
    d = _commit(repo, "feat: add d", content_name="d.txt")
    candidates = collect_git_candidates(
        repo, base_ref="v0.1.0", head_ref="HEAD", include_merges="nontrivial"
    )
    shas = {cand.sha for cand in candidates}
    assert {a, b, c, d}.issubset(shas)
    assert all(not cand.is_merge for cand in candidates)


def test_user_branch_graph_always_includes_merge(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _commit(repo, "root", content_name="README.md")
    _git(repo, "tag", "v0.1.0")
    _commit(repo, "feat: add c", content_name="c.txt")
    _git(repo, "branch", "f1", "v0.1.0")
    _git(repo, "checkout", "-q", "f1")
    _commit(repo, "feat: add a", content_name="a.txt")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "--no-ff", "f1", "-m", "Merge branch f1")
    _commit(repo, "feat: add d", content_name="d.txt")
    candidates = collect_git_candidates(
        repo, base_ref="v0.1.0", head_ref="HEAD", include_merges="always"
    )
    assert any(cand.is_merge for cand in candidates)


# --------------------------------------------------------------------------
# §14.3 squash merge — final range has s, not stale a/b
# --------------------------------------------------------------------------


def test_squash_merge_drops_original_branch_commits(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _commit(repo, "root", content_name="README.md")
    _git(repo, "tag", "v0.1.0")
    _git(repo, "branch", "f1", "v0.1.0")
    _git(repo, "checkout", "-q", "f1")
    a = _commit(repo, "feat: add a", content_name="a.txt")
    b = _commit(repo, "feat: add b", content_name="b.txt")
    _git(repo, "checkout", "-q", "main")
    # squash merge: produces a single new commit s on main
    _git(repo, "merge", "--squash", "f1")
    _git(repo, "commit", "-m", "feat: squash f1 work")
    s = _git(repo, "rev-parse", "HEAD").strip()
    _commit(repo, "feat: add d", content_name="d.txt")

    candidates = collect_git_candidates(
        repo, base_ref="v0.1.0", head_ref="HEAD", include_merges="never"
    )
    shas = {cand.sha for cand in candidates}
    assert s in shas
    # original branch commits a/b are NOT reachable from HEAD -> excluded.
    assert a not in shas
    assert b not in shas


# --------------------------------------------------------------------------
# §14.8 rebased branch — final range has a2, not a
# --------------------------------------------------------------------------


def test_rebased_branch_includes_rebased_commit_not_original(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _commit(repo, "root", content_name="README.md")
    _git(repo, "tag", "v0.1.0")
    _git(repo, "branch", "f1", "v0.1.0")
    _git(repo, "checkout", "-q", "f1")
    a = _commit(repo, "feat: add a", content_name="a.txt")
    _git(repo, "checkout", "-q", "main")
    # advance main so a rebase is required for f1
    _commit(repo, "feat: add c", content_name="c.txt")
    # rebase f1 onto main, then fast-forward merge
    _git(repo, "checkout", "-q", "f1")
    _git(repo, "rebase", "main")
    a2 = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "--ff-only", "f1")
    _commit(repo, "feat: add d", content_name="d.txt")

    candidates = collect_git_candidates(
        repo, base_ref="v0.1.0", head_ref="HEAD", include_merges="never"
    )
    shas = {cand.sha for cand in candidates}
    assert a2 in shas
    # original a (pre-rebase) is not reachable from HEAD.
    assert a not in shas


# --------------------------------------------------------------------------
# Candidate field shape
# --------------------------------------------------------------------------


def test_candidate_fields_are_populated(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _commit(repo, "root", content_name="README.md")
    _git(repo, "tag", "v0.1.0")
    sha = _commit(repo, "feat(parser): add new token", content_name="p.txt")
    candidates = collect_git_candidates(repo, base_ref="v0.1.0", head_ref="HEAD")
    assert len(candidates) == 1
    cand = candidates[0]
    assert cand.sha == sha
    assert cand.source_ref == f"git:{sha}"
    assert cand.subject == "feat(parser): add new token"
    assert cand.inferred_kind == "added"
    assert cand.inferred_summary == "add new token"
    assert cand.include_by_default is True
    assert "p.txt" in cand.paths


def test_candidate_extracts_pr_and_issue_refs(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _commit(repo, "root", content_name="README.md")
    _git(repo, "tag", "v0.1.0")
    _commit(
        repo,
        "fix: handle edge case\n\nFixes #42 and closes PR #7. See issues/99.",
        content_name="x.txt",
    )
    candidates = collect_git_candidates(repo, base_ref="v0.1.0", head_ref="HEAD")
    cand = candidates[0]
    assert "github:pr-42" in cand.pr_refs
    assert "github:pr-7" in cand.pr_refs
    assert "github:issue-99" in cand.issue_refs


# --------------------------------------------------------------------------
# build_git_range_summary
# --------------------------------------------------------------------------


def test_range_summary_counts_merges(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _commit(repo, "root", content_name="README.md")
    _git(repo, "tag", "v0.1.0")
    _commit(repo, "feat: add c", content_name="c.txt")
    _git(repo, "branch", "f1", "v0.1.0")
    _git(repo, "checkout", "-q", "f1")
    _commit(repo, "feat: add a", content_name="a.txt")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "--no-ff", "f1", "-m", "Merge branch f1")
    _commit(repo, "feat: add d", content_name="d.txt")
    summary = build_git_range_summary(
        repo, base_ref="v0.1.0", head_ref="HEAD", include_merges="nontrivial"
    )
    assert summary["commit_count"] == 4  # a, b-na, c, d  -> actually a,c,merge,d
    # 4 commits in range: c, a, merge, d
    assert summary["merge_commit_count"] == 1
    assert summary["merge_commits_skipped"] == 1
    assert summary["candidate_count"] == 3  # a, c, d (merge skipped)


# --------------------------------------------------------------------------
# net diff helpers
# --------------------------------------------------------------------------


def test_net_diff_paths(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _commit(repo, "root", content_name="README.md")
    _git(repo, "tag", "v0.1.0")
    _commit(repo, "feat: add x", content_name="x.txt")
    paths = net_diff_paths(repo, base_ref="v0.1.0", head_ref="HEAD")
    assert "x.txt" in paths


# --------------------------------------------------------------------------
# Error handling
# --------------------------------------------------------------------------


def test_collect_rejects_non_ancestor_base(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _commit(repo, "root", content_name="README.md")
    _git(repo, "tag", "v0.1.0")
    _commit(repo, "feat: add a", content_name="a.txt")
    # reset back so HEAD is behind, making the new base NOT an ancestor.
    _git(repo, "branch", "other")
    _git(repo, "reset", "--hard", "v0.1.0")
    _commit(repo, "feat: add b", content_name="b.txt")
    # base 'other' (has a) is not an ancestor of HEAD (has b).
    with pytest.raises(LaunchError) as exc_info:
        collect_git_candidates(repo, base_ref="other", head_ref="HEAD")
    assert "not an ancestor" in exc_info.value.message


def test_collect_allows_diverged_base(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _commit(repo, "root", content_name="README.md")
    _git(repo, "tag", "v0.1.0")
    _commit(repo, "feat: add a", content_name="a.txt")
    _git(repo, "branch", "other")
    _git(repo, "reset", "--hard", "v0.1.0")
    _commit(repo, "feat: add b", content_name="b.txt")
    # With allow_diverged_base, the call should not raise on ancestry.
    # Note: a non-ancestor base with rev-list base..head still works in git
    # (returns commits in head not in base), so this should succeed.
    candidates = collect_git_candidates(
        repo, base_ref="other", head_ref="HEAD", allow_diverged_base=True
    )
    assert isinstance(candidates, list)


def test_collect_rejects_invalid_merge_policy(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _commit(repo, "root", content_name="README.md")
    _git(repo, "tag", "v0.1.0")
    with pytest.raises(LaunchError):
        collect_git_candidates(
            repo, base_ref="v0.1.0", head_ref="HEAD", include_merges="bogus"
        )


def test_collect_enforces_clean_worktree(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _commit(repo, "root", content_name="README.md")
    _git(repo, "tag", "v0.1.0")
    (repo / "dirty.txt").write_text("uncommitted\n")
    _git(repo, "add", "dirty.txt")
    with pytest.raises(LaunchError) as exc_info:
        collect_git_candidates(
            repo, base_ref="v0.1.0", head_ref="HEAD", require_clean_worktree=True
        )
    assert "dirty" in exc_info.value.message.lower()
