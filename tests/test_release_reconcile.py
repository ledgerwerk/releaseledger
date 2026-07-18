from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from releaseledger.services import releases as releases_service
from releaseledger.services.releases import create_release, reconcile_releases
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
    (tmp_path / "CHANGELOG.md").write_text("## [0.1.0] - 2026-01-01\n## [0.3.0] - Unreleased\n")

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
