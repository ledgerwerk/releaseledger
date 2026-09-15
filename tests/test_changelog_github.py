"""Tests for structured GitHub-style changelog rendering."""

from __future__ import annotations

from pathlib import Path

import tomlkit

from releaseledger.services.changelog_build import render_changelog_section
from releaseledger.services.entries import add_release_entry
from releaseledger.services.releases import create_release
from releaseledger.storage.paths import initialize_project


def _configure_github(tmp_path: Path, **values: bool | str) -> None:
    path = tmp_path / ".ledger" / "releaseledger" / "config.toml"
    document = tomlkit.parse(path.read_text(encoding="utf-8"))
    changelog = document["changelog"]
    assert isinstance(changelog, dict)
    for key, value in values.items():
        changelog[key] = value
    path.write_text(tomlkit.dumps(document), encoding="utf-8")


def _seed_current_release(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    create_release(
        tmp_path,
        version="1.0.0",
        status="released",
        released_at="2026-01-01",
    )
    add_release_entry(
        tmp_path,
        release_version="1.0.0",
        kind="added",
        summary="Added old feature",
        contributors=("@old",),
    )
    create_release(
        tmp_path,
        version="1.1.0",
        status="released",
        released_at="2026-02-01",
        previous_version="1.0.0",
    )
    add_release_entry(
        tmp_path,
        release_version="1.1.0",
        kind="changed",
        summary="Updated current feature",
        prs=("github:pr-3",),
        contributors=("@alice",),
    )


def test_github_attribution_new_contributors_and_compare(tmp_path: Path) -> None:
    _seed_current_release(tmp_path)
    _configure_github(
        tmp_path,
        repository_url="https://github.com/example/project",
        github_attribution=True,
        github_new_contributors=True,
        github_full_compare=True,
    )

    result = render_changelog_section(tmp_path, version="1.1.0")
    section = str(result["section"])
    assert "Updated current feature by @alice in [#3]" in section
    assert "### New Contributors" not in section
    assert "first contribution" not in section
    assert (
        "**Full Changelog**: [v1.0.0...v1.1.0](https://github.com/example/project/compare/v1.0.0...v1.1.0)"
        in section
    )


def test_github_duplicate_mode_is_explicit(tmp_path: Path) -> None:
    _seed_current_release(tmp_path)
    _configure_github(
        tmp_path,
        github_attribution=True,
        github_whats_changed=True,
        github_duplicate_categorized_entries=True,
    )

    section = str(render_changelog_section(tmp_path, version="1.1.0")["section"])
    assert "### What's Changed" in section
    assert section.count("Updated current feature") == 2


def test_github_flags_false_preserve_plain_output(tmp_path: Path) -> None:
    _seed_current_release(tmp_path)
    _configure_github(
        tmp_path,
        github_attribution=False,
        github_whats_changed=False,
        github_new_contributors=False,
        github_full_compare=False,
        github_duplicate_categorized_entries=False,
    )

    section = str(render_changelog_section(tmp_path, version="1.1.0")["section"])
    assert "- Updated current feature" in section
    assert "by @alice" not in section
    assert "What's Changed" not in section
    assert "New Contributors" not in section
    assert "Full Changelog" not in section
