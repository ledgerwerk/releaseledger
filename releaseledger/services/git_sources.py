"""Git-first release evidence source.

Releaseledger is git-first: the authoritative evidence of shipped changes is the
git commit range between the previous shipped release (``base``) and the new
release target (``head``). This module reads that range using only the standard
library ``subprocess`` and turns each reachable commit into a
:class:`GitSourceCandidate` whose ``source_ref`` (``git:<full-sha>``) is a
first-class coverable source ref.

Coverage rule (design doc §3.1, §9.3):

    git rev-list --reverse --topo-order <base>..<head>

i.e. every commit reachable from ``head`` but not from ``base``. This is correct
for normal merges, rebases, squashes, and amends — it tracks what actually
shipped. First-parent is intentionally NOT used as the default: it would miss
branch commits (design §3.2).

Merge policy (design §6.5):

    include_merges = "never"      skip all merge commits
    include_merges = "always"     include all merge commits
    include_merges = "nontrivial" (default) skip merge commits as candidate
                                              entries but preserve PR metadata

Nontrivial conflict-resolution detection is deferred to a later phase; the
initial implementation skips merges while keeping their PR metadata for grouping.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from releaseledger.errors import CODE_USAGE_ERROR, LaunchError

__all__ = [
    "GIT_DEFAULT_HEAD",
    "GIT_DEFAULT_INCLUDE_MERGES",
    "GIT_DEFAULT_MAX_DIFF_CHARS",
    "GIT_DEFAULT_MAX_COMMITS",
    "GitSourceCandidate",
    "MERGE_POLICIES",
    "build_git_range_summary",
    "collect_git_candidates",
    "is_git_worktree",
    "resolve_git_ref",
]


GIT_DEFAULT_HEAD = "HEAD"
GIT_DEFAULT_INCLUDE_MERGES = "nontrivial"
GIT_DEFAULT_MAX_DIFF_CHARS = 12000
GIT_DEFAULT_MAX_COMMITS = 500
MERGE_POLICIES = ("never", "always", "nontrivial")

# Conventional-commit prefix -> releaseledger entry kind inference.
# Used for candidate entries; review/strict only WARNs on uncertain inference.
_CONVENTIONAL_KIND_MAP = (
    (re.compile(r"^\w+\(\)", re.IGNORECASE), None),  # scoped, decide by type
    (re.compile(r"^feat(\(.+\))?!?:", re.IGNORECASE), "added"),
    (re.compile(r"^fix(\(.+\))?!?:", re.IGNORECASE), "fixed"),
    (re.compile(r"^docs?(\(.+\))?!?:", re.IGNORECASE), "docs"),
    (re.compile(r"^perf(\(.+\))?!?:", re.IGNORECASE), "changed"),
    (re.compile(r"^refactor(\(.+\))?!?:", re.IGNORECASE), "changed"),
    (re.compile(r"^revert(\(.+\))?!?:", re.IGNORECASE), "fixed"),
    (re.compile(r"^chore(\(.+\))?!?:", re.IGNORECASE), "internal"),
    (re.compile(r"^test(\(.+\))?!?:", re.IGNORECASE), "internal"),
    (re.compile(r"^build(\(.+\))?!?:", re.IGNORECASE), "internal"),
    (re.compile(r"^ci(\(.+\))?!?:", re.IGNORECASE), "internal"),
    (re.compile(r"^style(\(.+\))?!?:", re.IGNORECASE), "internal"),
)

# Free-text subject heuristics when there is no conventional prefix.
_SUBJECT_KIND_MAP = (
    (re.compile(r"\badd(ed|ing)?\b", re.IGNORECASE), "added"),
    (re.compile(r"\bnew\b", re.IGNORECASE), "added"),
    (re.compile(r"\bfix(ed|es|ing)?\b", re.IGNORECASE), "fixed"),
    (re.compile(r"\bbug\b", re.IGNORECASE), "fixed"),
    (re.compile(r"\bremov(e|ed|ing|al)\b", re.IGNORECASE), "removed"),
    (re.compile(r"\bdeprecat(e|ed|ing|ion)\b", re.IGNORECASE), "removed"),
    (re.compile(r"\bdocument(ed|ing|ation)\b", re.IGNORECASE), "docs"),
    (re.compile(r"\brefactor", re.IGNORECASE), "changed"),
    (re.compile(r"\bupdat(e|ed|ing)\b", re.IGNORECASE), "changed"),
    (re.compile(r"\bchang(e|ed|ing)\b", re.IGNORECASE), "changed"),
    (re.compile(r"\bimprov(e|ed|ing|ement)\b", re.IGNORECASE), "changed"),
)

# PR/issue reference patterns in commit subjects/bodies (GitHub style).
_PR_HASH_RE = re.compile(r"#(\d+)")
_ISSUE_REF_RE = re.compile(r"\bissues?/(\d+)\b", re.IGNORECASE)


@dataclass(frozen=True)
class _CommitMeta:
    """Internal typed commit metadata (NUL-delimited git show -s parse)."""

    sha: str
    short_sha: str
    parents: tuple[str, ...]
    author_name: str
    author_email: str
    authored_at: str
    committed_at: str
    subject: str
    body: str


@dataclass(frozen=True)
class GitSourceCandidate:
    """One reachable commit in a release range, as release-note evidence."""

    sha: str
    short_sha: str
    source_ref: str  # "git:<full-sha>"
    subject: str
    body: str
    author_name: str
    author_email: str
    authored_at: str
    committed_at: str
    parents: tuple[str, ...]
    is_merge: bool
    include_by_default: bool
    paths: tuple[str, ...]
    additions: int | None
    deletions: int | None
    pr_refs: tuple[str, ...]
    issue_refs: tuple[str, ...]
    inferred_kind: str
    inferred_summary: str
    diff_excerpt: str | None


# --------------------------------------------------------------------------
# Low-level subprocess helper
# --------------------------------------------------------------------------


def _run_git(workspace_root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a git command capturing text output (never raises CalledProcessError)."""
    return subprocess.run(
        ["git", "-C", str(workspace_root), *args],
        check=False,
        text=True,
        capture_output=True,
    )


