"""Common root command behavior and read-only filesystem guarantees."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from releaseledger import protocol
from releaseledger.cli import app
from releaseledger.services.releases import create_release
from releaseledger.storage.paths import resolve_project_paths

runner = CliRunner()


def _write_skill(path: Path, declared_protocol: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        (
            "---\n"
            "name: releaseledger\n"
            "description: Test Releaseledger skill.\n"
            f"protocol: {declared_protocol}\n"
            "---\n"
        ),
        encoding="utf-8",
    )


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


def test_index_repair_cli_and_storage_validation_share_health(
    tmp_path: Path,
) -> None:
    initialized = runner.invoke(app, ["--root", str(tmp_path), "init"])
    assert initialized.exit_code == 0, initialized.stdout
    create_release(tmp_path, version="1.0.0")
    indexes = resolve_project_paths(tmp_path).project.indexes_root
    (indexes / ".ledger-project.toml").unlink()

    where = runner.invoke(app, ["--json", "--root", str(tmp_path), "storage", "where"])
    assert where.exit_code == 0, where.stdout
    where_result = json.loads(where.stdout)["result"]
    assert "unknown" not in where_result["bindings"]
    assert where_result["bindings"]["indexes"]["classification"] == "generated-current"
    assert where_result["raw_layout_valid"] is False
    assert where_result["layout_valid"] is True
    where_human = runner.invoke(app, ["--root", str(tmp_path), "storage", "where"])
    assert where_human.exit_code == 0, where_human.stdout
    assert "Indexes state: generated-current" in where_human.stdout
    assert "Raw layout valid: False" in where_human.stdout
    assert "Layout valid: True" in where_human.stdout

    validation = runner.invoke(
        app,
        ["--json", "--root", str(tmp_path), "storage", "validate", "--strict"],
    )
    assert validation.exit_code == 0, validation.stdout
    assert json.loads(validation.stdout)["result"]["passed"] is True
    validation_human = runner.invoke(
        app,
        ["--root", str(tmp_path), "storage", "validate", "--strict"],
    )
    assert validation_human.exit_code == 0, validation_human.stdout
    assert "Raw layout valid: False" in validation_human.stdout
    assert "Layout valid: True" in validation_human.stdout

    preview = runner.invoke(
        app,
        ["--json", "--root", str(tmp_path), "repair", "index", "--dry-run"],
    )
    assert preview.exit_code == 0, preview.stdout
    assert json.loads(preview.stdout)["result"]["action"] == "rebind-and-rebuild"
    preview_human = runner.invoke(
        app,
        ["--root", str(tmp_path), "repair", "index", "--dry-run"],
    )
    assert preview_human.exit_code == 0, preview_human.stdout
    assert "Action: rebind-and-rebuild" in preview_human.stdout
    assert not (indexes / ".ledger-project.toml").exists()

    applied = runner.invoke(
        app,
        ["--json", "--root", str(tmp_path), "repair", "index", "--apply"],
    )
    assert applied.exit_code == 0, applied.stdout
    repair_result = json.loads(applied.stdout)["result"]
    assert repair_result["binding_restored"] is True
    assert repair_result["indexes_rebuilt"] is True

    validation_after = runner.invoke(
        app,
        ["--json", "--root", str(tmp_path), "storage", "validate", "--strict"],
    )
    assert validation_after.exit_code == 0, validation_after.stdout
    assert json.loads(validation_after.stdout)["result"]["raw_layout_valid"] is True


def test_blocked_cache_cli_reports_repair_and_never_silently_replaces(
    tmp_path: Path,
) -> None:
    init = runner.invoke(app, ["--root", str(tmp_path), "init"])
    assert init.exit_code == 0, init.stdout
    create_release(tmp_path, version="1.0.0")
    indexes = resolve_project_paths(tmp_path).project.indexes_root
    (indexes / ".ledger-project.toml").unlink()
    foreign = indexes / "foreign-cache.dat"
    foreign.write_text("preserve", encoding="utf-8")

    where = runner.invoke(app, ["--json", "--root", str(tmp_path), "storage", "where"])
    assert where.exit_code == 0, where.stdout
    where_result = json.loads(where.stdout)["result"]
    assert where_result["bindings"]["indexes"]["classification"] == "foreign"
    assert where_result["repair_command"] == "releaseledger repair index --dry-run"
    where_human = runner.invoke(app, ["--root", str(tmp_path), "storage", "where"])
    assert where_human.exit_code == 0, where_human.stdout
    assert "Unexpected cache paths:" in where_human.stdout
    assert "Repair: releaseledger repair index --dry-run" in where_human.stdout

    validation = runner.invoke(
        app,
        ["--json", "--root", str(tmp_path), "storage", "validate", "--strict"],
    )
    assert validation.exit_code == 1, validation.stdout
    assert json.loads(validation.stdout)["result"]["repair_command"] == (
        "releaseledger repair index --dry-run"
    )
    validation_human = runner.invoke(
        app, ["--root", str(tmp_path), "storage", "validate", "--strict"]
    )
    assert validation_human.exit_code == 1, validation_human.stdout
    assert "Repair: releaseledger repair index --dry-run" in validation_human.stdout

    next_result = runner.invoke(app, ["--json", "--root", str(tmp_path), "next-action"])
    assert next_result.exit_code == 0, next_result.stdout
    assert (
        json.loads(next_result.stdout)["result"]["command"] == "repair index --dry-run"
    )

    preview = runner.invoke(
        app,
        ["--json", "--root", str(tmp_path), "repair", "index", "--dry-run"],
    )
    assert preview.exit_code == 0, preview.stdout
    preview_result = json.loads(preview.stdout)["result"]
    assert preview_result["action"] == "blocked"
    assert preview_result["unexpected_paths"] == ["foreign-cache.dat"]
    assert foreign.read_text(encoding="utf-8") == "preserve"

    refused = runner.invoke(
        app,
        ["--root", str(tmp_path), "repair", "index", "--apply"],
    )
    assert refused.exit_code == 2, refused.stdout
    assert foreign.read_text(encoding="utf-8") == "preserve"

    applied = runner.invoke(
        app,
        [
            "--json",
            "--root",
            str(tmp_path),
            "repair",
            "index",
            "--apply",
            "--quarantine-foreign",
        ],
    )
    assert applied.exit_code == 0, applied.stdout
    assert json.loads(applied.stdout)["result"]["action"] == "quarantine-and-rebuild"
    assert (indexes / ".ledger-project.toml").is_file()


def test_repair_index_appears_in_generated_cli_inventory() -> None:
    commands = runner.invoke(app, ["--json", "commands"])
    assert commands.exit_code == 0, commands.stdout
    inventory = json.loads(commands.stdout)["result"]["commands"]
    assert any(item["path"] == "repair index" for item in inventory)

    help_group = runner.invoke(app, ["--json", "help", "repair"])
    assert help_group.exit_code == 0, help_group.stdout
    assert any(
        child["path"] == "repair index"
        for child in json.loads(help_group.stdout)["result"]["children"]
    )
    help_command = runner.invoke(app, ["--json", "help", "repair", "index"])
    assert help_command.exit_code == 0, help_command.stdout
    assert json.loads(help_command.stdout)["result"]["path"] == "repair index"
    root_help = runner.invoke(app, ["--help"])
    assert root_help.exit_code == 0
    assert "repair" in root_help.stdout
    commands_human = runner.invoke(app, ["commands"])
    assert commands_human.exit_code == 0, commands_human.stdout
    assert "repair index" in commands_human.stdout
    help_human = runner.invoke(app, ["help", "repair"])
    assert help_human.exit_code == 0, help_human.stdout
    assert "repair index" in help_human.stdout


def test_doctor_human_and_json_share_skill_protocol_check(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    workspace_skill = tmp_path / ".agents/skills/releaseledger/SKILL.md"
    _write_skill(workspace_skill, protocol.SKILL_PROTOCOL_VERSION)

    init = runner.invoke(app, ["--root", str(tmp_path), "init"])
    assert init.exit_code == 0, init.stdout

    human = runner.invoke(app, ["--root", str(tmp_path), "doctor"])
    machine = runner.invoke(app, ["--json", "--root", str(tmp_path), "doctor"])

    assert human.exit_code == 0, human.stdout
    assert machine.exit_code == 0, machine.stdout
    assert "pass  skill_protocol" in human.stdout
    assert (
        "All discovered Releaseledger Agent Skill copies match CLI protocol 2."
        in human.stdout
    )

    payload = json.loads(machine.stdout)
    checks = payload["result"]["checks"]
    skill_check = next(item for item in checks if item["code"] == "skill_protocol")
    assert skill_check["status"] == "pass"
    assert skill_check["message"] == (
        "All discovered Releaseledger Agent Skill copies match CLI protocol 2."
    )
    assert skill_check["data"]["skill_path"] == str(workspace_skill)
    assert skill_check["data"]["skill_state"] == "match"
    assert skill_check["data"]["skill_candidate_count"] == 1
    assert skill_check["data"]["skill_matches_cli"] is True


def test_doctor_accepts_host_native_project_skill(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    skill = tmp_path / ".github/skills/releaseledger/SKILL.md"
    _write_skill(skill, protocol.SKILL_PROTOCOL_VERSION)

    init = runner.invoke(app, ["--root", str(tmp_path), "init"])
    assert init.exit_code == 0, init.stdout

    result = runner.invoke(app, ["--root", str(tmp_path), "doctor", "--check"])

    assert result.exit_code == 0, result.stdout
    payload = json.loads(
        runner.invoke(app, ["--json", "--root", str(tmp_path), "doctor"]).stdout
    )
    skill_check = next(
        item for item in payload["result"]["checks"] if item["code"] == "skill_protocol"
    )
    assert skill_check["status"] == "pass"
    assert skill_check["data"]["skill_candidates"][0]["source"] == "project_github"


def test_doctor_reports_skill_conflicts_and_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project_skill = tmp_path / ".agents/skills/releaseledger/SKILL.md"
    user_skill = tmp_path / "home/.claude/skills/releaseledger/SKILL.md"
    _write_skill(project_skill, protocol.SKILL_PROTOCOL_VERSION)
    _write_skill(user_skill, 1)

    init = runner.invoke(app, ["--root", str(tmp_path), "init"])
    assert init.exit_code == 0, init.stdout

    human = runner.invoke(app, ["--root", str(tmp_path), "doctor"])
    machine = runner.invoke(
        app, ["--json", "--root", str(tmp_path), "doctor", "--check"]
    )

    assert human.exit_code == 0, human.stdout
    assert "conflicting or invalid protocol metadata" in human.stdout
    assert machine.exit_code == 1, machine.stdout
    payload = json.loads(machine.stdout)
    skill_check = next(
        item for item in payload["result"]["checks"] if item["code"] == "skill_protocol"
    )
    assert skill_check["status"] == "fail"
    assert skill_check["data"]["skill_state"] == "conflict"
    assert skill_check["data"]["skill_candidate_count"] == 2
    assert (
        "Update or remove stale Releaseledger skill copies"
        in skill_check["remediation"][0]
    )
    candidate_paths = {item["path"] for item in skill_check["data"]["skill_candidates"]}
    assert candidate_paths == {str(project_skill), str(user_skill)}


def test_doctor_missing_skill_recommends_portable_locations(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    init = runner.invoke(app, ["--root", str(tmp_path), "init"])
    assert init.exit_code == 0, init.stdout

    machine = runner.invoke(app, ["--json", "--root", str(tmp_path), "doctor"])
    payload = json.loads(machine.stdout)
    skill_check = next(
        item for item in payload["result"]["checks"] if item["code"] == "skill_protocol"
    )

    assert "not found in supported local discovery locations" in skill_check["message"]
    assert ".agents/skills/releaseledger/SKILL.md" in skill_check["remediation"][0]
    assert "~/.agents/skills/releaseledger/SKILL.md" in skill_check["remediation"][0]
