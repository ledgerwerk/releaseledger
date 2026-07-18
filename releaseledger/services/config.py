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

__all__ = [
    "config_set_releaseledger_dir",
    "config_show",
    "storage_where",
]


def storage_where(workspace_root: Path) -> dict[str, object]:
    """Return a read-only diagnostic dict describing the effective storage location.

    Never mutates state. Checks for canonical schema-3 manifest first,
    then falls back to legacy detection.
    """
    root = Path(workspace_root).resolve()

    # Try to load the canonical project (traverses upward).
    try:
        from releaseledger.ledgercore_backend import (
            load_releaseledger_ledger_layout,
        )

        layout = load_releaseledger_ledger_layout(
            root, allow_missing=True, validate_storage=False
        )
        ledger_ref = ""
        ledger_dir = ""
        try:
            from releaseledger.storage.paths import resolve_project_paths

            paths = resolve_project_paths(root)
            ledger_ref = paths.ledger_ref
            ledger_dir = str(paths.ledger_dir)
        except Exception:
            pass

        legacy_detected = False
        from releaseledger.migration import discover_legacy_project

        try:
            discover_legacy_project(root)
            legacy_detected = True
        except Exception:
            pass

        return {
            "kind": "storage_location",
            "project_root": str(layout.project_root),
            "project_uuid": layout.project_uuid,
            "project_name": layout.project_name or "",
            "manifest_path": str(layout.manifest_path),
            "local_config_path": str(layout.local_config_path),
            "tool_config_path": str(layout.config_path),
            "data_root": str(layout.data_root),
            "data_storage": str(layout.data_storage),
            "data_source": layout.data_source,
            "external_root": str(layout.external_root) if layout.external_root else "",
            "indexes_root": str(layout.indexes_root),
            "active_ledger_ref": ledger_ref,
            "active_ledger_dir": ledger_dir,
            "layout_valid": True,
            "legacy_detected": legacy_detected,
            "migration_state": "canonical-ready",
            # Compatibility aliases for one release.
            "workspace_root": str(layout.project_root),
            "releaseledger_dir": str(layout.data_root),
            "inside_workspace": layout.data_storage == "project",
        }
    except Exception:
        pass

    # Legacy detection (no canonical manifest or load failed).
    from releaseledger.migration import discover_legacy_project

    try:
        config_path, _ = discover_legacy_project(root)
        return {
            "kind": "storage_location",
            "project_root": str(root),
            "legacy_detected": True,
            "legacy_config_path": str(config_path),
            "migration_state": "legacy",
            "layout_valid": False,
            "data_root": "",
            "indexes_root": "",
            "workspace_root": str(root),
            "releaseledger_dir": "",
        }
    except Exception:
        return {
            "kind": "storage_location",
            "project_root": str(root),
            "legacy_detected": False,
            "migration_state": "uninitialized",
            "layout_valid": False,
            "workspace_root": str(root),
        }


def config_show(workspace_root: Path) -> dict[str, object]:
    """Return a read-only dict with validated config values and resolved paths."""
    from releaseledger.ledgercore_backend import load_releaseledger_ledger_layout

    root = Path(workspace_root).resolve()
    try:
        layout = load_releaseledger_ledger_layout(root, allow_missing=False)
        project_name = layout.project_name
    except Exception:
        layout = None
        project_name = None

    config: ProjectConfig | None = None
    config_path = root / ".ledger" / "releaseledger" / "config.toml"
    if config_path.is_file():
        try:
            config = load_project_config(config_path)
        except LaunchError:
            config = None
    config_dict: dict[str, object] = {}
    if config is not None:
        config_dict = {
            "config_version": config.config_version,
            "ledger_ref": config.ledger_ref,
            "ledger_parent_ref": config.ledger_parent_ref,
            "ledger_branch_guard": config.ledger_branch_guard,
            "ledger_code": config.ledger_code,
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
        "project_root": str(root),
        "project_name": project_name or "",
        "config_path": str(config_path),
        "config": config_dict,
        # Compatibility aliases.
        "workspace_root": str(root),
        "releaseledger_dir": str(layout.data_root) if layout else "",
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
