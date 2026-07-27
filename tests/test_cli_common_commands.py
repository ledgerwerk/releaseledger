"""Common root command behavior and read-only filesystem guarantees."""

from __future__ import annotations

import hashlib
from pathlib import Path

from typer.testing import CliRunner

from releaseledger.cli import app

runner = CliRunner()


def _snapshot(root: Path) -> dict[str, tuple[int, str]]:
    result: dict[str, tuple[int, str]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            result[str(path.relative_to(root))] = (
                path.stat().st_mtime_ns,
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
    return result


def test_uninitialized_common_commands_are_read_only(tmp_path: Path) -> None:
    before = _snapshot(tmp_path)
    for command in ("status", "info", "doctor", "next-action"):
        result = runner.invoke(app, ["--root", str(tmp_path), command])
        assert result.exit_code == 0, (command, result.stdout, result.stderr)
    assert _snapshot(tmp_path) == before


def test_status_and_doctor_check_return_one_when_unhealthy(tmp_path: Path) -> None:
    for command in ("status", "doctor"):
        result = runner.invoke(app, ["--root", str(tmp_path), command, "--check"])
        assert result.exit_code == 1, (command, result.stdout, result.stderr)


def test_common_commands_report_initialized_project(tmp_path: Path) -> None:
    init = runner.invoke(app, ["--root", str(tmp_path), "init"])
    assert init.exit_code == 0, init.stdout
    for command in ("status", "info", "doctor", "next-action"):
        result = runner.invoke(app, ["--root", str(tmp_path), command])
        assert result.exit_code == 0, (command, result.stdout, result.stderr)
