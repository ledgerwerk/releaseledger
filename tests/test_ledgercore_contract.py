"""Installation contract for the final Ledgercore 0.6 migration API."""

from __future__ import annotations

import inspect

import ledgercore
from ledgercore import migration


def test_releaseledger_requires_ledgercore_0_6_public_contract() -> None:
    """Fail clearly when a transitional Ledgercore build is installed."""

    version = str(getattr(ledgercore, "__version__", "unknown"))
    assert version.startswith("0.6."), (
        "Releaseledger requires Ledgercore 0.6.x; installed version is " + version
    )

    required_types = (
        "DestinationPrecondition",
        "StorageFingerprint",
        "StorageMigrationHooks",
        "StorageMigrationItem",
        "StorageMigrationPlan",
        "StorageMigrationPlanValidation",
        "StorageMigrationResult",
    )
    missing_types = [name for name in required_types if not hasattr(migration, name)]
    assert not missing_types, (
        "Ledgercore 0.6 migration contract is incomplete; missing public types: "
        + ", ".join(missing_types)
    )

    required_functions = (
        "fingerprint_storage_directory",
        "fingerprint_storage_file",
        "inspect_storage_migration_destination",
        "validate_storage_migration_plan",
        "execute_storage_migration",
        "inspect_storage_migration",
        "recover_storage_migration",
    )
    missing_functions = [
        name
        for name in required_functions
        if not callable(getattr(migration, name, None))
    ]
    assert not missing_functions, (
        "Ledgercore 0.6 migration contract is incomplete; missing public functions: "
        + ", ".join(missing_functions)
    )

    execute_parameters = inspect.signature(
        migration.execute_storage_migration
    ).parameters
    assert "hooks" in execute_parameters, (
        "Ledgercore 0.6 execute_storage_migration must accept lifecycle hooks; "
        "the installed build is transitional"
    )
    recovery_parameters = inspect.signature(
        migration.recover_storage_migration
    ).parameters
    assert "policy" in recovery_parameters, (
        "Ledgercore 0.6 recover_storage_migration must accept an explicit policy; "
        "the installed build is transitional"
    )
