"""Contract checks for the unified Releaseledger command boundary."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from releaseledger.cli import app
from releaseledger.cli_common import normalize_legacy_global_argv

runner = CliRunner()


def test_json_success_has_the_unified_envelope(tmp_path: Path) -> None:
    result = runner.invoke(app, ["--root", str(tmp_path), "--json", "init"])
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["schema"] == "ledgerwerk.cli.v1"
    assert payload["ok"] is True
    assert payload["tool"] == "releaseledger"
    assert payload["command"] == "init"
    assert "result_type" not in payload
    assert payload["events"] == []
    assert payload["warnings"] == []


def test_cwd_alias_warns_inside_json_only(tmp_path: Path) -> None:
    result = runner.invoke(app, ["--cwd", str(tmp_path), "--json", "init"])
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["warnings"] == [
        {
            "code": "deprecated_option",
            "message": "Option `--cwd` is deprecated; use `--root` instead.",
            "replacement": "--root",
        }
    ]
    assert result.stderr == ""


def test_conflicting_root_aliases_are_usage_errors(tmp_path: Path) -> None:
    other = tmp_path / "other"
    result = runner.invoke(
        app,
        ["--root", str(tmp_path), "--cwd", str(other), "--json", "init"],
    )
    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "usage_error"
    assert payload["error"]["remediation"]


def test_legacy_post_command_json_is_narrowly_hoisted() -> None:
    assert normalize_legacy_global_argv(["status", "--json"]) == [
        "--json",
        "status",
    ]
    assert normalize_legacy_global_argv(["status", "--", "--json"]) == [
        "status",
        "--",
        "--json",
    ]
    assert normalize_legacy_global_argv(["status", "--format", "json"]) == [
        "status",
        "--format",
        "json",
    ]
