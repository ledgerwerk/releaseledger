"""Tests for the `releaseledger git` CLI commands (Phase 2/3, design §7)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import yaml
from typer.testing import CliRunner

from releaseledger.cli import app

runner = CliRunner()


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


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


def _commit(repo: Path, message: str, content_name: str | None = None) -> str:
    if content_name is None:
        content_name = f"file-{abs(hash(message)) % 100000}.txt"
    (repo / content_name).write_text(f"content of {content_name}\n")
    _git(repo, "add", content_name)
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD").strip()


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {
        **os.environ,
        "GIT_CONFIG_NOSYSTEM": "1",
        "HOME": str(repo),
    }
    subprocess.run(
        ["git", "init", "-q", "-b", "main", str(repo)],
        check=True,
        text=True,
        env=env,
    )
    return repo


def _run(repo: Path, *cmd: str) -> str:
    """Run a CLI command with --cwd set to repo."""
    result = runner.invoke(app, ["--cwd", str(repo), *cmd])
    return result.output


def _jrun(repo: Path, *cmd: str) -> dict:
    """Run a CLI command with --json and --cwd, return parsed result."""
    result = runner.invoke(app, ["--cwd", str(repo), "--json", *cmd])
    assert result.exit_code == 0, f"exit={result.exit_code}: {result.output}"
    return json.loads(result.output)


def _setup_release(tmp_path: Path) -> tuple[Path, str, str]:
    """Create a repo with v0.1.0 + two commits, init releaseledger, return (repo, sha_a, sha_b)."""  # noqa: E501
    repo = _init_repo(tmp_path)
    _commit(repo, "root", "README.md")
    _git(repo, "tag", "v0.1.0")
    sha_a = _commit(repo, "feat: add a", "a.txt")
    sha_b = _commit(repo, "fix: handle b", "b.txt")
    result = runner.invoke(app, ["--cwd", str(repo), "init"])
    assert result.exit_code == 0, result.output
    result = runner.invoke(
        app,
        [
            "--cwd",
            str(repo),
            "release",
            "create",
            "0.2.0",
            "--previous",
            "0.1.0",
            "--released-at",
            "2026-06-14",
        ],  # noqa: E501
    )
    assert result.exit_code == 0, result.output
    result = runner.invoke(
        app,
        [
            "--cwd",
            str(repo),
            "release",
            "update",
            "0.2.0",
            "--git-base",
            "v0.1.0",
            "--git-head",
            "HEAD",
        ],  # noqa: E501
    )
    assert result.exit_code == 0, result.output
    return repo, sha_a, sha_b


# --------------------------------------------------------------------------
# git range
# --------------------------------------------------------------------------


def test_git_range_shows_candidates(tmp_path: Path) -> None:
    repo, sha_a, sha_b = _setup_release(tmp_path)
    result = runner.invoke(app, ["--cwd", str(repo), "git", "range", "0.2.0"])
    assert result.exit_code == 0, result.output
    assert "GIT RANGE 0.2.0" in result.output
    assert sha_a[:7] in result.output
    assert sha_b[:7] in result.output
    assert "added" in result.output
    assert "fixed" in result.output


def test_git_range_json(tmp_path: Path) -> None:
    repo, sha_a, sha_b = _setup_release(tmp_path)
    data = _jrun(repo, "git", "range", "0.2.0")
    assert data["ok"] is True
    assert data["result"]["version"] == "0.2.0"
    assert data["result"]["candidate_count"] == 2
    sha_refs = {c["source_ref"] for c in data["result"]["candidates"]}
    assert f"git:{sha_a}" in sha_refs
    assert f"git:{sha_b}" in sha_refs


# --------------------------------------------------------------------------
# git range next
# --------------------------------------------------------------------------


def test_git_range_next_no_release_required(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _commit(repo, "root", "README.md")
    _git(repo, "tag", "v0.1.0")
    sha_a = _commit(repo, "feat: add a", "a.txt")
    runner.invoke(app, ["--cwd", str(repo), "init"])
    # No release create — just project init.
    result = runner.invoke(
        app,
        [
            "--cwd",
            str(repo),
            "git",
            "range",
            "next",
            "--base",
            "v0.1.0",
            "--head",
            "HEAD",
        ],  # noqa: E501
    )
    assert result.exit_code == 0, result.output
    assert "GIT RANGE next" in result.output
    assert sha_a[:7] in result.output


def test_git_range_next_requires_base(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _commit(repo, "root", "README.md")
    result = runner.invoke(
        app,
        ["--cwd", str(repo), "git", "range", "next", "--head", "HEAD"],
    )
    assert result.exit_code != 0


# --------------------------------------------------------------------------
# git import
# --------------------------------------------------------------------------


def test_git_import_writes_entry_yaml(tmp_path: Path) -> None:
    repo, sha_a, sha_b = _setup_release(tmp_path)
    out_file = repo / "batch.yaml"
    result = runner.invoke(
        app,
        [
            "--cwd",
            str(repo),
            "git",
            "import",
            "0.2.0",
            "--base",
            "v0.1.0",
            "--head",
            "HEAD",
            "--status",
            "draft",
            "--output",
            str(out_file),
        ],
    )
    assert result.exit_code == 0, result.output
    assert out_file.is_file()
    batch = yaml.safe_load(out_file.read_text())
    assert "entries" in batch
    assert len(batch["entries"]) == 2
    all_src_refs = {
        ref for entry in batch["entries"] for ref in entry.get("source_refs", [])
    }
    assert f"git:{sha_a}" in all_src_refs
    assert f"git:{sha_b}" in all_src_refs
    for entry in batch["entries"]:
        assert entry["status"] == "draft"
    # dry-run accepts the batch
    result2 = runner.invoke(
        app,
        [
            "--cwd",
            str(repo),
            "entry",
            "add-many",
            "0.2.0",
            "--file",
            str(out_file),
            "--dry-run",
        ],  # noqa: E501
    )
    assert result2.exit_code == 0, result2.output


def test_git_import_next_no_release_required(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _commit(repo, "root", "README.md")
    _git(repo, "tag", "v0.1.0")
    _commit(repo, "feat: add a", "a.txt")
    runner.invoke(app, ["--cwd", str(repo), "init"])
    out_file = repo / "batch.yaml"
    result = runner.invoke(
        app,
        [
            "--cwd",
            str(repo),
            "git",
            "import",
            "next",
            "--base",
            "v0.1.0",
            "--head",
            "HEAD",
            "--status",
            "draft",
            "--output",
            str(out_file),
        ],
    )
    assert result.exit_code == 0, result.output
    assert out_file.is_file()


# --------------------------------------------------------------------------
# release update --git-base/--git-head
# --------------------------------------------------------------------------


def test_release_update_stores_git_metadata(tmp_path: Path) -> None:
    repo, sha_a, sha_b = _setup_release(tmp_path)
    data = _jrun(repo, "release", "show", "0.2.0")
    release = data["result"]["release"]
    assert release["git_base_ref"] == "v0.1.0"
    assert release["git_head_ref"] == "HEAD"
    assert release["git_commit_count"] == 2
    assert release["git_range"] is not None
    assert len(release["git_base_sha"]) == 40
    assert len(release["git_head_sha"]) == 40


def test_release_update_clear_git_range(tmp_path: Path) -> None:
    repo, sha_a, sha_b = _setup_release(tmp_path)
    result = runner.invoke(
        app,
        ["--cwd", str(repo), "release", "update", "0.2.0", "--clear-git-range"],
    )
    assert result.exit_code == 0, result.output
    data = _jrun(repo, "release", "show", "0.2.0")
    release = data["result"]["release"]
    assert release["git_base_ref"] is None
    assert release["git_range"] is None


# --------------------------------------------------------------------------
# Error handling
# --------------------------------------------------------------------------


def test_git_range_fails_without_base(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _commit(repo, "root", "README.md")
    _git(repo, "tag", "v0.1.0")
    _commit(repo, "feat: add a", "a.txt")
    runner.invoke(app, ["--cwd", str(repo), "init"])
    runner.invoke(
        app,
        [
            "--cwd",
            str(repo),
            "release",
            "create",
            "0.2.0",
            "--previous",
            "0.1.0",
            "--released-at",
            "2026-06-14",
        ],  # noqa: E501
    )
    result = runner.invoke(app, ["--cwd", str(repo), "git", "range", "0.2.0"])
    assert result.exit_code != 0
