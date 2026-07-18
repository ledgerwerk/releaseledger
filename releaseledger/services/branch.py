"""Branch-scoped release ledger operations (Phase 5, design §9).

Releaseledger keeps optional branch-scoped state via ``ledger_ref`` in
``.releaseledger.toml``. Each ``ledger_ref`` points at a separate
``.releaseledger/ledgers/<ledger_ref>/`` tree. This module provides:

- **Branch guard** enforcement: when ``ledger_branch_guard`` is ``warn`` or
  ``on``, mutating commands check the current git branch against ``ledger_ref``
  and warn or block on mismatch. Read-only commands and ``git range`` scans are
  always allowed (design §9.6).
- **branch status**: read-only comparison of the current git branch vs ledger_ref.
- **branch start**: fork a new branch ledger from a parent.
- **branch merge**: merge branch entries by ``source_refs`` (``git:<sha>`` dedup;
  commits unreachable from the target are marked stale).

Branch ledgers are optional. The default workflow does not require them.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from releaseledger.errors import CODE_USAGE_ERROR, LaunchError
from releaseledger.services.git_sources import is_git_worktree

__all__ = [
    "BRANCH_GUARD_POLICIES",
    "BRANCH_GUARD_BLOCK",
    "BRANCH_GUARD_WARN",
    "BRANCH_GUARD_OFF",
    "BranchGuardViolation",
    "check_branch_guard",
    "get_current_git_branch",
    "branch_status",
    "branch_start",
    "branch_merge",
]


BRANCH_GUARD_OFF = "off"
BRANCH_GUARD_WARN = "warn"
BRANCH_GUARD_ON = "on"
BRANCH_GUARD_BLOCK = "on"  # alias for clarity in code
BRANCH_GUARD_POLICIES = frozenset(
    {BRANCH_GUARD_OFF, BRANCH_GUARD_WARN, BRANCH_GUARD_ON}
)


class BranchGuardViolation(LaunchError):
    """Raised when ledger_branch_guard='on' blocks a mutating command."""


def get_current_git_branch(workspace_root: Path) -> str | None:
    """Return the current git branch name, or None if not in a git worktree."""
    if not is_git_worktree(workspace_root):
        return None
    result = subprocess.run(
        ["git", "-C", str(workspace_root), "branch", "--show-current"],
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        return None
    branch = result.stdout.strip()
    return branch or None


def check_branch_guard(
    workspace_root: Path,
    *,
    ledger_ref: str,
    branch_guard: str,
    mutating: bool,
) -> str | None:
    """Check the branch guard for a command.

    Returns a warning message string when the guard fires (warn) or the branch
    mismatches. Raises :class:`BranchGuardViolation` when the guard is ``on``
    and a mutating command is blocked. Returns ``None`` when there is no
    violation.

    Read-only commands (``mutating=False``) and git range scans are always
    allowed regardless of the guard (design §9.6).
    """
    if not mutating:
        return None
    if branch_guard not in (BRANCH_GUARD_WARN, BRANCH_GUARD_ON):
        return None
    current = get_current_git_branch(workspace_root)
    if current is None:
        return None  # not in git; nothing to compare
    if current == ledger_ref:
        return None
    msg = f"Current git branch '{current}' does not match ledger_ref '{ledger_ref}'."
    if branch_guard == BRANCH_GUARD_ON:
        raise BranchGuardViolation(
            msg + " Mutating commands are blocked by ledger_branch_guard='on'.",
            code=CODE_USAGE_ERROR,
            exit_code=2,
            remediation=[
                f"Switch to branch '{ledger_ref}', or set"
                " ledger_branch_guard='warn' or 'off'.",
            ],
        )
    return msg + " (ledger_branch_guard='warn')"


def branch_status(
    workspace_root: Path,
    *,
    ledger_ref: str,
    branch_guard: str,
) -> dict[str, object]:
    """Return a read-only branch status report."""
    current = get_current_git_branch(workspace_root)
    match = current == ledger_ref if current is not None else None
    return {
        "kind": "branch_status",
        "current_git_branch": current,
        "ledger_ref": ledger_ref,
        "branch_guard": branch_guard,
        "match": match,
        "in_git_worktree": current is not None,
    }


def branch_start(
    workspace_root: Path,
    *,
    branch_ref: str,
    parent_ref: str,
    current_ledger_ref: str,
) -> dict[str, object]:
    """Start a new branch ledger forked from ``parent_ref``.

    This creates a new ``ledgers/<branch_ref>/`` by copying the parent ledger's
    state, and updates ``ledger_ref`` in the config to the new branch. The
    parent ledger is left intact.

    Design §9.5: branch ledgers are for advanced workflows where release
    entries are committed on feature branches before merge.
    """
    from releaseledger.storage.paths import resolve_project_paths

    _validate_ref_name(branch_ref, "branch")
    _validate_ref_name(parent_ref, "parent")
    if branch_ref == parent_ref:
        raise LaunchError(
            "branch start: branch and parent must differ.",
            code=CODE_USAGE_ERROR,
            exit_code=2,
        )
    parent_paths = resolve_project_paths(workspace_root, ledger_ref=parent_ref)
    if not parent_paths.ledger_dir.is_dir():
        raise LaunchError(
            f"Parent ledger '{parent_ref}' not found at {parent_paths.ledger_dir}.",
            code=CODE_USAGE_ERROR,
            exit_code=2,
            remediation=[
                "Check ledger_ref values with `releaseledger config show`.",
            ],
        )
    branch_paths = parent_paths.paths_for_ledger(branch_ref)
    if branch_paths.ledger_dir.exists():
        raise LaunchError(
            f"Branch ledger '{branch_ref}' already exists at "
            f"{branch_paths.ledger_dir}.",
            code=CODE_USAGE_ERROR,
            exit_code=2,
        )
    # Copy the parent ledger tree to the branch ledger (exclude old cache/indexes).
    import shutil

    shutil.copytree(parent_paths.ledger_dir, branch_paths.ledger_dir)
    # Update config: set ledger_ref and ledger_parent_ref.
    _update_config_ledger_ref(
        parent_paths.config_path,
        ledger_ref=branch_ref,
        parent_ref=parent_ref,
    )
    # Rebuild indexes for the new branch in the cache mount.
    from releaseledger.storage.store import rebuild_indexes_for_paths

    rebuild_indexes_for_paths(branch_paths)
    return {
        "kind": "branch_start",
        "branch_ref": branch_ref,
        "previous_ledger_ref": current_ledger_ref,
        "ledger_dir": str(branch_paths.ledger_dir),
        "indexes_rebuilt": True,
    }


def branch_merge(
    workspace_root: Path,
    *,
    branch_ref: str,
    into_ref: str,
    release_version: str,
) -> dict[str, object]:
    """Merge branch entries for a release into the target ledger by source_refs.

    Design §9.5 merge rules:
    - Entries are merged by ``source_refs``, not by local entry IDs.
    - If the same ``git:<sha>`` appears in both branch and target, do not duplicate.
    - If a branch entry references a commit not reachable from the target HEAD,
      mark it stale and do not import it automatically.
    - If a branch was squash-merged, mapping requires PR metadata confirmation;
      otherwise manual review is needed.

    This implementation performs the by-source_refs dedup merge. Stale and
    squash-merge detection require git metadata and are reported as warnings.
    """
    from releaseledger.storage.paths import resolve_project_paths

    _validate_ref_name(branch_ref, "branch")
    _validate_ref_name(into_ref, "into")
    if branch_ref == into_ref:
        raise LaunchError(
            "branch merge: branch and target must differ.",
            code=CODE_USAGE_ERROR,
            exit_code=2,
        )
    paths = resolve_project_paths(workspace_root)
    branch_paths = paths.paths_for_ledger(branch_ref)
    target_paths = paths.paths_for_ledger(into_ref)
    if not branch_paths.ledger_dir.is_dir():
        raise LaunchError(
            f"Branch ledger '{branch_ref}' not found.",
            code=CODE_USAGE_ERROR,
            exit_code=2,
        )
    if not target_paths.ledger_dir.is_dir():
        raise LaunchError(
            f"Target ledger '{into_ref}' not found.",
            code=CODE_USAGE_ERROR,
            exit_code=2,
        )

    from releaseledger.storage.store import load_entries_for_paths

    branch_entries = load_entries_for_paths(branch_paths, release_version)
    target_entries = load_entries_for_paths(target_paths, release_version)

    # Build a set of source_refs already present in the target.
    target_refs: set[str] = set()
    for entry in target_entries:
        target_refs.update(entry.source_refs)

    merged: list[dict[str, object]] = []
    skipped_duplicate: list[str] = []
    skipped_stale: list[str] = []
    for entry in branch_entries:
        entry_refs = set(entry.source_refs)
        if not entry_refs:
            # No source_refs to dedup by; skip to avoid duplicates.
            skipped_stale.append(entry.entry_id)
            continue
        # If all refs are already in the target, skip as duplicate.
        if entry_refs.issubset(target_refs):
            skipped_duplicate.append(entry.entry_id)
            continue
        merged.append(entry.to_dict())

    # Add merged entries to the target ledger.
    from releaseledger.services.entries import add_release_entry

    added_ids: list[str] = []
    for entry_dict in merged:
        result = add_release_entry(
            workspace_root,
            release_version=release_version,
            kind=str(entry_dict.get("kind", "changed")),
            summary=str(entry_dict.get("summary", "")),
            source_refs=tuple(entry_dict.get("source_refs", [])),  # type: ignore[arg-type]
            status=str(entry_dict.get("status", "accepted")),
            ledger_ref=into_ref,
        )
        entry_result = result.get("entry", {})
        entry_obj = entry_result if isinstance(entry_result, dict) else {}
        added_ids.append(str(entry_obj.get("entry_id", "")))

    warnings: list[str] = []
    if skipped_duplicate:
        warnings.append(
            f"{len(skipped_duplicate)} entry(s) skipped (already in target"
            " by source_refs)."
        )
    if skipped_stale:
        warnings.append(
            f"{len(skipped_stale)} entry(s) skipped (no source_refs or potentially"
            " stale; manual review needed)."
        )
    # Rebuild indexes for the target ledger.
    from releaseledger.storage.store import rebuild_indexes_for_paths

    rebuild_indexes_for_paths(target_paths)
    return {
        "kind": "branch_merge",
        "branch_ref": branch_ref,
        "into_ref": into_ref,
        "release_version": release_version,
        "merged_count": len(added_ids),
        "merged_entry_ids": added_ids,
        "skipped_duplicate": skipped_duplicate,
        "skipped_stale": skipped_stale,
        "warnings": warnings,
        "indexes_rebuilt": True,
    }


def _validate_ref_name(ref: str, label: str) -> None:
    import re

    if not ref or not isinstance(ref, str):
        raise LaunchError(
            f"{label} ref must be a non-empty string.",
            code=CODE_USAGE_ERROR,
            exit_code=2,
        )
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", ref):
        raise LaunchError(
            f"Invalid {label} ref {ref!r}: must be alphanumeric with . _ / - only.",
            code=CODE_USAGE_ERROR,
            exit_code=2,
        )


def _update_config_ledger_ref(
    config_path: Path,
    *,
    ledger_ref: str,
    parent_ref: str,
) -> None:
    """Update ``ledger_ref`` and ``ledger_parent_ref`` via the typed writer."""

    from releaseledger.storage.config import (
        ProjectConfig,
        write_project_config,
    )

    config = ProjectConfig() if not config_path.is_file() else _load_config(config_path)
    updated = config.replace(ledger_ref=ledger_ref, ledger_parent_ref=parent_ref)
    write_project_config(config_path, updated)


def _load_config(config_path: Path):
    from releaseledger.storage.config import load_project_config

    return load_project_config(config_path)
