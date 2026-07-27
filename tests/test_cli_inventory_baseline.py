"""Frozen pre-migration CLI inventory and Ledgercore contract smoke tests."""

from __future__ import annotations

from pathlib import Path

from ledgercore.cli import CommandMetadata, ExitCode, SuccessEnvelope
from typer.main import get_command

from releaseledger.cli import app


def _command_paths(command: object, prefix: tuple[str, ...] = ()) -> list[str]:
    commands = getattr(command, "commands", None)
    if not commands:
        return [" ".join(prefix)]
    paths: list[str] = []
    for name, child in sorted(commands.items()):
        paths.extend(_command_paths(child, prefix + (name,)))
    return paths


def test_pre_migration_command_inventory_is_frozen() -> None:
    actual = _command_paths(get_command(app))
    expected_path = Path(__file__).parent / "fixtures" / "cli_inventory_baseline.txt"
    expected = expected_path.read_text(encoding="utf-8").splitlines()
    assert expected == sorted(set(expected))
    assert len(expected) == 50
    # The snapshot is the compatibility floor while canonical groups are
    # added. Paths may gain new canonical siblings during migration.
    retained = set(expected) - {"changelog"}
    assert retained.issubset(actual)


def test_ledgercore_cli_contract_is_available() -> None:
    metadata = CommandMetadata(path="status", summary="Project status.")
    envelope = SuccessEnvelope(
        tool="releaseledger",
        command=metadata.path,
        result={"kind": "status"},
    )
    assert ExitCode.SUCCESS == 0
    assert envelope.as_mapping() == {
        "schema": "ledgerwerk.cli.v1",
        "ok": True,
        "tool": "releaseledger",
        "command": "status",
        "result": {"kind": "status"},
        "events": [],
        "warnings": [],
    }