def _require_git_available(
    result: subprocess.CompletedProcess[str], *, what: str
) -> None:
    """Turn a git invocation failure into an actionable LaunchError."""
    if result.returncode == 0:
        return
    err = (result.stderr or "").strip()
    low = err.lower()
    if "not a git repository" in low or "not a git worktree" in low:
        raise LaunchError(
            f"{what}: {workspace_root_hint()}{err}",
            code=CODE_USAGE_ERROR,
            exit_code=2,
            remediation=[
                "Run releaseledger inside a git worktree.",
                "Initialize one with `git init` if needed.",
            ],
        )
    if "usage:" in low and "git" in low:
        raise LaunchError(
            f"{what}: git rejected the request: {err}",
            code=CODE_USAGE_ERROR,
            exit_code=2,
        )
    raise LaunchError(
        f"{what}: git exited {result.returncode}: {err}",
        code=CODE_USAGE_ERROR,
        exit_code=2,
    )


def workspace_root_hint() -> str:
    return ""


def _check_git_installed() -> None:
    """Raise a clear error if git is not installed at all."""
    try:
        result = subprocess.run(
            ["git", "--version"],
            check=False,
            text=True,
            capture_output=True,
        )
    except FileNotFoundError as exc:
        raise LaunchError(
            "git is not installed or not on PATH; git-first features require git.",
            code=CODE_USAGE_ERROR,
            exit_code=2,
            remediation=["Install git and ensure `git --version` works."],
        ) from exc
    if result.returncode != 0:
        raise LaunchError(
            f"git is not usable: {result.stderr.strip()}",
            code=CODE_USAGE_ERROR,
            exit_code=2,
        )


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def is_git_worktree(workspace_root: Path) -> bool:
    """Return True when ``workspace_root`` is inside a git worktree."""
    result = _run_git(workspace_root, ["rev-parse", "--is-inside-work-tree"])
    if result.returncode != 0:
        return False
    return result.stdout.strip().lower() == "true"


