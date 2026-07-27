"""Canonical resource paths and compatibility aliases."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from releaseledger.cli import app

runner = CliRunner()


def test_entry_apply_accepts_versioned_and_stdin_batches(tmp_path: Path) -> None:
    assert runner.invoke(app, ["--root", str(tmp_path), "init"]).exit_code == 0
    assert (
        runner.invoke(
            app,
            ["--root", str(tmp_path), "release", "create", "1.0.0"],
        ).exit_code
        == 0
    )
    batch = tmp_path / "entries.yaml"
    batch.write_text(
        "schema: releaseledger.entry-batch.v1\n"
        "operation: create\n"
        "reason: migration\n"
        "entries:\n"
        "  - kind: fixed\n"
        "    summary: Add the unified entry command\n",
        encoding="utf-8",
    )
    applied = runner.invoke(
        app,
        [
            "--root",
            str(tmp_path),
            "--json",
            "entry",
            "apply",
            "1.0.0",
            "--file",
            str(batch),
        ],
    )
    assert applied.exit_code == 0, applied.stdout + applied.stderr
    assert json.loads(applied.stdout)["command"] == "entry apply"

    stdin_batch = "entries:\n  - kind: docs\n    summary: Read stdin\n"
    preview = runner.invoke(
        app,
        [
            "--root",
            str(tmp_path),
            "--json",
            "entry",
            "apply",
            "1.0.0",
            "--file",
            "-",
            "--dry-run",
        ],
        input=stdin_batch,
    )
    assert preview.exit_code == 0, preview.stdout + preview.stderr
    payload = json.loads(preview.stdout)
    assert payload["warnings"][0]["code"] == "legacy_input"


def test_changelog_canonical_paths_and_root_aliases(tmp_path: Path) -> None:
    assert runner.invoke(app, ["--root", str(tmp_path), "init"]).exit_code == 0
    canonical = runner.invoke(
        app,
        ["--root", str(tmp_path), "--json", "changelog", "build"],
    )
    assert canonical.exit_code == 0, canonical.stdout + canonical.stderr
    assert json.loads(canonical.stdout)["command"] == "changelog build"
    alias = runner.invoke(
        app,
        ["--root", str(tmp_path), "--json", "build"],
    )
    assert alias.exit_code == 0, alias.stdout + alias.stderr
    alias_payload = json.loads(alias.stdout)
    assert alias_payload["command"] == "changelog build"
    assert alias_payload["warnings"][0]["code"] == "deprecated_command"
