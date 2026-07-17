"""Public config/layout API re-exports."""

from __future__ import annotations

from releaseledger.ledgercore_backend import (
    ReleaseledgerLedgerLayout,
    clear_releaseledger_data_override,
    load_releaseledger_ledger_layout,
    set_releaseledger_data_target,
)
from releaseledger.migration import discover_legacy_project, migration_status
from releaseledger.services.config import (
    config_set_releaseledger_dir as _config_set_releaseledger_dir,
    config_show,
    storage_where,
)
from releaseledger.storage.config import (
    ProjectConfig,
    load_project_config,
    render_default_project_config,
    write_project_config,
)
from releaseledger.storage.paths import (
    ProjectPaths,
    ReleaseledgerProject,
    discover_workspace_root,
    load_project_locator,
    require_project,
    resolve_project_paths,
)

__all__ = [
    "ProjectConfig",
    "ProjectPaths",
    "ReleaseledgerLedgerLayout",
    "ReleaseledgerProject",
    "clear_releaseledger_data_override",
    "config_show",
    "discover_legacy_project",
    "discover_workspace_root",
    "load_project_config",
    "load_project_locator",
    "load_releaseledger_ledger_layout",
    "migration_status",
    "render_default_project_config",
    "require_project",
    "resolve_project_paths",
    "set_releaseledger_data_target",
    "storage_where",
    "write_project_config",
    # Deprecated aliases retained for one release.
    "config_set_releaseledger_dir",
    "render_default_releaseledger_toml",
]

# Deprecated aliases.
config_set_releaseledger_dir = _config_set_releaseledger_dir
render_default_releaseledger_toml = render_default_project_config
