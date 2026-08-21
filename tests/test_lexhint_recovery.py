from __future__ import annotations

import datetime
import os
import subprocess
from pathlib import Path

from releaseledger.services.changelog_build import build_full_changelog_file
from releaseledger.services.entries import add_release_entry
from releaseledger.services.releases import (
    create_release,
    rename_release,
    repair_release_chain,
    restore_release,
)
from releaseledger.storage.paths import ensure_canonical_project
from releaseledger.storage.store import load_entries, load_release


def _git(repo: Path, *args: str) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Tester",
        "GIT_AUTHOR_EMAIL": "tester@example.com",
        "GIT_COMMITTER_NAME": "Tester",
        "GIT_COMMITTER_EMAIL": "tester@example.com",
    }
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        env=env,
    )


def _add_entries(root: Path, version: str, count: int) -> None:
    for index in range(count):
        add_release_entry(
            root,
            release_version=version,
            kind="added",
            summary=f"entry {version} {index}",
        )


def test_lexhint_shaped_canceled_release_recovery(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.name", "Tester")
    _git(tmp_path, "config", "user.email", "tester@example.com")
    (tmp_path / "README.md").write_text("initial\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-m", "initial")
    _git(tmp_path, "tag", "v0.1.0")
    (tmp_path / "README.md").write_text("next\n", encoding="utf-8")
    _git(tmp_path, "commit", "-am", "next")
    _git(tmp_path, "tag", "v0.1.1")
    ensure_canonical_project(tmp_path)

    create_release(tmp_path, version="v0.1.0", status="canceled")
    _add_entries(tmp_path, "v0.1.0", 11)
    create_release(tmp_path, version="0.1.0", status="canceled")
    _add_entries(tmp_path, "0.1.0", 4)
    create_release(
        tmp_path,
        version="0.1.1",
        status="released",
        released_at=datetime.datetime.now(tz=datetime.timezone.utc).date().isoformat(),
    )
    _add_entries(tmp_path, "0.1.1", 2)
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "# Changelog\n\n## Unreleased\n\nold content\n", encoding="utf-8"
    )

    rename_release(
        tmp_path,
        old_version="v0.1.0",
        new_version="0.1.0",
        replace_canceled_target=True,
        reason="Consolidate the duplicate canceled release bundles.",
    )
    restore_release(
        tmp_path,
        version="0.1.0",
        from_tag="v0.1.0",
        reason="The tagged release was actually shipped.",
    )
    repair_release_chain(tmp_path, apply_changes=True)
    build_full_changelog_file(
        tmp_path,
        target_file=changelog,
        strict=True,
        preserve_unreleased=False,
        require_complete_history=True,
    )

    assert len(load_entries(tmp_path, "0.1.0")) == 11
    assert load_release(tmp_path, "0.1.0").status == "released"
    assert load_release(tmp_path, "0.1.1").previous_version == "0.1.0"
    assert "## Unreleased" not in changelog.read_text(encoding="utf-8")
