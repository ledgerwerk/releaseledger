"""Canonical lifecycle command and compatibility alias checks."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from releaseledger.cli import app

runner = CliRunner()


def test_release_set_status_requires_reason_and_preserves_terminal_workflows(
    tmp_path: Path,
) -> None:
    assert runner.invoke(app, ["--root", str(tmp_path), "init"]).exit_code == 0
    assert (
        runner.invoke(
            app,
            ["--root", str(tmp_path), "release", "create", "1.0.0"],
        ).exit_code
        == 0
    )
    missing_reason = runner.invoke(
        app,
        ["--root", str(tmp_path), "release", "set-status", "1.0.0", "candidate"],
    )
    assert missing_reason.exit_code == 2
    transition = runner.invoke(
        app,
        [
            "--root",
            str(tmp_path),
            "--json",
            "release",
            "set-status",
            "1.0.0",
            "candidate",
            "--reason",
            "Ready for review",
        ],
    )
    assert transition.exit_code == 0, transition.stdout + transition.stderr
    assert json.loads(transition.stdout)["result"]["reason"] == "Ready for review"
    terminal = runner.invoke(
        app,
        [
            "--root",
            str(tmp_path),
            "release",
            "set-status",
            "1.0.0",
            "released",
            "--reason",
            "No bypass",
        ],
    )
    assert terminal.exit_code == 2


def test_entry_set_status_and_update_alias(tmp_path: Path) -> None:
    assert runner.invoke(app, ["--root", str(tmp_path), "init"]).exit_code == 0
    assert (
        runner.invoke(
            app,
            ["--root", str(tmp_path), "release", "create", "1.0.0"],
        ).exit_code
        == 0
    )
    added = runner.invoke(
        app,
        [
            "--root",
            str(tmp_path),
            "entry",
            "add",
            "1.0.0",
            "--summary",
            "Document the migration",
        ],
    )
    assert added.exit_code == 0, added.stdout + added.stderr
    entry_id = json.loads(
        runner.invoke(
            app,
            ["--root", str(tmp_path), "--json", "entry", "list", "1.0.0"],
        ).stdout
    )["result"]["entries"][0]["entry_id"]
    changed = runner.invoke(
        app,
        [
            "--root",
            str(tmp_path),
            "entry",
            "set-status",
            "1.0.0",
            entry_id,
            "draft",
            "--reason",
            "Needs review",
        ],
    )
    assert changed.exit_code == 0, changed.stdout + changed.stderr
    alias = runner.invoke(
        app,
        [
            "--root",
            str(tmp_path),
            "--json",
            "entry",
            "update",
            "1.0.0",
            entry_id,
            "--status",
            "accepted",
        ],
    )
    assert alias.exit_code == 0, alias.stdout + alias.stderr
    payload = json.loads(alias.stdout)
    assert payload["command"] == "entry set-status"
    assert payload["warnings"][0]["code"] == "deprecated_command"
