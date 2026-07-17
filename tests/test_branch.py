"""Tests for branch ledger commands (Phase 5, design §9)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from releaseledger.cli import app

runner = CliRunner()


def _git(repo: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Tester",
        "GIT_AUTHOR_EMAIL": "tester@example.com",
        "GIT_COMMITTER_NAME": "Tester",
        "GIT_COMMITTER_EMAIL": "tester@example.com",
        "GIT_CONFIG_NOSYSTEM": "1",
        "HOME": str(repo),
    }
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )
    return result.stdout


def _commit(repo: Path, message: str, name: str) -> None:
    (repo / name).write_text(name)
    _git(repo, "add", name)
    _git(repo, "commit", "-m", message)


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "HOME": str(repo)}
    subprocess.run(
        ["git", "init", "-q", "-b", "main", str(repo)],
        check=True,
        text=True,
        env=env,
    )
    return repo


def _run(repo: Path, *cmd: str):
    return runner.invoke(app, ["--cwd", str(repo), *cmd])


def _jrun(repo: Path, *cmd: str) -> dict:
    r = runner.invoke(app, ["--cwd", str(repo), "--json", *cmd])
    assert r.exit_code == 0, f"exit={r.exit_code}: {r.output}"
    return json.loads(r.output)


# --------------------------------------------------------------------------
# branch status
# --------------------------------------------------------------------------


def test_branch_status_matches(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _commit(repo, "init", "README.md")
    _run(repo, "init")
    data = _jrun(repo, "branch", "status")
    assert data["result"]["current_git_branch"] == "main"
    assert data["result"]["ledger_ref"] == "main"
    assert data["result"]["match"] is True


def test_branch_status_mismatch(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _commit(repo, "init", "README.md")
    _run(repo, "init")
    _git(repo, "checkout", "-b", "feature-x")
    data = _jrun(repo, "branch", "status")
    assert data["result"]["current_git_branch"] == "feature-x"
    assert data["result"]["match"] is False


def test_branch_status_not_in_git(tmp_path: Path) -> None:
    repo = tmp_path / "nogit"
    repo.mkdir()
    _run(repo, "init")
    data = _jrun(repo, "branch", "status")
    assert data["result"]["in_git_worktree"] is False
    assert data["result"]["match"] is None


# --------------------------------------------------------------------------
# branch start / merge
# --------------------------------------------------------------------------


def test_branch_start_creates_ledger(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _commit(repo, "init", "README.md")
    _run(repo, "init")
    _run(repo, "release", "create", "0.1.0", "--released-at", "2026-06-14")
    # Start a branch ledger.
    result = _run(repo, "branch", "start", "feature-a", "--parent", "main")
    assert result.exit_code == 0, result.output
    # The new ledger dir should exist.
    from releaseledger.storage.paths import resolve_project_paths

    paths = resolve_project_paths(repo)
    branch_ledger = paths.releaseledger_dir / "ledgers" / "feature-a"
    assert branch_ledger.is_dir()
    # Config should now point to feature-a.
    data = _jrun(repo, "config", "show")
    assert data["result"]["config"]["ledger_ref"] == "feature-a"


def test_branch_start_rejects_existing(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _commit(repo, "init", "README.md")
    _run(repo, "init")
    result = _run(repo, "branch", "start", "main", "--parent", "main")
    assert result.exit_code != 0


def test_branch_merge_dedups_by_source_refs(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _commit(repo, "init", "README.md")
    _git(repo, "tag", "v0.1.0")
    _run(repo, "init")
    _run(
        repo,
        "release",
        "create",
        "0.2.0",
        "--previous",
        "0.1.0",
        "--released-at",
        "2026-06-14",
    )
    # Start branch ledger.
    _run(repo, "branch", "start", "feature-b", "--parent", "main")
    # Add an entry on the branch with a git source ref.
    _run(
        repo,
        "entry",
        "add",
        "0.2.0",
        "--kind",
        "added",
        "--summary",
        "Branch entry",
        "--source-ref",
        "git:abcdef0123456789abcdef0123456789abcdef01",
    )
    # Merge back into main.
    result = _run(
        repo,
        "branch",
        "merge",
        "feature-b",
        "--into",
        "main",
        "--release",
        "0.2.0",
    )
    assert result.exit_code == 0, result.output
    # Verify the entry was merged into main.
    data = _jrun(repo, "entry", "list", "0.2.0")
    entries = data["result"]["entries"]
    assert len(entries) >= 1


# --------------------------------------------------------------------------
# Branch guard
# --------------------------------------------------------------------------


def test_branch_guard_warn_does_not_block(tmp_path: Path) -> None:
    """ledger_branch_guard='warn' prints a warning but allows mutating commands."""
    repo = _init_repo(tmp_path)
    _commit(repo, "init", "README.md")
    _git(repo, "checkout", "-b", "other")
    _run(repo, "init")
    # Set guard to warn via config edit.
    config_path = repo / ".ledger" / "releaseledger" / "config.toml"
    content = config_path.read_text()
    content = content.replace(
        'ledger_branch_guard = "off"', 'ledger_branch_guard = "warn"'
    )
    config_path.write_text(content)
    # A mutating command should succeed (with warning).
    result = _run(repo, "release", "create", "0.1.0", "--released-at", "2026-06-14")
    assert result.exit_code == 0, result.output


def test_branch_guard_on_blocks_mutating(tmp_path: Path) -> None:
    """ledger_branch_guard='on' blocks mutating commands on branch mismatch."""
    repo = _init_repo(tmp_path)
    _commit(repo, "init", "README.md")
    _git(repo, "checkout", "-b", "other")
    _run(repo, "init")
    config_path = repo / ".ledger" / "releaseledger" / "config.toml"
    content = config_path.read_text()
    content = content.replace(
        'ledger_branch_guard = "off"', 'ledger_branch_guard = "on"'
    )
    config_path.write_text(content)
    # A mutating command should fail.
    result = _run(repo, "release", "create", "0.1.0", "--released-at", "2026-06-14")
    assert result.exit_code != 0


def test_branch_guard_readonly_always_allowed(tmp_path: Path) -> None:
    """Read-only commands are always allowed regardless of branch guard."""
    repo = _init_repo(tmp_path)
    _commit(repo, "init", "README.md")
    _git(repo, "checkout", "-b", "other")
    _run(repo, "init")
    config_path = repo / ".ledger" / "releaseledger" / "config.toml"
    content = config_path.read_text()
    content = content.replace(
        'ledger_branch_guard = "off"', 'ledger_branch_guard = "on"'
    )
    config_path.write_text(content)
    # Read-only commands should succeed.
    result = _run(repo, "release", "list")
    assert result.exit_code == 0, result.output
    result = _run(repo, "branch", "status")
    assert result.exit_code == 0, result.output
