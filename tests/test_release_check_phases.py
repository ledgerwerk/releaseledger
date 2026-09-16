"""Regression tests for lifecycle-aware release review/check changelog modes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from releaseledger.cli import app

runner = CliRunner()


def _run(tmp_path: Path, *args: str):
    return runner.invoke(app, ["--cwd", str(tmp_path), *args])


def _json_run(tmp_path: Path, *args: str) -> dict[str, object]:
    result = runner.invoke(app, ["--cwd", str(tmp_path), "--json", *args])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def _init_keepachangelog(tmp_path: Path) -> None:
    result = _run(tmp_path, "init")
    assert result.exit_code == 0, result.output
    config = tmp_path / ".ledger" / "releaseledger" / "config.toml"
    config.write_text(
        """config_version = 2
ledger_ref = "main"
ledger_parent_ref = ""
ledger_branch_guard = "off"

[ledger]
code = "rl"

[release]
default_changelog = "CHANGELOG.md"
default_status = "planned"
allow_dirty_worktree = true

[changelog]
output = "CHANGELOG.md"
standard = "keepachangelog-1.1.0"
group_mode = "keepachangelog"
link_references = false
repository_url = ""
tag_prefix = "v"
compare_url_template = ""
release_url_template = ""
preamble = ""
semver = true
unreleased = true
trim = true
render_always = false
header = ""
footer = ""
postprocessors = []
"""
    )


def _seed_release(tmp_path: Path, *, status: str = "planned") -> None:
    result = _run(tmp_path, "release", "create", "0.2.0", "--status", status)
    assert result.exit_code == 0, result.output
    result = _run(
        tmp_path,
        "entry",
        "add",
        "0.2.0",
        "--kind",
        "added",
        "--summary",
        "Feature",
    )
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize("status", ["planned", "draft", "candidate"])
def test_current_active_undated_release_uses_unreleased_mode(
    tmp_path: Path, status: str
) -> None:
    _init_keepachangelog(tmp_path)
    _seed_release(tmp_path, status=status)

    review = _json_run(tmp_path, "review", "0.2.0", "--strict")
    review_result = review["result"]
    assert isinstance(review_result, dict)
    assert review_result["ok"] is True
    changelog = review_result["changelog"]
    assert isinstance(changelog, dict)
    assert changelog["dry_run_ok"] is True
    assert changelog["mode"] == "unreleased"
    assert changelog["unreleased"] is True
    assert changelog["effective_release_date"] is None
    assert changelog["section_heading"] == "## [0.2.0] - Unreleased"
    assert review_result["release"]["released_at"] is None

    check = _json_run(tmp_path, "release", "check", "0.2.0", "--strict")
    assert check["ok"] is True
    check_result = check["result"]
    assert isinstance(check_result, dict)
    assert check_result["checks"]["changelog_ok"] is True


def test_direct_strict_build_keeps_explicit_unreleased_contract(tmp_path: Path) -> None:
    _init_keepachangelog(tmp_path)
    _seed_release(tmp_path)

    rejected = _run(tmp_path, "changelog", "build", "0.2.0", "--strict")
    assert rejected.exit_code != 0
    assert "release date" in rejected.output.lower()

    accepted = _run(
        tmp_path,
        "changelog",
        "build",
        "0.2.0",
        "--strict",
        "--unreleased",
        "--dry-run",
    )
    assert accepted.exit_code == 0, accepted.output


def test_finalize_requires_date_and_uses_dated_mode(tmp_path: Path) -> None:
    _init_keepachangelog(tmp_path)
    _seed_release(tmp_path)

    missing_date = _run(
        tmp_path, "release", "check", "0.2.0", "--phase", "finalize", "--strict"
    )
    assert missing_date.exit_code != 0

    finalized = _json_run(
        tmp_path,
        "release",
        "check",
        "0.2.0",
        "--phase",
        "finalize",
        "--released-at",
        "2026-09-16",
        "--strict",
    )
    assert finalized["ok"] is True
    result = finalized["result"]
    assert isinstance(result, dict)
    changelog = result["changelog"]
    assert isinstance(changelog, dict)
    assert changelog["mode"] == "released"
    assert changelog["unreleased"] is False
    assert changelog["effective_release_date"] == "2026-09-16"
    assert result["release"]["released_at"] is None


def test_published_requires_released_state_and_persisted_date(tmp_path: Path) -> None:
    _init_keepachangelog(tmp_path)
    result = _run(tmp_path, "release", "tag", "0.2.0", "--released-at", "2026-09-16")
    assert result.exit_code == 0, result.output
    _run(
        tmp_path,
        "entry",
        "add",
        "0.2.0",
        "--kind",
        "added",
        "--summary",
        "Feature",
    )

    published = _json_run(
        tmp_path, "release", "check", "0.2.0", "--phase", "published", "--strict"
    )
    assert published["ok"] is True
    result = published["result"]
    assert isinstance(result, dict)
    changelog = result["changelog"]
    assert isinstance(changelog, dict)
    assert changelog["mode"] == "released"
    assert changelog["effective_release_date"] == "2026-09-16"
