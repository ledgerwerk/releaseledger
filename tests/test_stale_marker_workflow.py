"""End-to-end regression for the generated-cache marker incident."""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

from releaseledger.services.audit import update_commit_audit_sheet
from releaseledger.services.changelog_build import build_changelog_file
from releaseledger.services.entries import add_many_release_entries
from releaseledger.services.events import load_events
from releaseledger.services.releases import finalize_release, prepare_release
from releaseledger.services.review import build_release_review
from releaseledger.storage.paths import initialize_project, resolve_project_paths
from releaseledger.storage.store import load_entries, load_release


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _commit(root: Path, message: str, name: str) -> None:
    (root / name).write_text(message + "\n", encoding="utf-8")
    _git(root, "add", name)
    _git(root, "commit", "-m", message)


def test_stale_marker_prepare_and_publish_workflow_is_idempotent(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test User")
    _commit(tmp_path, "root", "README.md")
    _git(tmp_path, "tag", "v0.1.0")
    _commit(tmp_path, "feature", "feature.txt")

    initialize_project(tmp_path)
    from releaseledger.services.releases import create_release

    create_release(
        tmp_path,
        version="v0.1.0",
        status="released",
        released_at="2026-01-01",
    )
    paths = resolve_project_paths(tmp_path)
    paths.project.indexes_binding_path.unlink()

    prepared = prepare_release(
        tmp_path,
        version="v0.2.0",
        previous_version="v0.1.0",
        released_at="2026-02-01",
        git_base_ref="v0.1.0",
        git_head_ref="HEAD",
        output_dir=Path(".ledger/releaseledger/work"),
    )
    assert paths.project.indexes_binding_path.is_file()
    assert prepared["proposed_released_at"] == "2026-02-01"

    scaffold_path = Path(str(prepared["outputs"]["entries_yaml"]))
    scaffold = yaml.safe_load(scaffold_path.read_text(encoding="utf-8"))
    entries = scaffold["entries"]
    assert entries
    entries[0]["summary"] = "Added the reviewed feature"
    entries[0]["status"] = "accepted"
    entries[0]["contributors"] = ["@alice"]
    entries[0]["prs"] = ["github:pr-42"]
    result = add_many_release_entries(
        tmp_path,
        release_version="v0.2.0",
        entries=entries,
        sync_audit=True,
    )
    assert result["written"] is True
    assert load_entries(tmp_path, "v0.2.0")[0].contributors == tuple(
        entries[0]["contributors"]
    )

    audit_path = Path(str(prepared["outputs"]["audit_yaml"]))
    audit = yaml.safe_load(audit_path.read_text(encoding="utf-8"))
    audit["rows"][0].update(
        {
            "inspected": True,
            "inspected_paths": ["feature.txt"],
            "observed_behavior": "Feature is available to release users.",
            "public_impact": "public",
            "decision": "accepted",
        }
    )
    audit_path.write_text(
        yaml.safe_dump(audit, sort_keys=False), encoding="utf-8"
    )
    update_commit_audit_sheet(
        tmp_path, version="v0.2.0", file=audit_path
    )

    finalize_release(tmp_path, version="v0.2.0", released_at="2026-02-01")
    build_changelog_file(
        tmp_path,
        version="v0.2.0",
        target_file=tmp_path / "CHANGELOG.md",
        replace_existing=False,
    )
    _git(tmp_path, "tag", "v0.2.0")
    published = build_release_review(
        tmp_path,
        version="v0.2.0",
        strict=True,
        git=True,
        phase="published",
        target_file=tmp_path / "CHANGELOG.md",
    )
    assert published["ok"] is True, (
        published["failed_checks"],
        published.get("warnings"),
        published["checks"],
    )
    assert "snapshot" not in published["failed_checks"]
    assert len([event for event in load_events(tmp_path) if event.event == "release.created"]) == 2

    before_revision = load_release(tmp_path, "v0.2.0").versioning.revision
    build_changelog_file(
        tmp_path,
        version="v0.2.0",
        target_file=tmp_path / "CHANGELOG.md",
        replace_existing=True,
    )
    assert load_release(tmp_path, "v0.2.0").versioning.revision == before_revision
