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


def test_git_range_uses_stored_head_not_current_head(tmp_path: Path) -> None:
    """Regression: without --head, use the stored release head, not current HEAD."""
    repo = _init_repo(tmp_path)
    _commit(repo, "root", "README.md")
    _git(repo, "tag", "v0.1.0")
    sha_a = _commit(repo, "feat: add a", "a.txt")
    sha_b = _commit(repo, "fix: handle b", "b.txt")
    # Tag the release head at a fixed commit, then move HEAD past it.
    _git(repo, "tag", "v0.2.0")
    extra_sha = _commit(repo, "feat: add c", "c.txt")
    runner.invoke(app, ["--cwd", str(repo), "init"])
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
        ],
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
            "v0.2.0",
        ],
    )
    assert result.exit_code == 0, result.output
    # Without --head: stored range v0.1.0..v0.2.0 -> 2 commits, not the extra one.
    data = _jrun(repo, "git", "range", "0.2.0")
    assert data["result"]["head_ref"] == "v0.2.0"
    assert data["result"]["commit_count"] == 2
    sha_refs = {c["source_ref"] for c in data["result"]["candidates"]}
    assert f"git:{sha_a}" in sha_refs
    assert f"git:{sha_b}" in sha_refs
    assert f"git:{extra_sha}" not in sha_refs
    # Explicit --head HEAD scans to current HEAD and includes the extra commit.
    data_head = _jrun(repo, "git", "range", "0.2.0", "--head", "HEAD")
    assert data_head["result"]["head_ref"] == "HEAD"
    assert data_head["result"]["commit_count"] == 3
    head_refs = {c["source_ref"] for c in data_head["result"]["candidates"]}
    assert f"git:{extra_sha}" in head_refs


def test_git_import_uses_stored_head_not_current_head(tmp_path: Path) -> None:
    """Regression: git import should scaffold from the pinned stored snapshot."""
    repo = _init_repo(tmp_path)
    _commit(repo, "root", "README.md")
    _git(repo, "tag", "v0.1.0")
    sha_a = _commit(repo, "feat: add a", "a.txt")
    sha_b = _commit(repo, "fix: handle b", "b.txt")
    runner.invoke(app, ["--cwd", str(repo), "init"])
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
        ],
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
        ],
    )
    assert result.exit_code == 0, result.output
    extra_sha = _commit(repo, "feat: add c", "c.txt")
    out = repo / "entries.yaml"
    payload = _jrun(repo, "git", "import", "0.2.0", "--output", str(out))
    assert payload["result"]["head_ref"] == "HEAD"
    assert payload["result"]["head_sha"] == sha_b
    assert payload["result"]["entry_count"] == 2
    batch = yaml.safe_load(out.read_text(encoding="utf-8"))
    refs = {entry["source_refs"][0] for entry in batch["entries"]}
    assert f"git:{sha_a}" in refs
    assert f"git:{sha_b}" in refs
    assert f"git:{extra_sha}" not in refs


def test_git_scaffold_alias_emits_metadata_rich_batch(tmp_path: Path) -> None:
    repo, sha_a, sha_b = _setup_release(tmp_path)
    out = repo / "entries.yaml"
    payload = _jrun(repo, "git", "scaffold", "0.2.0", "--output", str(out))
    assert payload["command"] == "git.scaffold"
    assert payload["result_type"] == "git_scaffold"
    batch = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert payload["result"]["entry_count"] == 2
    assert batch["object_type"] == "release_entry_batch"
    assert batch["release_version"] == "0.2.0"
    assert len(batch["git_base_sha"]) == 40
    assert len(batch["git_head_sha"]) == 40
    refs = {entry["source_refs"][0] for entry in batch["entries"]}
    assert refs == {f"git:{sha_a}", f"git:{sha_b}"}


# --------------------------------------------------------------------------
# git range --evidence
# --------------------------------------------------------------------------


def test_git_range_evidence_json_includes_full_fields(tmp_path: Path) -> None:
    repo, sha_a, sha_b = _setup_release(tmp_path)
    data = _jrun(repo, "git", "range", "0.2.0", "--evidence")
    assert data["ok"] is True
    candidates = data["result"]["candidates"]
    assert len(candidates) == 2
    by_ref = {c["source_ref"]: c for c in candidates}
    cand_a = by_ref[f"git:{sha_a}"]
    # Evidence-only fields must be present and populated.
    for field in ("paths", "additions", "deletions", "pr_refs", "issue_refs"):
        assert field in cand_a, f"missing evidence field: {field}"
    assert isinstance(cand_a["paths"], list)
    assert "a.txt" in cand_a["paths"]
    assert cand_a["additions"] is not None and cand_a["additions"] >= 1
    assert cand_a["deletions"] is not None and cand_a["deletions"] >= 0
    assert cand_a["pr_refs"] == []
    assert cand_a["issue_refs"] == []
    # diff_excerpt is optional in the dataclass but populated for real diffs.
    assert cand_a["diff_excerpt"] is None or isinstance(cand_a["diff_excerpt"], str)


