"""Read-only root command service adapters."""

from pathlib import Path

from releaseledger.services.project_state import (
    next_action,
    project_doctor,
    project_info,
    project_status,
)

__all__ = [
    "next_action",
    "project_doctor",
    "project_info",
    "project_status",
]


def status(root: Path) -> dict[str, object]:
    return project_status(root)


def info(root: Path) -> dict[str, object]:
    return project_info(root)


def doctor(root: Path) -> dict[str, object]:
    return project_doctor(root)


def next_command(root: Path) -> dict[str, object]:
    return next_action(root)