def resolve_git_ref(workspace_root: Path, ref: str) -> str:
    """Return the full 40-char commit SHA for ``ref``.

    Raises :class:`LaunchError` with remediation when git is missing, the
    workspace is not a worktree, or ``ref`` cannot be resolved to a commit.
    """
    _check_git_installed()
    if not is_git_worktree(workspace_root):
        raise LaunchError(
            f"Not a git worktree: {workspace_root}",
            code=CODE_USAGE_ERROR,
            exit_code=2,
            remediation=["Run releaseledger inside a git worktree."],
        )
    result = _run_git(workspace_root, ["rev-parse", "--verify", f"{ref}^{{commit}}"])
    if result.returncode != 0:
        raise LaunchError(
            f"Cannot resolve git ref {ref!r}: {(result.stderr or '').strip()}",
            code=CODE_USAGE_ERROR,
            exit_code=2,
            remediation=[
                f"Check the ref with `git rev-parse --verify {ref}^{{commit}}`.",
            ],
        )
    sha = result.stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise LaunchError(
            f"Resolved ref {ref!r} is not a 40-char commit SHA: {sha!r}",
            code=CODE_USAGE_ERROR,
            exit_code=2,
        )
    return sha


def _verify_ancestry(
    workspace_root: Path, *, base_sha: str, head_sha: str, allow_diverged: bool
) -> None:
    result = _run_git(
        workspace_root, ["merge-base", "--is-ancestor", base_sha, head_sha]
    )
    if result.returncode == 0:
        return
    if result.returncode == 1 and allow_diverged:
        # base is not an ancestor but caller accepted divergence.
        return
    if result.returncode == 1:
        raise LaunchError(
            f"Base commit {base_sha[:7]} is not an ancestor of head {head_sha[:7]}.",
            code=CODE_USAGE_ERROR,
            exit_code=2,
            remediation=[
                "Use a base ref that is an ancestor of the head ref, or pass"
                " --allow-diverged-base.",
            ],
        )
    _require_git_available(result, what="git merge-base --is-ancestor")


def collect_git_candidates(
    workspace_root: Path,
    *,
    base_ref: str,
    head_ref: str = GIT_DEFAULT_HEAD,
    include_merges: str = GIT_DEFAULT_INCLUDE_MERGES,
    max_diff_chars: int = GIT_DEFAULT_MAX_DIFF_CHARS,
    max_commits: int = GIT_DEFAULT_MAX_COMMITS,
    allow_diverged_base: bool = False,
    require_clean_worktree: bool = False,
) -> list[GitSourceCandidate]:
    """Collect release-note candidate commits in ``base_ref..head_ref``.

    Uses ``git rev-list --reverse --topo-order <base>..<head>`` so every commit
    reachable from head but not base is included (correct for merges, rebases,
    squashes, amends). Merge policy controls whether merge commits become
    candidate entries (design §6.5).

    Raises :class:`LaunchError` for: git not installed, not a worktree,
    unresolvable base/head, base not an ancestor (unless
    ``allow_diverged_base``), dirty worktree (when ``require_clean_worktree``),
    and range overruns (``max_commits``).
    """
    if include_merges not in MERGE_POLICIES:
        raise LaunchError(
            f"Invalid include_merges {include_merges!r}; expected one of"
            f" {', '.join(MERGE_POLICIES)}.",
            code=CODE_USAGE_ERROR,
            exit_code=2,
        )
    _check_git_installed()
    if not is_git_worktree(workspace_root):
        raise LaunchError(
            f"Not a git worktree: {workspace_root}",
            code=CODE_USAGE_ERROR,
            exit_code=2,
            remediation=["Run releaseledger inside a git worktree."],
        )
    if require_clean_worktree:
        _require_clean(workspace_root)
    base_sha = resolve_git_ref(workspace_root, base_ref)
    head_sha = resolve_git_ref(workspace_root, head_ref)
    if base_sha == head_sha:
        return []
    _verify_ancestry(
        workspace_root,
        base_sha=base_sha,
        head_sha=head_sha,
        allow_diverged=allow_diverged_base,
    )
    rev_list = _run_git(
        workspace_root,
        ["rev-list", "--reverse", "--topo-order", f"{base_sha}..{head_sha}"],
    )
    _require_git_available(rev_list, what="git rev-list")
    shas = [line.strip() for line in rev_list.stdout.splitlines() if line.strip()]
    if len(shas) > max_commits:
        raise LaunchError(
            f"Git range {base_ref}..{head_ref} contains {len(shas)} commits;"
            f" exceeds max_commits={max_commits}.",
            code=CODE_USAGE_ERROR,
            exit_code=2,
            remediation=[
                "Narrow the range, or raise [git] max_commits in .releaseledger.toml.",
            ],
        )
    candidates: list[GitSourceCandidate] = []
    for sha in shas:
        candidate = _build_candidate(
            workspace_root,
            sha=sha,
            include_merges=include_merges,
            max_diff_chars=max_diff_chars,
        )
        if candidate is None:
            continue
        candidates.append(candidate)
    return candidates


