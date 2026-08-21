from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from releaseledger.services import releases as releases_service
from releaseledger.services.releases import (
    _parse_changelog_headings,
    create_release,
    import_tags,
    reconcile_releases,
)
from releaseledger.storage.paths import ensure_canonical_project


def test_reconcile_reports_tag_and_changelog_mismatches(
    tmp_path: Path, monkeypatch
) -> None:
    ensure_canonical_project(tmp_path)
    create_release(
        tmp_path,
        version="0.1.0",
        status="released",
        released_at="2026-01-01",
    )
    create_release(tmp_path, version="0.2.0", status="planned")
    (tmp_path / "CHANGELOG.md").write_text(
        "## [0.1.0] - 2026-01-01\n## [0.3.0] - Unreleased\n"
    )

    def fake_run(args, **kwargs):
        if args[-2:] == ["tag", "--list"]:
            return SimpleNamespace(returncode=0, stdout="v0.1.0\nv0.2.0\n", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(releases_service.subprocess, "run", fake_run)
    result = reconcile_releases(tmp_path)
    kinds = {problem["kind"] for problem in result["problems"]}
    assert "planned_with_tag" in kinds
    assert "changelog_without_release" in kinds
    assert result["ok"] is False


def test_parse_changelog_headings_canonical(tmp_path: Path) -> None:
    """Canonical ## [VERSION] - DATE headings are parsed correctly."""
    target = tmp_path / "CHANGELOG.md"
    target.write_text(
        "# Changelog\n"
        "\n"
        "## [0.7.0] - 2026-07-26\n"
        "## [0.4.0] - 2026-01-11\n"
        "## [0.1.0] - 2025-12-01\n"
    )
    headings = _parse_changelog_headings(target)
    assert sorted(headings) == ["0.1.0", "0.4.0", "0.7.0"]


def test_parse_changelog_headings_legacy_version(tmp_path: Path) -> None:
    """Legacy '## Version X (date)' headings are parsed correctly."""
    target = tmp_path / "CHANGELOG.md"
    target.write_text(
        "# Changelog\n"
        "## Version 0.4.0 (2026-01-11)\n"
        "## Version 0.1.0 (Initial Release)\n"
    )
    headings = _parse_changelog_headings(target)
    assert sorted(headings) == ["0.1.0", "0.4.0"]


def test_parse_changelog_headings_bare_version(tmp_path: Path) -> None:
    """Bare '## VERSION' headings are parsed correctly."""
    target = tmp_path / "CHANGELOG.md"
    target.write_text("# Changelog\n## 0.7.0\n## 0.5.1\n")
    headings = _parse_changelog_headings(target)
    assert sorted(headings) == ["0.5.1", "0.7.0"]


def test_parse_changelog_headings_v_prefix_normalized(tmp_path: Path) -> None:
    """v-prefixes are normalized away."""
    target = tmp_path / "CHANGELOG.md"
    target.write_text("## [v0.7.0] - 2026-07-26\n## v0.4.0\n")
    headings = _parse_changelog_headings(target)
    assert sorted(headings) == ["0.4.0", "0.7.0"]


def test_parse_changelog_headings_ignores_narrative(tmp_path: Path) -> None:
    """Narrative headings like 'Previous Unreleased Changes' are ignored."""
    target = tmp_path / "CHANGELOG.md"
    target.write_text(
        "# Changelog\n"
        "## [0.7.0] - 2026-07-26\n"
        "## Previous Unreleased Changes\n"
        "## Some Other Section\n"
    )
    headings = _parse_changelog_headings(target)
    assert sorted(headings) == ["0.7.0"]


def test_parse_changelog_headings_skips_unreleased(tmp_path: Path) -> None:
    """## [Unreleased] is skipped."""
    target = tmp_path / "CHANGELOG.md"
    target.write_text("# Changelog\n## [Unreleased]\n## [0.7.0] - 2026-07-26\n")
    headings = _parse_changelog_headings(target)
    assert sorted(headings) == ["0.7.0"]


def test_parse_changelog_headings_mixed_formats(tmp_path: Path) -> None:
    """A changelog with mixed heading formats parses all versions."""
    target = tmp_path / "CHANGELOG.md"
    target.write_text(
        "# Changelog\n"
        "## [0.7.0] - Unreleased\n"
        "## Version 0.4.0 (2026-01-11)\n"
        "## Previous Unreleased Changes\n"
        "## Version 0.1.0 (Initial Release)\n"
    )
    headings = _parse_changelog_headings(target)
    assert sorted(headings) == ["0.1.0", "0.4.0", "0.7.0"]


def test_parse_changelog_headings_empty_file(tmp_path: Path) -> None:
    """An empty or missing file returns no headings."""
    target = tmp_path / "CHANGELOG.md"
    target.write_text("")
    headings = _parse_changelog_headings(target)
    assert headings == {}

    # Missing file also returns empty
    headings = _parse_changelog_headings(tmp_path / "nonexistent.md")
    assert headings == {}


def test_parse_changelog_headings_duplicate_versions(tmp_path: Path) -> None:
    """Duplicate version headings are collected into a list."""
    target = tmp_path / "CHANGELOG.md"
    target.write_text("## [0.7.0] - 2026-07-26\n## [0.7.0] - 2026-07-27\n")
    headings = _parse_changelog_headings(target)
    assert len(headings["0.7.0"]) == 2


def test_import_tags_dry_run(tmp_path: Path, monkeypatch) -> None:
    """Dry-run discovers tags but does not create records."""
    ensure_canonical_project(tmp_path)

    def fake_run(args, **kwargs):
        if args[-2:] == ["tag", "--list"]:
            return SimpleNamespace(
                returncode=0, stdout="v0.1.0\nv0.2.0\nv0.3.0\n", stderr=""
            )
        if len(args) >= 2 and args[-2] == "log":
            return SimpleNamespace(returncode=0, stdout="2026-01-01\n", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(releases_service.subprocess, "run", fake_run)
    result = import_tags(tmp_path, apply=False)
    assert result["applied"] is False
    assert result["discovered_tag_count"] == 3
    assert result["applied_count"] == 0
    plans = [p for p in result["plans"] if p["action"] == "create"]
    assert len(plans) == 3


def test_import_tags_apply(tmp_path: Path, monkeypatch) -> None:
    """Apply creates release records for missing tags."""
    ensure_canonical_project(tmp_path)
    # Pre-create one release
    create_release(
        tmp_path, version="0.1.0", status="released", released_at="2026-01-01"
    )

    def fake_run(args, **kwargs):
        if args[-2:] == ["tag", "--list"]:
            return SimpleNamespace(
                returncode=0, stdout="v0.1.0\nv0.2.0\nv0.3.0\n", stderr=""
            )
        if len(args) >= 2 and args[-2] == "log":
            return SimpleNamespace(returncode=0, stdout="2026-01-15\n", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(releases_service.subprocess, "run", fake_run)
    result = import_tags(tmp_path, apply=True)
    assert result["applied"] is True
    assert result["applied_count"] == 2  # 0.2.0 and 0.3.0
    assert result["skipped_count"] == 1  # 0.1.0 already existed


def test_import_tags_idempotent(tmp_path: Path, monkeypatch) -> None:
    """Running import-tags twice does not duplicate records."""
    ensure_canonical_project(tmp_path)

    def fake_run(args, **kwargs):
        if args[-2:] == ["tag", "--list"]:
            return SimpleNamespace(returncode=0, stdout="v0.1.0\nv0.2.0\n", stderr="")
        if len(args) >= 2 and args[-2] == "log":
            return SimpleNamespace(returncode=0, stdout="2026-01-01\n", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(releases_service.subprocess, "run", fake_run)
    # First run
    result1 = import_tags(tmp_path, apply=True)
    assert result1["applied_count"] == 2
    # Second run - should skip all
    result2 = import_tags(tmp_path, apply=True)
    assert result2["applied_count"] == 0
    assert result2["skipped_count"] == 2


def _tag_run(tags: str):
    def fake_run(args, **kwargs):
        if args[-2:] == ["tag", "--list"]:
            return SimpleNamespace(returncode=0, stdout=tags, stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="")
    return fake_run



def test_reconcile_reports_canceled_release_with_matching_tag(
    tmp_path: Path, monkeypatch
 ) -> None:
    ensure_canonical_project(tmp_path)
    create_release(tmp_path, version="0.1.0", status="canceled")
    monkeypatch.setattr(releases_service.subprocess, "run", _tag_run("v0.1.0\n"))
    result = reconcile_releases(tmp_path)
    assert any(
        problem["kind"] == "canceled_with_tag"
        for problem in result["problems"]
    )



def test_reconcile_assigns_alias_evidence_to_active_owner(
    tmp_path: Path, monkeypatch
 ) -> None:
    ensure_canonical_project(tmp_path)
    create_release(tmp_path, version="0.1.0", status="canceled")
    create_release(
        tmp_path,
        version="v0.1.0",
        status="released",
        released_at="2026-01-01",
    )
    (tmp_path / "CHANGELOG.md").write_text("## [0.1.0] - 2026-01-01\n")
    monkeypatch.setattr(releases_service.subprocess, "run", _tag_run("v0.1.0\n"))
    result = reconcile_releases(tmp_path)
    kinds = {problem["kind"] for problem in result["problems"]}
    assert "canceled_with_tag" not in kinds
    assert "canceled_with_changelog" not in kinds



def test_reconcile_reports_multiple_active_aliases(
    tmp_path: Path, monkeypatch
 ) -> None:
    ensure_canonical_project(tmp_path)
    create_release(tmp_path, version="0.1.0", status="candidate")
    create_release(tmp_path, version="v0.1.0", status="draft")
    monkeypatch.setattr(releases_service.subprocess, "run", _tag_run(""))
    result = reconcile_releases(tmp_path)
    assert any(
        problem["kind"] == "ambiguous_active_release_identity"
        for problem in result["problems"]
    )



def test_import_tags_marks_canceled_alias_for_restore(
    tmp_path: Path, monkeypatch
 ) -> None:
    ensure_canonical_project(tmp_path)
    create_release(tmp_path, version="v0.1.0", status="canceled")
    monkeypatch.setattr(releases_service.subprocess, "run", _tag_run("v0.1.0\n"))
    result = import_tags(tmp_path, apply=False)
    assert result["plans"][0]["action"] == "needs_restore"
    assert result["plans"][0]["records"] == ["v0.1.0"]