def test_git_range_evidence_human_output_marks_evidence(tmp_path: Path) -> None:
    repo, sha_a, sha_b = _setup_release(tmp_path)
    result = runner.invoke(
        app,
        ["--cwd", str(repo), "git", "range", "0.2.0", "--evidence"],
    )
    assert result.exit_code == 0, result.output
    assert "evidence:" in result.output
    assert "paths:" in result.output


def test_git_range_without_evidence_omits_fields(tmp_path: Path) -> None:
    repo, sha_a, sha_b = _setup_release(tmp_path)
    data = _jrun(repo, "git", "range", "0.2.0")
    candidates = data["result"]["candidates"]
    cand = candidates[0]
    # Default view keeps the compact fields only.
    assert set(cand.keys()) == {
        "sha",
        "short_sha",
        "source_ref",
        "inferred_kind",
        "subject",
    }


def test_git_evidence_exports_manifest_and_patches(tmp_path: Path) -> None:
    repo, sha_a, sha_b = _setup_release(tmp_path)
    out_dir = tmp_path / "evidence"
    payload = _jrun(repo, "git", "evidence", "0.2.0", "--output-dir", str(out_dir))
    assert payload["result"]["candidate_count"] == 2
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["object_type"] == "git_evidence_manifest"
    patch_files = {candidate["patch_file"] for candidate in manifest["candidates"]}
    assert patch_files == {f"{sha_a[:7]}.patch", f"{sha_b[:7]}.patch"}
    for patch_file in patch_files:
        assert (out_dir / patch_file).is_file()


def test_release_prepare_exports_snapshot_artifacts(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _commit(repo, "root", "README.md")
    _git(repo, "tag", "v0.1.0")
    _commit(repo, "feat: add a", "a.txt")
    _commit(repo, "fix: handle b", "b.txt")
    runner.invoke(app, ["--cwd", str(repo), "init"])
    out_dir = tmp_path / "prep"
    payload = _jrun(
        repo,
        "release",
        "prepare",
        "0.2.0",
        "--previous",
        "0.1.0",
        "--git-base",
        "v0.1.0",
        "--git-head",
        "HEAD",
        "--output-dir",
        str(out_dir),
    )
    assert payload["result_type"] == "release_prepare"
    assert (out_dir / "range.json").is_file()
    assert (out_dir / "audit.yaml").is_file()
    assert (out_dir / "entries.yaml").is_file()


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


def test_git_range_next_root_base_returns_all_commits(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    sha_a = _commit(repo, "feat: add a", "a.txt")
    sha_b = _commit(repo, "feat: add b", "b.txt")
    runner.invoke(app, ["--cwd", str(repo), "init"])
    data = _jrun(repo, "git", "range", "next", "--base", ":root", "--head", "HEAD")
    assert data["result"]["base_ref"] == ":root"
    assert data["result"]["commit_count"] == 2
    refs = {c["source_ref"] for c in data["result"]["candidates"]}
    assert f"git:{sha_a}" in refs
    assert f"git:{sha_b}" in refs


def test_release_update_root_base_persists_range(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _commit(repo, "root", "README.md")
    _commit(repo, "feat: add a", "a.txt")
    _git(repo, "tag", "v0.1.0")
    _commit(repo, "feat: add b", "b.txt")
    _git(repo, "tag", "v0.2.0")
    runner.invoke(app, ["--cwd", str(repo), "init"])
    runner.invoke(
        app,
        ["--cwd", str(repo), "release", "create", "0.2.0", "--previous", "0.1.0"],
    )
    upd = runner.invoke(
        app,
        [
            "--cwd",
            str(repo),
            "release",
            "update",
            "0.2.0",
            "--git-base",
            ":root",
            "--git-head",
            "v0.2.0",
        ],
    )
    assert upd.exit_code == 0, upd.output
    show = _jrun(repo, "release", "show", "0.2.0")
    release = show["result"]["release"]
    assert release["git_base_ref"] == ":root"
    # Empty-tree base covers all commits from the start of the repo (3 total).
    assert release["git_commit_count"] == 3


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
        assert entry["summary"] == ""
    # dry-run rejects the scaffold until summaries are manually written
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
    assert result2.exit_code != 0
    assert "Entry batch validation failed" in result2.output


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