def _require_clean(workspace_root: Path) -> None:
    status = _run_git(workspace_root, ["status", "--porcelain"])
    _require_git_available(status, what="git status --porcelain")
    if status.stdout.strip():
        raise LaunchError(
            "Worktree is dirty and require_clean_worktree is enabled.",
            code=CODE_USAGE_ERROR,
            exit_code=2,
            remediation=[
                "Commit or stash changes before scanning, or set"
                " [git] require_clean_worktree = false.",
            ],
        )


def _build_candidate(
    workspace_root: Path,
    *,
    sha: str,
    include_merges: str,
    max_diff_chars: int,
) -> GitSourceCandidate | None:
    meta = _commit_metadata(workspace_root, sha)
    is_merge = len(meta.parents) >= 2
    # Merge policy: never/always are explicit. nontrivial skips merges as
    # candidate entries but keeps their PR metadata available to the caller via
    # the returned candidate list when include_merges == "always"; for
    # "nontrivial"/"never" the merge is skipped from candidate entries.
    if is_merge:
        if include_merges == "never" or include_merges == "nontrivial":
            # Skipped from candidate entries. PR metadata is preserved on the
            # commit but not surfaced as a candidate (design §6.5).
            return None
        # include_merges == "always": fall through and include it.
    paths, additions, deletions = _changed_paths(workspace_root, sha)
    pr_refs, issue_refs = _extract_refs(meta.subject, meta.body)
    inferred_kind = _infer_kind(meta.subject)
    # Intentionally blank: commit subjects are evidence, not changelog prose.
    # Agents must write release-entry summaries from reviewed behavior/diffs.
    inferred_summary = ""
    diff_excerpt = _diff_excerpt(workspace_root, sha, max_diff_chars)
    return GitSourceCandidate(
        sha=sha,
        short_sha=sha[:7],
        source_ref=f"git:{sha}",
        subject=meta.subject,
        body=meta.body,
        author_name=meta.author_name,
        author_email=meta.author_email,
        authored_at=meta.authored_at,
        committed_at=meta.committed_at,
        parents=meta.parents,
        is_merge=is_merge,
        include_by_default=True,
        paths=tuple(paths),
        additions=additions,
        deletions=deletions,
        pr_refs=tuple(pr_refs),
        issue_refs=tuple(issue_refs),
        inferred_kind=inferred_kind,
        inferred_summary=inferred_summary,
        diff_excerpt=diff_excerpt,
    )


# %H%x00%h%x00%P%x00%an%x00%ae%x00%aI%x00%cI%x00%s%x00%b
_META_FIELDS = (
    "sha",
    "short_sha",
    "parents",
    "author_name",
    "author_email",
    "authored_at",
    "committed_at",
    "subject",
    "body",
)


