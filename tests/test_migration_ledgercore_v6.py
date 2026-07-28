"""Releaseledger adapter tests for the final Ledgercore 0.6 contract."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from releaseledger import ledgercore_backend as backend
from releaseledger.errors import LaunchError
from releaseledger.migration import recover_migration


def test_execution_adapter_passes_real_hooks(monkeypatch: pytest.MonkeyPatch) -> None:
    import ledgercore.migration

    captured: dict[str, object] = {}

    def fake_execute(plan: object, **kwargs: object) -> object:
        captured.update(kwargs)
        return "committed"

    monkeypatch.setattr(ledgercore.migration, "execute_storage_migration", fake_execute)
    result = backend.execute_releaseledger_layout_migration(
        object(),
        quiescence_check=lambda: None,
        validate_staged=lambda _index: None,
        validate_activated=lambda _index: None,
        finalize=lambda: None,
    )

    assert result == "committed"
    hooks = captured["hooks"]
    assert hooks.quiescence_check is not None
    assert hooks.validate_staged is not None
    assert hooks.validate_activated is not None
    assert hooks.finalize is not None


def test_execution_adapter_requires_quiescence() -> None:
    with pytest.raises(LaunchError) as exc:
        backend.execute_releaseledger_layout_migration(object())
    assert exc.value.code == "migration_quiescence_required"
    assert exc.value.exit_code == 4


def test_recovery_forwards_explicit_policy_and_hooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ledgercore.migration

    journal = tmp_path / ".ledger" / "migrations" / "migration.toml"
    journal.parent.mkdir(parents=True)
    journal.write_text("placeholder", encoding="utf-8")
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        ledgercore.migration,
        "inspect_storage_migration",
        lambda _path: SimpleNamespace(migration_id="migration-0001", phase="failed"),
    )

    def fake_recover(path: Path, **kwargs: object) -> SimpleNamespace:
        captured["path"] = path
        captured.update(kwargs)
        return SimpleNamespace(phase="complete", items_completed=3)

    monkeypatch.setattr(ledgercore.migration, "recover_storage_migration", fake_recover)
    result = recover_migration(
        tmp_path,
        journal=journal,
        policy="rollback",
        reason="Test explicit rollback policy",
    )

    assert result["committed"] is True
    assert captured["path"] == journal
    assert captured["policy"] == "rollback"
    assert captured["hooks"] is not None
