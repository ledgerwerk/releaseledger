"""Common root command behavior and read-only filesystem guarantees."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from releaseledger import protocol
from releaseledger.cli import app

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