def _commit_metadata(workspace_root: Path, sha: str) -> _CommitMeta:
    fmt = "%H%x00%h%x00%P%x00%an%x00%ae%x00%aI%x00%cI%x00%s%x00%b"
    result = _run_git(workspace_root, ["show", "-s", f"--format={fmt}", sha])
    _require_git_available(result, what=f"git show -s {sha}")
    raw = result.stdout
    # %b can contain newlines; only split on NUL.
    parts = raw.split("\x00")
    # Pad in case %b is empty (trailing NUL may be absent).
    while len(parts) < len(_META_FIELDS):
        parts.append("")
    values = list(zip(_META_FIELDS, parts, strict=False))
    fields = dict(values)
    parents = tuple(p for p in str(fields.get("parents", "")).split() if p)
    return _CommitMeta(
        sha=str(fields.get("sha", sha)),
        short_sha=str(fields.get("short_sha", sha[:7])),
        parents=parents,
        author_name=str(fields.get("author_name", "")),
        author_email=str(fields.get("author_email", "")),
        authored_at=str(fields.get("authored_at", "")),
        committed_at=str(fields.get("committed_at", "")),
        subject=str(fields.get("subject", "")).rstrip("\n"),
        body=str(fields.get("body", "")).rstrip("\n"),
    )


def _changed_paths(
    workspace_root: Path, sha: str
) -> tuple[list[str], int | None, int | None]:
    """Return (paths, additions, deletions) for ``sha``."""
    # name-status for the path list (handles renames via -M).
    ns = _run_git(
        workspace_root,
        ["diff-tree", "--no-commit-id", "--name-status", "-r", "-M", sha],
    )
    _require_git_available(ns, what=f"git diff-tree {sha}")
    paths: list[str] = []
    for line in ns.stdout.splitlines():
        if not line.strip():
            continue
        # name-status lines look like "M\tpath" or "R100\told\tnew".
        bits = line.split("\t")
        if len(bits) >= 2:
            # take the final path (the new path for renames).
            paths.append(bits[-1])
    # numstat for additions/deletions when available.
    numstat = _run_git(
        workspace_root,
        ["diff-tree", "--no-commit-id", "--numstat", "-r", "-M", sha],
    )
    additions: int | None = None
    deletions: int | None = None
    if numstat.returncode == 0:
        add_total = 0
        del_total = 0
        seen_any = False
        for line in numstat.stdout.splitlines():
            cells = line.split("\t")
            if len(cells) < 3:
                continue
            a, d = cells[0], cells[1]
            if a == "-" or d == "-":
                # binary file; cannot count.
                continue
            try:
                add_total += int(a)
                del_total += int(d)
                seen_any = True
            except ValueError:
                continue
        if seen_any:
            additions = add_total
            deletions = del_total
    return paths, additions, deletions


def _diff_excerpt(workspace_root: Path, sha: str, max_chars: int) -> str | None:
    """Return a bounded patch excerpt for ``sha`` or None if empty."""
    if max_chars <= 0:
        return None
    result = _run_git(
        workspace_root,
        [
            "show",
            "--format=",
            "--find-renames",
            "--find-copies",
            "--patch",
            "--stat",
            sha,
        ],
    )
    if result.returncode != 0:
        return None
    text = result.stdout
    if len(text) > max_chars:
        text = text[:max_chars] + "\n... (truncated)"
    return text or None


def _extract_refs(subject: str, body: str) -> tuple[list[str], list[str]]:
    """Extract GitHub-style PR and issue references from a commit.

    Deterministic model:
      - any ``#N`` token (subject first, then body) -> ``github:pr-N``
      - explicit ``issues/N`` / ``issue/N``          -> ``github:issue-N``

    GitHub treats ``Fixes #42`` ambiguously (issue or PR); releaseledger
    surfaces ``#N`` as a PR reference because that is the most common
    provenance users attach to a shipped commit. Reviewers can refine in
    the curated entry.
    """
    text = f"{subject}\n{body}"
    prs: list[str] = []
    seen_pr: set[str] = set()
    for match in _PR_HASH_RE.finditer(text):
        num = match.group(1)
        ref = f"github:pr-{num}"
        if ref not in seen_pr:
            seen_pr.add(ref)
            prs.append(ref)
    issues: list[str] = []
    seen_issue: set[str] = set()
    for match in _ISSUE_REF_RE.finditer(text):
        num = match.group(1)
        ref = f"github:issue-{num}"
        if ref not in seen_issue:
            seen_issue.add(ref)
            issues.append(ref)
    return prs, issues


