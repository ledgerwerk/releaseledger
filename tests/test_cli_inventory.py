"""Metadata/registration drift checks for canonical CLI paths."""

from __future__ import annotations

from typer.main import get_command

from releaseledger.cli import app
from releaseledger.command_registry import COMMAND_INVENTORY


def _paths(command: object, prefix: tuple[str, ...] = ()) -> set[str]:
    commands = getattr(command, "commands", None)
    if not commands:
        return {" ".join(prefix)}
    result: set[str] = set()
    for name, child in commands.items():
        result.update(_paths(child, prefix + (name,)))
    return result


def test_canonical_registration_and_metadata_do_not_drift() -> None:
    registered = _paths(get_command(app))
    canonical = {entry.path for entry in COMMAND_INVENTORY.entries}
    aliases = {alias for entry in COMMAND_INVENTORY.entries for alias in entry.aliases}
    assert canonical <= registered
    assert registered - canonical <= aliases


def test_inventory_order_and_aliases_are_deterministic() -> None:
    paths = [entry.path for entry in COMMAND_INVENTORY.entries]
    assert paths == sorted(paths)
    for entry in COMMAND_INVENTORY.entries:
        for alias in entry.aliases:
            assert COMMAND_INVENTORY.resolve(alias) == entry
