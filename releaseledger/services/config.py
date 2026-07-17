"""Config and storage diagnostics service.

Read-only helpers for agents to inspect project configuration, resolved paths,
and on-disk storage health without mutating state.
"""

from __future__ import annotations

from pathlib import Path

from releaseledger.errors import CODE_USAGE_ERROR, LaunchError
from releaseledger.storage.config import (
    ProjectConfig,
    load_project_config,
)
from releaseledger.storage.paths import (
    ProjectLocator,
    load_project_locator,
    resolve_project_paths,
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
            "ledger_ref": config.ledger_ref,
            "ledger_parent_ref": config.ledger_parent_ref,
            "ledger_branch_guard": config.ledger_branch_guard,
            "ledger": {
                "code": config.ledger_code,
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
    """Storage mutation now lives under ``releaseledger storage set``."""

    raise LaunchError(
        "config set releaseledger_dir is no longer supported; storage "
        "topology is owned by the canonical Ledger project.",
        code=CODE_USAGE_ERROR,
        exit_code=2,
        data={"command": "config set releaseledger_dir", "value": value},
        remediation=[
            "Use `releaseledger storage set data --storage ... --root ...`.",
        ],
    )
