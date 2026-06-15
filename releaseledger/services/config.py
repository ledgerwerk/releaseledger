"""Config and storage diagnostics service.

Read-only helpers for agents to inspect project configuration, resolved paths,
and on-disk storage health without mutating state.
"""

from __future__ import annotations

from pathlib import Path

import ledgercore

from releaseledger.errors import LaunchError
from releaseledger.storage.config import (
    ProjectConfig,
    load_project_config,
    render_default_releaseledger_toml,
)
from releaseledger.storage.paths import (
    ProjectLocator,
    find_project_config,
    load_project_locator,
    resolve_project_paths,
    resolve_releaseledger_dir,
)

__all__ = [
    "config_set_releaseledger_dir",
    "config_show",
    "storage_where",
]


def storage_where(workspace_root: Path) -> dict[str, object]:
    """Return a read-only diagnostic dict describing the effective storage location.

    Never mutates state. Safe to call from any working directory that resolves
    to the same project (e.g. a subdirectory).
    """
    locator: ProjectLocator = load_project_locator(workspace_root)
    rldir = locator.releaseledger_dir

    layout_ok = False
    indexes_ok = False
    ledger_ref: str = ""
    ledger_dir: str = ""

    # Only try to resolve further paths when a config exists that the
    # locator could read.  load_project_locator defaults source='default'
    # when no config is found, in which case resolve_project_paths will
    # fail.  Guard against that.
    if locator.config_path.is_file():
        try:
            paths = resolve_project_paths(locator.workspace_root)
            ledger_ref = paths.ledger_ref
            ledger_dir = str(paths.ledger_dir)
            layout_ok = (
                paths.releases_dir.is_dir()
                and paths.events_dir.is_dir()
                and paths.indexes_dir.is_dir()
            )
            indexes_ok = (
                paths.releases_index_path.is_file()
                and paths.entries_index_path.is_file()
            )
        except Exception:  # pragma: no cover - defensive: partial layout
            pass

    root = locator.workspace_root.resolve()
    inside = False
    try:
        rldir.resolve().relative_to(root)
        inside = True
    except ValueError:
        pass

    return {
        "kind": "storage_location",
        "workspace_root": str(root),
        "config_path": str(locator.config_path),
        "releaseledger_dir": str(rldir.resolve()),
        "ledger_ref": ledger_ref,
        "ledger_dir": ledger_dir,
        "inside_workspace": inside,
        "source": locator.source,
        "layout_exists": layout_ok,
        "indexes_exist": indexes_ok,
    }


def config_show(workspace_root: Path) -> dict[str, object]:
    """Return a read-only dict with validated config values and resolved paths."""
    locator = load_project_locator(workspace_root)
    root = locator.workspace_root.resolve()
    config: ProjectConfig | None = None
    if locator.config_path.is_file():
        try:
            config = load_project_config(locator.config_path)
        except LaunchError:
            config = None
    config_dict: dict[str, object] = {}
    if config is not None:
        config_dict = {
            "config_version": config.config_version,
            "releaseledger_dir": config.releaseledger_dir,
            "releaseledger_dir_policy": config.releaseledger_dir_policy,
            "ledger_ref": config.ledger_ref,
            "ledger_parent_ref": config.ledger_parent_ref,
            "ledger_next_entry_number": config.ledger_next_entry_number,
            "ledger_branch_guard": config.ledger_branch_guard,
            "ledger": {
                "code": config.ledger_code,
                "name": config.ledger_name,
            },
            "release": {
                "default_changelog": config.default_changelog,
                "default_status": config.default_status,
                "allow_dirty_worktree": config.allow_dirty_worktree,
            },
            "changelog": {
                "output": config.changelog_output,
                "trim": config.changelog_trim,
                "render_always": config.changelog_render_always,
                "header": config.changelog_header,
                "footer": config.changelog_footer,
            },
            "git": {
                "enabled": config.git_enabled,
                "default_base": config.git_default_base,
                "default_head": config.git_default_head,
                "previous_tag_patterns": list(config.git_previous_tag_patterns),
                "include_merges": config.git_include_merges,
                "require_clean_worktree": config.git_require_clean_worktree,
                "max_commits": config.git_max_commits,
                "max_diff_chars_per_commit": config.git_max_diff_chars_per_commit,
                "candidate_status": config.git_candidate_status,
            },
        }
    return {
        "kind": "config_show",
        "workspace_root": str(root),
        "config_path": str(locator.config_path),
        "releaseledger_dir": str(locator.releaseledger_dir.resolve()),
        "config": config_dict,
    }


def config_set_releaseledger_dir(
    workspace_root: Path,
    value: str,
    *,
    external_dir: bool = False,
) -> dict[str, object]:
    """Atomically rewrite .releaseledger.toml with a new releaseledger_dir.

    Returns a dict with before, after, and config_path for JSON mode.
    Raises LaunchError on validation failure.
    """
    start = workspace_root.expanduser().resolve()
    if start.is_file():
        start = start.parent
    config_path = find_project_config(start)
    if config_path is None:
        raise LaunchError(
            "Project not initialized: no .releaseledger.toml found.",
            code="NOT_FOUND",
            exit_code=2,
            remediation=["Run `releaseledger init` to create the config."],
        )
    root = config_path.parent.resolve()
    config = load_project_config(config_path)
    before = config.releaseledger_dir

    # Determine the new policy.
    policy = config.releaseledger_dir_policy
    if external_dir:
        candidate = Path(value)
        if not candidate.is_absolute():
            resolved = (root / value).resolve()
            try:
                resolved.relative_to(root)
            except ValueError:
                policy = "external"
            else:
                # Path is inside workspace; keep existing policy.
                pass
        # Absolute paths are accepted regardless.
    else:
        # Validate with the current policy (or workspace if unchanged).
        resolve_releaseledger_dir(root, value, policy=policy)

    # Render the new TOML with the resolved policy.
    toml_text = render_default_releaseledger_toml(
        releaseledger_dir=value,
        project_name=config.ledger_name,
        ledger_ref=config.ledger_ref,
        policy=policy,
    )
    ledgercore.atomic_write_text(config_path, toml_text)

    return {
        "kind": "config_set",
        "config_path": str(config_path),
        "key": "releaseledger_dir",
        "before": before,
        "after": value,
        "policy": policy,
    }