def _infer_kind(subject: str) -> str:
    for pattern, kind in _CONVENTIONAL_KIND_MAP:
        if kind is None:
            continue
        if pattern.search(subject):
            return kind
    for pattern, kind in _SUBJECT_KIND_MAP:
        if pattern.search(subject):
            return kind
    return "changed"


def build_git_range_summary(
    workspace_root: Path,
    *,
    base_ref: str,
    head_ref: str = GIT_DEFAULT_HEAD,
    include_merges: str = GIT_DEFAULT_INCLUDE_MERGES,
    allow_diverged_base: bool = False,
    require_clean_worktree: bool = False,
) -> dict[str, object]:
    """Return a deterministic summary of a git release range.

    Includes resolved refs, full SHAs, the ``base..head`` range string, the
    total commit count, the number of merge commits in range, and how many
    candidate entries the merge policy produced.
    """
    _check_git_installed()
    base_sha = resolve_git_ref(workspace_root, base_ref)
    head_sha = resolve_git_ref(workspace_root, head_ref)
    _verify_ancestry(
        workspace_root,
        base_sha=base_sha,
        head_sha=head_sha,
        allow_diverged=allow_diverged_base,
    )
    rev_list = _run_git(
        workspace_root,
        ["rev-list", "--reverse", "--topo-order", f"{base_sha}..{head_sha}"],
    )
    _require_git_available(rev_list, what="git rev-list")
    all_shas = [line.strip() for line in rev_list.stdout.splitlines() if line.strip()]
    merge_count = 0
    for sha in all_shas:
        meta = _commit_metadata(workspace_root, sha)
        if len(meta.parents) >= 2:
            merge_count += 1
    candidates = collect_git_candidates(
        workspace_root,
        base_ref=base_ref,
        head_ref=head_ref,
        include_merges=include_merges,
        allow_diverged_base=allow_diverged_base,
        require_clean_worktree=require_clean_worktree,
    )
    return {
        "kind": "git_range_summary",
        "base_ref": base_ref,
        "base_sha": base_sha,
        "head_ref": head_ref,
        "head_sha": head_sha,
        "range": f"{base_sha}..{head_sha}",
        "commit_count": len(all_shas),
        "merge_commit_count": merge_count,
        "merge_commits_skipped": (
            merge_count if include_merges in ("never", "nontrivial") else 0
        ),
        "candidate_count": len(candidates),
        "include_merges": include_merges,
    }


def net_diff_paths(
    workspace_root: Path, *, base_ref: str, head_ref: str = GIT_DEFAULT_HEAD
) -> list[str]:
    """Return the net-changed paths across ``base..head`` (find-renames).

    Useful for review path-coverage warnings (design §3.3). Does not block
    strict mode unless explicitly enabled.
    """
    base_sha = resolve_git_ref(workspace_root, base_ref)
    head_sha = resolve_git_ref(workspace_root, head_ref)
    result = _run_git(
        workspace_root,
        ["diff", "--name-status", "--find-renames", f"{base_sha}..{head_sha}"],
    )
    _require_git_available(result, what="git diff --name-status")
    paths: list[str] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        bits = line.split("\t")
        if len(bits) >= 2:
            paths.append(bits[-1])
    return paths


def net_diff_stat(
    workspace_root: Path, *, base_ref: str, head_ref: str = GIT_DEFAULT_HEAD
) -> str:
    """Return ``git diff --stat`` for ``base..head``."""
    base_sha = resolve_git_ref(workspace_root, base_ref)
    head_sha = resolve_git_ref(workspace_root, head_ref)
    result = _run_git(workspace_root, ["diff", "--stat", f"{base_sha}..{head_sha}"])
    _require_git_available(result, what="git diff --stat")
    return result.stdout
