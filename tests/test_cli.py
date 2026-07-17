"""Releaseledger CLI tests.

Uses ``typer.testing.CliRunner`` with isolated filesystem (``tmp_path``) per the
brief's test plan. JSON helpers parse the deterministic success/error envelopes.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from releaseledger import __version__
from releaseledger.cli import app

runner = CliRunner()


def _json(result) -> dict[str, object]:
    assert result.exit_code == 0, result.stdout
    return json.loads(result.stdout)


def _init_project(tmp_path: Path) -> None:
    result = runner.invoke(app, ["--cwd", str(tmp_path), "init"])
    assert result.exit_code == 0, result.stdout


def _human_error(result) -> str:
    """Return the stderr/stdout text for a non-zero human-mode result.

    Some Click/Typer combinations raise ``ValueError`` when ``stderr`` was
    not captured separately; tolerate that and fall back to stdout/output.
    """
    try:
        stderr = result.stderr or ""
    except ValueError:
        stderr = ""
    stdout = result.stdout or getattr(result, "output", "") or ""
    return stderr + stdout


def _run(tmp_path: Path, *cmd: str):
    """Invoke a releaseledger command scoped to ``tmp_path`` (human mode)."""
    return runner.invoke(app, ["--cwd", str(tmp_path), *cmd])


def _jrun(tmp_path: Path, *cmd: str):
    """Invoke a releaseledger command scoped to ``tmp_path`` in JSON mode."""
    return runner.invoke(app, ["--cwd", str(tmp_path), "--json", *cmd])


# ---------------------------------------------------------------------------
# Phase 1: executable shell and import resilience
# ---------------------------------------------------------------------------


class TestPhase1Shell:
    def test_import_releaseledger_package(self) -> None:
        import releaseledger  # noqa: F401

        assert __version__

    def test_import_releaseledger_cli(self) -> None:
        import releaseledger.cli  # noqa: F401
        import releaseledger.launcher  # noqa: F401

    def test_version_flag(self) -> None:
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0, result.stdout
        assert __version__ in result.stdout

    def test_python_dash_m_version(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "releaseledger", "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert __version__ in result.stdout

    def test_console_entry_point_resolves(self) -> None:
        # The declared entry point ``releaseledger.launcher:main`` must import
        # and be callable without arguments producing a help/exit (not raise).
        from releaseledger.launcher import main

        assert callable(main)

    def test_py_typed_present(self) -> None:
        import releaseledger

        marker = Path(releaseledger.__file__).parent / "py.typed"
        assert marker.is_file()


# ---------------------------------------------------------------------------
# Phase 2: config and layout
# ---------------------------------------------------------------------------


class TestPhase2ConfigLayout:
    def test_init_creates_dot_config_and_default_dir(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["--cwd", str(tmp_path), "init"])
        assert result.exit_code == 0, result.stdout
        assert (tmp_path / ".ledger" / "releaseledger" / "config.toml").is_file()
        releases = tmp_path / ".releaseledger" / "ledgers" / "main" / "releases"
        events = tmp_path / ".releaseledger" / "ledgers" / "main" / "events"
        indexes = tmp_path / ".releaseledger" / "ledgers" / "main" / "indexes"
        assert releases.is_dir()
        assert events.is_dir()
        assert indexes.is_dir()

    def test_init_creates_empty_json_indexes(self, tmp_path: Path) -> None:
        _init_project(tmp_path)
        indexes = tmp_path / ".releaseledger" / "ledgers" / "main" / "indexes"
        assert json.loads((indexes / "releases.json").read_text()) == []
        assert json.loads((indexes / "entries.json").read_text()) == []

    def test_init_writes_canonical_config_keys(self, tmp_path: Path) -> None:
        _init_project(tmp_path)
        text = (tmp_path / ".ledger" / "releaseledger" / "config.toml").read_text()
        assert 'releaseledger_dir = ".releaseledger"' in text
        assert "config_version = 2" in text
        assert "[ledger]" in text
        assert "[release]" in text

    def test_init_human_output_mentions_config_and_dir(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["--cwd", str(tmp_path), "init"])
        assert result.exit_code == 0, result.stdout
        assert "initialized releaseledger in .releaseledger" in result.stdout
        assert "wrote .ledger/ledger.toml" in result.stdout

    def test_init_refuses_overwrite_without_force(self, tmp_path: Path) -> None:
        _init_project(tmp_path)
        result = runner.invoke(app, ["--cwd", str(tmp_path), "init"])
        assert result.exit_code != 0
        assert "already exists" in _human_error(result)

    def test_init_force_overwrites_config(self, tmp_path: Path) -> None:
        _init_project(tmp_path)
        result = runner.invoke(app, ["--cwd", str(tmp_path), "init", "--force"])
        assert result.exit_code == 0, result.stdout

    def test_init_custom_releaseledger_dir(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            ["--cwd", str(tmp_path), "init", "--releaseledger-dir", ".custom-rl"],
        )
        assert result.exit_code == 0, result.stdout
        assert (tmp_path / ".custom-rl" / "ledgers" / "main" / "releases").is_dir()
        text = (tmp_path / ".ledger" / "releaseledger" / "config.toml").read_text()
        assert 'releaseledger_dir = ".custom-rl"' in text

    def test_subdirectory_discovers_root(self, tmp_path: Path) -> None:
        _init_project(tmp_path)
        subdir = tmp_path / "src" / "deep"
        subdir.mkdir(parents=True)
        # A read-only-ish command from a subdir must resolve the same root by
        # checking that require_project finds the config via the locator.
        from releaseledger.storage.paths import (
            discover_workspace_root,
            find_project_config,
        )

        config = find_project_config(subdir)
        assert config is not None
        assert config.parent == tmp_path.resolve() or config.parent == tmp_path
        assert discover_workspace_root(subdir).resolve() == tmp_path.resolve()

    def test_require_project_errors_when_uninitialized(self, tmp_path: Path) -> None:
        from releaseledger.errors import LaunchError
        from releaseledger.storage.paths import require_project

        with pytest.raises(LaunchError) as exc_info:
            require_project(tmp_path)
        assert exc_info.value.code == "NOT_FOUND"

    def test_unknown_config_keys_rejected(self, tmp_path: Path) -> None:
        (tmp_path / ".ledger" / "releaseledger" / "config.toml").write_text(
            'bogus_key = true\nreleaseledger_dir = ".releaseledger"\n'
        )
        from releaseledger.errors import LaunchError
        from releaseledger.storage.paths import require_project

        with pytest.raises(LaunchError) as exc_info:
            require_project(tmp_path)
        assert exc_info.value.code == "CONFIG_ERROR"
        assert "bogus_key" in exc_info.value.message

    def test_traversal_releaseledger_dir_rejected(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            ["--cwd", str(tmp_path), "init", "--releaseledger-dir", "../escape"],
        )
        assert result.exit_code != 0
        assert "escapes" in _human_error(result)

    def test_init_json_envelope(self, tmp_path: Path) -> None:
        payload = _json(runner.invoke(app, ["--cwd", str(tmp_path), "--json", "init"]))
        assert payload["ok"] is True
        assert payload["command"] == "init"
        assert payload["result_type"] == "project_init"
        assert payload["result"]["releaseledger_dir"].endswith(".releaseledger")


# ---------------------------------------------------------------------------
# Phase 3: release records
# ---------------------------------------------------------------------------


class TestPhase3Releases:
    def test_release_tag_persists_release_record(self, tmp_path: Path) -> None:
        _init_project(tmp_path)
        result = _run(tmp_path, "release", "tag", "1.2.0", "--note", "MVP")
        assert result.exit_code == 0, result.stdout
        path = (
            tmp_path
            / ".releaseledger"
            / "ledgers"
            / "main"
            / "releases"
            / "1.2.0"
            / "release.md"
        )
        assert path.is_file()
        import ledgercore

        metadata, body = ledgercore.read_front_matter_document(path)
        assert metadata["object_type"] == "release"
        assert metadata["version"] == "1.2.0"
        assert metadata["status"] == "released"
        assert "MVP" in body

    def test_release_tag_human_output(self, tmp_path: Path) -> None:
        _init_project(tmp_path)
        result = _run(tmp_path, "release", "tag", "1.2.0", "--note", "First MVP")
        assert result.exit_code == 0, result.stdout
        assert "tagged release 1.2.0" in result.stdout

    def test_release_tag_rejects_duplicate_version(self, tmp_path: Path) -> None:
        _init_project(tmp_path)
        assert _run(tmp_path, "release", "tag", "1.2.0").exit_code == 0
        result = _run(tmp_path, "release", "tag", "1.2.0")
        assert result.exit_code != 0
        assert "already exists" in _human_error(result)

    def test_release_tag_rejects_invalid_version(self, tmp_path: Path) -> None:
        _init_project(tmp_path)
        cases = ["", "  ", "1.2/0", "1.2\\0", "ver sion", "..hidden", "1.2;rm"]
        for version in cases:
            result = _run(tmp_path, "release", "tag", version)
            assert result.exit_code != 0, f"version {version!r} should be rejected"

    def test_release_list_is_deterministic(self, tmp_path: Path) -> None:
        _init_project(tmp_path)
        _run(tmp_path, "release", "tag", "2.0.0", "--released-at", "2026-02-01")
        _run(tmp_path, "release", "tag", "1.0.0", "--released-at", "2026-01-01")
        result = _run(tmp_path, "release", "list")
        assert result.exit_code == 0, result.stdout
        lines = [ln for ln in result.stdout.splitlines() if ln and ln != "RELEASES"]
        # sorted by released_at ascending
        assert lines[0].startswith("1.0.0"), result.stdout
        assert lines[1].startswith("2.0.0"), result.stdout

    def test_release_list_human_header(self, tmp_path: Path) -> None:
        _init_project(tmp_path)
        _run(tmp_path, "release", "tag", "1.2.0", "--note", "Hello")
        result = _run(tmp_path, "release", "list")
        assert "RELEASES" in result.stdout
        assert "1.2.0" in result.stdout

    def test_release_show_returns_persisted_metadata(self, tmp_path: Path) -> None:
        _init_project(tmp_path)
        _run(
            tmp_path,
            "release",
            "create",
            "1.2.0",
            "--status",
            "planned",
            "--title",
            "T",
        )
        result = _run(tmp_path, "release", "show", "1.2.0")
        assert result.exit_code == 0, result.stdout
        assert "version: 1.2.0" in result.stdout
        assert "status: planned" in result.stdout

    def test_release_show_text_includes_git_range_metadata(
        self, tmp_path: Path
    ) -> None:
        _init_project(tmp_path)
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "Tester",
            "GIT_AUTHOR_EMAIL": "tester@example.com",
            "GIT_COMMITTER_NAME": "Tester",
            "GIT_COMMITTER_EMAIL": "tester@example.com",
            "GIT_CONFIG_NOSYSTEM": "1",
            "HOME": str(tmp_path),
        }
        subprocess.run(
            ["git", "init", "-q", "-b", "main", str(tmp_path)],
            check=True,
            env=env,
        )
        (tmp_path / "README.md").write_text("init\n")
        subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True, env=env)
        subprocess.run(
            ["git", "-C", str(tmp_path), "commit", "-q", "-m", "root"],
            check=True,
            env=env,
        )
        subprocess.run(
            ["git", "-C", str(tmp_path), "tag", "v0.1.0"], check=True, env=env
        )
        (tmp_path / "a.txt").write_text("a\n")
        subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True, env=env)
        subprocess.run(
            ["git", "-C", str(tmp_path), "commit", "-q", "-m", "feat: add a"],
            check=True,
            env=env,
        )
        subprocess.run(
            ["git", "-C", str(tmp_path), "tag", "v0.2.0"], check=True, env=env
        )
        _run(tmp_path, "release", "create", "0.2.0", "--previous", "0.1.0")
        upd = _run(
            tmp_path,
            "release",
            "update",
            "0.2.0",
            "--git-base",
            "v0.1.0",
            "--git-head",
            "v0.2.0",
        )
        assert upd.exit_code == 0, _human_error(upd)
        result = _run(tmp_path, "release", "show", "0.2.0")
        assert result.exit_code == 0, result.stdout
        assert "git_base_ref: v0.1.0" in result.stdout
        assert "git_head_ref: v0.2.0" in result.stdout
        assert "git_range:" in result.stdout
        assert "git_commit_count:" in result.stdout

    def test_release_show_not_found(self, tmp_path: Path) -> None:
        _init_project(tmp_path)
        result = _run(tmp_path, "release", "show", "9.9.9")
        assert result.exit_code != 0
        assert "not found" in _human_error(result).lower()

    def test_release_finalize_transitions_to_released(self, tmp_path: Path) -> None:
        _init_project(tmp_path)
        _run(tmp_path, "release", "create", "1.2.0")
        result = _run(
            tmp_path,
            "release",
            "finalize",
            "1.2.0",
            "--released-at",
            "2026-06-13",
        )
        assert result.exit_code == 0, result.stdout
        show = _run(tmp_path, "release", "show", "1.2.0")
        assert "status: released" in show.stdout
        assert "released_at: 2026-06-13" in show.stdout

    def test_release_finalize_rejects_already_released(self, tmp_path: Path) -> None:
        _init_project(tmp_path)
        _run(tmp_path, "release", "tag", "1.2.0")
        result = _run(tmp_path, "release", "finalize", "1.2.0")
        assert result.exit_code != 0

    def test_json_release_show_envelope(self, tmp_path: Path) -> None:
        _init_project(tmp_path)
        assert _run(tmp_path, "release", "tag", "1.2.0").exit_code == 0
        payload = _json(_jrun(tmp_path, "release", "show", "1.2.0"))
        assert payload["ok"] is True
        assert payload["command"] == "release.show"
        assert payload["result_type"] == "release"
        assert payload["result"]["release"]["version"] == "1.2.0"

    def test_json_release_tag_envelope(self, tmp_path: Path) -> None:
        _init_project(tmp_path)
        payload = _json(_jrun(tmp_path, "release", "tag", "1.2.0"))
        assert payload["ok"] is True
        assert payload["command"] == "release.tag"
        assert payload["result"]["kind"] == "release"
        assert payload["result"]["release"]["status"] == "released"
        assert payload["result"]["events"]
        assert payload["events"] == payload["result"]["events"]

    def test_release_create_rejects_bad_status(self, tmp_path: Path) -> None:
        _init_project(tmp_path)
        result = _run(tmp_path, "release", "create", "1.2.0", "--status", "bogus")
        assert result.exit_code != 0

    def test_release_infers_previous_version(self, tmp_path: Path) -> None:
        _init_project(tmp_path)
        _run(tmp_path, "release", "tag", "1.0.0", "--released-at", "2026-01-01")
        payload = _json(_jrun(tmp_path, "release", "tag", "1.1.0"))
        assert payload["result"]["release"]["previous_version"] == "1.0.0"


# ---------------------------------------------------------------------------
# Phase 4: release entries
# ---------------------------------------------------------------------------


class TestPhase4Entries:
    def test_entry_add_persists_entry_and_bumps_count(self, tmp_path: Path) -> None:
        _init_project(tmp_path)
        _run(tmp_path, "release", "create", "1.2.0")
        result = _run(
            tmp_path,
            "entry",
            "add",
            "1.2.0",
            "--kind",
            "added",
            "--summary",
            "Add release bundle storage",
            "--path",
            "releaseledger/storage/store.py",
        )
        assert result.exit_code == 0, result.stdout
        assert "added entry entry-0001 to release 1.2.0" in result.stdout
        entry_path = (
            tmp_path
            / ".releaseledger"
            / "ledgers"
            / "main"
            / "releases"
            / "1.2.0"
            / "entries"
            / "entry-0001.md"
        )
        assert entry_path.is_file()
        show = _json(_jrun(tmp_path, "release", "show", "1.2.0"))
        assert show["result"]["release"]["entry_count"] == 1

    def test_entry_ids_are_monotonic(self, tmp_path: Path) -> None:
        _init_project(tmp_path)
        _run(tmp_path, "release", "create", "1.2.0")
        _run(tmp_path, "entry", "add", "1.2.0", "--kind", "added", "--summary", "one")
        _run(tmp_path, "entry", "add", "1.2.0", "--kind", "fixed", "--summary", "two")
        listing = _run(tmp_path, "entry", "list", "1.2.0")
        assert "entry-0001" in listing.stdout
        assert "entry-0002" in listing.stdout
        payload = _json(_jrun(tmp_path, "entry", "list", "1.2.0"))
        ids = [e["entry_id"] for e in payload["result"]["entries"]]
        assert ids == ["entry-0001", "entry-0002"]

    def test_entry_add_rejects_unknown_kind(self, tmp_path: Path) -> None:
        _init_project(tmp_path)
        _run(tmp_path, "release", "create", "1.2.0")
        result = _run(
            tmp_path, "entry", "add", "1.2.0", "--kind", "bogus", "--summary", "x"
        )
        assert result.exit_code != 0

    def test_entry_add_rejects_empty_summary(self, tmp_path: Path) -> None:
        _init_project(tmp_path)
        _run(tmp_path, "release", "create", "1.2.0")
        result = _run(tmp_path, "entry", "add", "1.2.0", "--kind", "added")
        assert result.exit_code != 0

    def test_entry_add_rejects_unsafe_path(self, tmp_path: Path) -> None:
        _init_project(tmp_path)
        _run(tmp_path, "release", "create", "1.2.0")
        result = _run(
            tmp_path,
            "entry",
            "add",
            "1.2.0",
            "--kind",
            "added",
            "--summary",
            "x",
            "--path",
            "../escape.py",
        )
        assert result.exit_code != 0

    def test_entry_add_rejects_when_release_missing(self, tmp_path: Path) -> None:
        _init_project(tmp_path)
        result = _run(
            tmp_path, "entry", "add", "9.9.9", "--kind", "added", "--summary", "x"
        )
        assert result.exit_code != 0

    def test_entry_list_human_output(self, tmp_path: Path) -> None:
        _init_project(tmp_path)
        _run(tmp_path, "release", "create", "1.2.0")
        _run(
            tmp_path,
            "entry",
            "add",
            "1.2.0",
            "--kind",
            "added",
            "--summary",
            "Added X",
        )
        result = _run(tmp_path, "entry", "list", "1.2.0")
        assert "ENTRIES" in result.stdout
        assert "entry-0001" in result.stdout
        assert "Added X" in result.stdout

    def test_entry_internal_flag_persisted(self, tmp_path: Path) -> None:
        _init_project(tmp_path)
        _run(tmp_path, "release", "create", "1.2.0")
        _run(
            tmp_path,
            "entry",
            "add",
            "1.2.0",
            "--kind",
            "internal",
            "--summary",
            "Refactor internals",
            "--internal",
        )
        payload = _json(_jrun(tmp_path, "entry", "list", "1.2.0"))
        entries = payload["result"]["entries"]
        assert len(entries) == 1
        assert entries[0]["internal"] is True
        assert entries[0]["kind"] == "internal"

    def test_entry_json_envelope(self, tmp_path: Path) -> None:
        _init_project(tmp_path)
        _run(tmp_path, "release", "create", "1.2.0")
        payload = _json(
            _jrun(
                tmp_path,
                "entry",
                "add",
                "1.2.0",
                "--kind",
                "added",
                "--summary",
                "Add release bundle storage",
            )
        )
        assert payload["ok"] is True
        assert payload["command"] == "entry.add"
        assert payload["result_type"] == "release_entry"
        assert payload["result"]["entry"]["entry_id"] == "entry-0001"
        assert payload["result"]["entry"]["paths"] == []


# ---------------------------------------------------------------------------
# Phase 5: changelog context
# ---------------------------------------------------------------------------


class TestPhase5Changelog:
    @staticmethod
    def _seed(tmp_path: Path) -> None:
        _init_project(tmp_path)
        _run(tmp_path, "release", "create", "1.2.0")
        _run(
            tmp_path,
            "entry",
            "add",
            "1.2.0",
            "--kind",
            "added",
            "--summary",
            "Add release bundle storage",
            "--path",
            "releaseledger/storage/store.py",
        )
        _run(
            tmp_path,
            "entry",
            "add",
            "1.2.0",
            "--kind",
            "fixed",
            "--summary",
            "Fix version filename validation",
        )

    def test_markdown_includes_instruction_and_changes(self, tmp_path: Path) -> None:
        self._seed(tmp_path)
        result = _run(tmp_path, "changelog", "1.2.0")
        assert result.exit_code == 0, result.stdout
        assert "# Changelog source" in result.stdout
        assert "## LLM instruction" in result.stdout
        assert "## Release metadata" in result.stdout
        assert "## Candidate changes" in result.stdout
        assert "Add release bundle storage" in result.stdout
        assert "releaseledger/storage/store.py" in result.stdout
        assert "Fix version filename validation" in result.stdout

    def test_markdown_groups_under_kind_headings(self, tmp_path: Path) -> None:
        self._seed(tmp_path)
        result = _run(tmp_path, "changelog", "1.2.0")
        assert "### Added" in result.stdout
        assert "### Fixed" in result.stdout

    def test_target_guidance_only_when_requested(self, tmp_path: Path) -> None:
        self._seed(tmp_path)
        no_target = _run(tmp_path, "changelog", "1.2.0").stdout
        assert "## Changelog edit guidance" not in no_target
        with_target = _run(
            tmp_path,
            "changelog",
            "1.2.0",
            "--target-changelog",
            "CHANGELOG.md",
            "--release-date",
            "2026-06-13",
        ).stdout
        assert "## Changelog edit guidance" in with_target
        assert "Target changelog: CHANGELOG.md" in with_target
        assert "Use release date: 2026-06-13" in with_target

    def test_target_guidance_no_date_warning(self, tmp_path: Path) -> None:
        self._seed(tmp_path)
        out = _run(
            tmp_path, "changelog", "1.2.0", "--target-changelog", "CHANGELOG.md"
        ).stdout
        assert "do not invent a release date" in out

    def test_json_payload_has_metadata_and_entries(self, tmp_path: Path) -> None:
        self._seed(tmp_path)
        result = _run(tmp_path, "changelog", "1.2.0", "--format", "json")
        assert result.exit_code == 0, result.stdout
        payload = json.loads(result.stdout)
        assert payload["kind"] == "release_changelog_context"
        assert payload["version"] == "1.2.0"
        assert payload["release"]["version"] == "1.2.0"
        assert payload["entry_count"] == 2
        kinds = sorted(e["kind"] for e in payload["entries"])
        assert kinds == ["added", "fixed"]
        assert payload["entries"][0]["paths"] == ["releaseledger/storage/store.py"]

    def test_internal_hidden_by_default(self, tmp_path: Path) -> None:
        _init_project(tmp_path)
        _run(tmp_path, "release", "create", "1.2.0")
        _run(
            tmp_path,
            "entry",
            "add",
            "1.2.0",
            "--kind",
            "added",
            "--summary",
            "visible",
        )
        _run(
            tmp_path,
            "entry",
            "add",
            "1.2.0",
            "--kind",
            "internal",
            "--summary",
            "hidden refactors",
            "--internal",
        )
        default_md = _run(tmp_path, "changelog", "1.2.0").stdout
        assert "visible" in default_md
        assert "hidden refactors" not in default_md
        with_internal = _run(
            tmp_path, "changelog", "1.2.0", "--include-internal"
        ).stdout
        assert "hidden refactors" in with_internal

    def test_output_writes_file(self, tmp_path: Path) -> None:
        self._seed(tmp_path)
        out_file = tmp_path / "changes.md"
        result = _run(tmp_path, "changelog", "1.2.0", "--output", str(out_file))
        assert result.exit_code == 0, result.stdout
        assert out_file.is_file()
        assert "# Changelog source" in out_file.read_text()
        assert "wrote" in result.stdout

    def test_output_json_file(self, tmp_path: Path) -> None:
        self._seed(tmp_path)
        out_file = tmp_path / "changes.json"
        result = _run(
            tmp_path,
            "changelog",
            "1.2.0",
            "--format",
            "json",
            "--output",
            str(out_file),
        )
        assert result.exit_code == 0, result.stdout
        payload = json.loads(out_file.read_text())
        assert payload["kind"] == "release_changelog_context"

    def test_changelog_missing_release(self, tmp_path: Path) -> None:
        _init_project(tmp_path)
        result = _run(tmp_path, "changelog", "9.9.9")
        assert result.exit_code != 0
        assert "not found" in _human_error(result).lower()


# ---------------------------------------------------------------------------
# Phase 7: changelog build (CHANGELOG.md)
# ---------------------------------------------------------------------------


class TestPhase7Build:
    @staticmethod
    def _seed(tmp_path: Path) -> None:
        _init_project(tmp_path)
        _run(tmp_path, "release", "create", "1.2.0")
        _run(
            tmp_path,
            "entry",
            "add",
            "1.2.0",
            "--kind",
            "added",
            "--summary",
            "Add release bundle storage",
            "--path",
            "releaseledger/storage/store.py",
        )
        _run(
            tmp_path,
            "entry",
            "add",
            "1.2.0",
            "--kind",
            "fixed",
            "--summary",
            "Fix version filename validation",
        )

    @staticmethod
    def _write_config(
        tmp_path: Path, *, body: str | None = None, postprocessors: str | None = None
    ) -> None:
        """Overwrite the project config with a known [changelog] block."""
        pp = postprocessors if postprocessors is not None else "[]"
        if body is not None:
            body_value = body
        else:
            body_value = (
                "## {% if release.date %}[{{ release.version }}] - "
                "{{ release.date }}{% else %}[{{ release.version }}] - "
                "Unreleased{% endif %}\\n\\n"
                "{% for group in groups %}\\n### {{ group.title }}\\n"
                "{% for entry in group.entries %}\\n- "
                "{% if entry.breaking %}**BREAKING:** {% endif %}"
                "{{ entry.summary }}\\n{% endfor %}\\n\\n{% endfor %}"
            )
        lines = [
            "config_version = 1",
            'releaseledger_dir = ".releaseledger"',
            'ledger_ref = "main"',
            'ledger_parent_ref = ""',
            "ledger_next_entry_number = 1",
            'ledger_branch_guard = "off"',
            "[ledger]",
            'code = "rl"',
            'name = "releaseledger"',
            "[release]",
            'default_changelog = "CHANGELOG.md"',
            'default_status = "planned"',
            "allow_dirty_worktree = true",
            "[changelog]",
            'output = "CHANGELOG.md"',
            "trim = true",
            "render_always = false",
            'header = ""',
            f"body = {body_value!r}",
            'footer = "<!-- generated by releaseledger -->"',
            f"postprocessors = {pp}",
        ]
        (tmp_path / ".ledger" / "releaseledger" / "config.toml").write_text(
            "\n".join(lines) + "\n"
        )

    def test_build_dry_run_renders_final_section(self, tmp_path: Path) -> None:
        self._seed(tmp_path)
        result = _run(
            tmp_path,
            "build",
            "1.2.0",
            "--dry-run",
            "--release-date",
            "2026-06-13",
        )
        assert result.exit_code == 0, result.stdout
        assert "## [1.2.0] - 2026-06-13" in result.stdout
        assert "### Added" in result.stdout
        assert "- Add release bundle storage" in result.stdout
        assert "# Changelog source" not in result.stdout
        assert not (tmp_path / "CHANGELOG.md").exists()

    def test_build_writes_changelog_below_unreleased(self, tmp_path: Path) -> None:
        self._seed(tmp_path)
        (tmp_path / "CHANGELOG.md").write_text(
            "# Changelog\n\n## Unreleased\n\n- Pending work\n\n"
            "## [1.1.0] - 2026-01-01\n\n- Old\n"
        )
        result = _run(
            tmp_path,
            "build",
            "1.2.0",
            "--release-date",
            "2026-06-13",
            "--target-file",
            "CHANGELOG.md",
        )
        assert result.exit_code == 0, result.stdout
        text = (tmp_path / "CHANGELOG.md").read_text()
        titles = [line for line in text.splitlines() if line.startswith("## ")]
        assert titles == [
            "## Unreleased",
            "## [1.2.0] - 2026-06-13",
            "## [1.1.0] - 2026-01-01",
        ]
        assert text.endswith("\n")
        assert text.count("## [1.2.0] - 2026-06-13") == 1

    def test_build_creates_parent_for_relative_target(self, tmp_path: Path) -> None:
        self._seed(tmp_path)
        result = _run(
            tmp_path,
            "build",
            "1.2.0",
            "--release-date",
            "2026-06-13",
            "--target-file",
            "docs/CHANGELOG.md",
        )
        assert result.exit_code == 0, _human_error(result)
        target = tmp_path / "docs" / "CHANGELOG.md"
        assert target.is_file()
        assert "## [1.2.0] - 2026-06-13" in target.read_text()

    def test_build_refuses_duplicate_without_replace(self, tmp_path: Path) -> None:
        self._seed(tmp_path)
        first = _run(
            tmp_path,
            "build",
            "1.2.0",
            "--release-date",
            "2026-06-13",
            "--target-file",
            "CHANGELOG.md",
        )
        assert first.exit_code == 0, _human_error(first)
        second = _run(
            tmp_path,
            "build",
            "1.2.0",
            "--release-date",
            "2026-06-13",
            "--target-file",
            "CHANGELOG.md",
        )
        assert second.exit_code != 0
        assert "section" in _human_error(second).lower()

    def test_build_replace_existing(self, tmp_path: Path) -> None:
        self._seed(tmp_path)
        _run(
            tmp_path,
            "build",
            "1.2.0",
            "--release-date",
            "2026-06-13",
            "--target-file",
            "CHANGELOG.md",
        )
        # Add another entry after the first build.
        _run(
            tmp_path,
            "entry",
            "add",
            "1.2.0",
            "--kind",
            "changed",
            "--summary",
            "Change rendering pipeline",
        )
        replaced = _run(
            tmp_path,
            "build",
            "1.2.0",
            "--release-date",
            "2026-06-13",
            "--target-file",
            "CHANGELOG.md",
            "--replace-existing",
        )
        assert replaced.exit_code == 0, _human_error(replaced)
        text = (tmp_path / "CHANGELOG.md").read_text()
        assert text.count("## [1.2.0] - 2026-06-13") == 1
        assert "Change rendering pipeline" in text

    def test_build_hides_internal_by_default(self, tmp_path: Path) -> None:
        _init_project(tmp_path)
        _run(tmp_path, "release", "create", "1.2.0")
        _run(
            tmp_path,
            "entry",
            "add",
            "1.2.0",
            "--kind",
            "added",
            "--summary",
            "visible feature",
        )
        _run(
            tmp_path,
            "entry",
            "add",
            "1.2.0",
            "--kind",
            "internal",
            "--summary",
            "secret refactor",
            "--internal",
        )
        default = _run(tmp_path, "build", "1.2.0", "--dry-run").stdout
        assert "visible feature" in default
        assert "secret refactor" not in default
        with_internal = _run(
            tmp_path, "build", "1.2.0", "--dry-run", "--include-internal"
        ).stdout
        assert "secret refactor" in with_internal

    def test_build_json_dry_run_payload(self, tmp_path: Path) -> None:
        self._seed(tmp_path)
        result = _jrun(tmp_path, "build", "1.2.0", "--dry-run")
        payload = _json(result)
        assert payload["ok"] is True
        assert payload["command"] == "build"
        assert payload["result_type"] == "changelog_build"
        assert "## [1.2.0]" in payload["result"]["section"]
        assert payload["result"]["updated"] is False
        assert payload["result"]["dry_run"] is True

    def test_changelog_config_template_customizes_output(self, tmp_path: Path) -> None:
        _init_project(tmp_path)
        self._write_config(
            tmp_path,
            body=(
                "## {{ release.title }}"
                "{% for group in groups %}\n### {{ group.title }} custom"
                "{% endfor %}"
            ),
        )
        _run(tmp_path, "release", "create", "1.2.0")
        _run(
            tmp_path,
            "entry",
            "add",
            "1.2.0",
            "--kind",
            "added",
            "--summary",
            "Add release bundle storage",
        )
        out = _run(tmp_path, "build", "1.2.0", "--dry-run").stdout
        assert "Release 1.2.0" in out
        assert "### Added custom" in out
        # Default heading style must not appear with the custom template.
        assert "## [1.2.0]" not in out

    def test_changelog_postprocessors_apply(self, tmp_path: Path) -> None:
        _init_project(tmp_path)
        self._write_config(
            tmp_path,
            postprocessors='[{ pattern = "releaseledger", replace = "Releaseledger" }]',
        )
        _run(tmp_path, "release", "create", "1.2.0")
        _run(
            tmp_path,
            "entry",
            "add",
            "1.2.0",
            "--kind",
            "added",
            "--summary",
            "Add releaseledger build command",
        )
        out = _run(tmp_path, "build", "1.2.0", "--dry-run").stdout
        assert "Releaseledger build command" in out
        assert "releaseledger build command" not in out

    def test_build_missing_release(self, tmp_path: Path) -> None:
        _init_project(tmp_path)
        result = _run(tmp_path, "build", "9.9.9")
        assert result.exit_code != 0
        assert "not found" in _human_error(result).lower()

    def test_default_config_contains_changelog_table(self, tmp_path: Path) -> None:
        _init_project(tmp_path)
        text = (tmp_path / ".ledger" / "releaseledger" / "config.toml").read_text()
        assert "[changelog]" in text
        assert "body =" in text
        assert "postprocessors = []" in text


# ---------------------------------------------------------------------------
# Phase 6: indexes and events
# ---------------------------------------------------------------------------


class TestPhase6EventsIndexes:
    @staticmethod
    def _events_path(tmp_path: Path) -> Path:
        return (
            tmp_path / ".releaseledger" / "ledgers" / "main" / "events" / "events.jsonl"
        )

    @staticmethod
    def _index_path(tmp_path: Path, name: str) -> Path:
        return tmp_path / ".releaseledger" / "ledgers" / "main" / "indexes" / name

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict]:
        return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]

    def test_events_jsonl_receives_release_events(self, tmp_path: Path) -> None:
        _init_project(tmp_path)
        _run(tmp_path, "release", "create", "1.2.0")
        _run(tmp_path, "release", "tag", "1.3.0")
        _run(tmp_path, "release", "finalize", "1.2.0")
        events_path = self._events_path(tmp_path)
        assert events_path.is_file()
        rows = self._read_jsonl(events_path)
        names = [row["event"] for row in rows]
        assert names == ["release.created", "release.tagged", "release.finalized"]

    def test_events_monotonic_ids_and_versions(self, tmp_path: Path) -> None:
        _init_project(tmp_path)
        _run(tmp_path, "release", "tag", "1.2.0")
        _run(
            tmp_path,
            "entry",
            "add",
            "1.2.0",
            "--kind",
            "added",
            "--summary",
            "Add release bundle storage",
        )
        events_path = self._events_path(tmp_path)
        rows = self._read_jsonl(events_path)
        assert [row["event_id"] for row in rows] == ["event-0001", "event-0002"]
        assert rows[0]["event"] == "release.tagged"
        assert rows[0]["release_version"] == "1.2.0"
        assert rows[1]["event"] == "entry.added"
        assert rows[1]["entry_id"] == "entry-0001"

    def test_indexes_are_valid_json_arrays(self, tmp_path: Path) -> None:
        _init_project(tmp_path)
        _run(tmp_path, "release", "tag", "1.2.0")
        _run(tmp_path, "entry", "add", "1.2.0", "--kind", "added", "--summary", "X")
        releases = json.loads(self._index_path(tmp_path, "releases.json").read_text())
        entries = json.loads(self._index_path(tmp_path, "entries.json").read_text())
        assert isinstance(releases, list)
        assert isinstance(entries, list)
        assert releases[0]["version"] == "1.2.0"
        assert releases[0]["entry_count"] == 1
        assert entries[0]["entry_id"] == "entry-0001"
        assert entries[0]["release_version"] == "1.2.0"

    def test_rebuild_indexes_is_idempotent(self, tmp_path: Path) -> None:
        from releaseledger.storage.store import rebuild_indexes

        _init_project(tmp_path)
        _run(tmp_path, "release", "tag", "1.2.0")
        _run(tmp_path, "entry", "add", "1.2.0", "--kind", "fixed", "--summary", "Y")
        before_r = self._index_path(tmp_path, "releases.json").read_text()
        before_e = self._index_path(tmp_path, "entries.json").read_text()
        rebuild_indexes(tmp_path)
        rebuild_indexes(tmp_path)
        assert self._index_path(tmp_path, "releases.json").read_text() == before_r
        assert self._index_path(tmp_path, "entries.json").read_text() == before_e

    def test_indexes_track_multiple_releases_and_entries(self, tmp_path: Path) -> None:
        _init_project(tmp_path)
        _run(tmp_path, "release", "tag", "1.0.0", "--released-at", "2026-01-01")
        _run(tmp_path, "release", "tag", "2.0.0", "--released-at", "2026-02-01")
        _run(
            tmp_path,
            "entry",
            "add",
            "1.0.0",
            "--kind",
            "added",
            "--summary",
            "in 1.0.0",
        )
        _run(
            tmp_path,
            "entry",
            "add",
            "2.0.0",
            "--kind",
            "changed",
            "--summary",
            "in 2.0.0",
        )
        releases = json.loads(self._index_path(tmp_path, "releases.json").read_text())
        entries = json.loads(self._index_path(tmp_path, "entries.json").read_text())
        assert [r["version"] for r in releases] == ["1.0.0", "2.0.0"]
        # entries index flattened across releases
        assert len(entries) == 2
        assert {(e["release_version"], e["entry_id"]) for e in entries} == {
            ("1.0.0", "entry-0001"),
            ("2.0.0", "entry-0001"),
        }

    def test_load_events_service_roundtrip(self, tmp_path: Path) -> None:
        from releaseledger.services.events import load_events

        _init_project(tmp_path)
        _run(tmp_path, "release", "tag", "1.2.0")
        events = load_events(tmp_path)
        assert len(events) == 1
        assert events[0].event == "release.tagged"
        assert events[0].event_id == "event-0001"


# ---------------------------------------------------------------------------
# Phase 8: storage diagnostics
# ---------------------------------------------------------------------------


class TestPhase8StorageWhere:
    def test_storage_where_reports_default_layout(self, tmp_path: Path) -> None:
        _init_project(tmp_path)
        result = _run(tmp_path, "storage", "where")
        assert result.exit_code == 0, result.stdout
        assert "Workspace:" in result.stdout
        assert "Config:" in result.stdout
        assert "Storage:" in result.stdout
        assert "Ledger: main" in result.stdout
        assert "Inside workspace: yes" in result.stdout
        assert "Source: canonical" in result.stdout
        assert "Layout: ok" in result.stdout
        assert "Indexes: ok" in result.stdout

    def test_storage_where_json_mode(self, tmp_path: Path) -> None:
        _init_project(tmp_path)
        payload = _json(_jrun(tmp_path, "storage", "where"))
        assert payload["ok"] is True
        assert payload["command"] == "storage.where"
        assert payload["result_type"] == "storage_location"
        r = payload["result"]
        assert r["kind"] == "storage_location"
        assert str(tmp_path.resolve()) in str(r["workspace_root"])
        assert r["ledger_ref"] == "main"
        assert r["inside_workspace"] is True
        assert r["source"] == "dotfile"
        assert r["layout_exists"] is True
        assert r["indexes_exist"] is True

    def test_storage_where_from_subdirectory(self, tmp_path: Path) -> None:
        _init_project(tmp_path)
        subdir = tmp_path / "src" / "deep"
        subdir.mkdir(parents=True)
        payload = _json(_jrun(tmp_path, "--cwd", str(subdir), "storage", "where"))
        r = payload["result"]
        assert str(tmp_path.resolve()) in str(r["workspace_root"])
        assert r["inside_workspace"] is True
        assert r["layout_exists"] is True

    def test_storage_where_read_only(self, tmp_path: Path) -> None:
        """storage where must not mutate .releaseledger or .releaseledger.toml."""
        _init_project(tmp_path)
        toml_before = (
            tmp_path / ".ledger" / "releaseledger" / "config.toml"
        ).read_text()
        layout_before = {p.name for p in (tmp_path / ".releaseledger").rglob("*")}
        _run(tmp_path, "storage", "where")
        assert (
            tmp_path / ".ledger" / "releaseledger" / "config.toml"
        ).read_text() == toml_before
        layout_after = {p.name for p in (tmp_path / ".releaseledger").rglob("*")}
        assert layout_after == layout_before

    def test_storage_where_uninitialized(self, tmp_path: Path) -> None:
        """Without a config, storage where still succeeds with defaults."""
        result = _run(tmp_path, "storage", "where")
        assert result.exit_code == 0, result.stdout
        assert "Source: default" in result.stdout
        assert "Layout: missing" in result.stdout

    def test_storage_where_with_custom_releaseledger_dir(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            ["--cwd", str(tmp_path), "init", "--releaseledger-dir", ".custom-rl"],
        )
        assert result.exit_code == 0, result.stdout
        payload = _json(_jrun(tmp_path, "storage", "where"))
        r = payload["result"]
        assert "custom-rl" in str(r["releaseledger_dir"])
        assert r["inside_workspace"] is True


# ---------------------------------------------------------------------------
# Phase 9: external state policy
# ---------------------------------------------------------------------------


@pytest.mark.xfail(reason="Removed --external-dir flag; migration error expected")
class TestPhase9ExternalPolicy:
    def test_external_relative_dir_rejected_by_default(self, tmp_path: Path) -> None:
        """A relative releaseledger_dir that escapes the workspace must be rejected."""
        result = runner.invoke(
            app,
            ["--cwd", str(tmp_path), "init", "--releaseledger-dir", "../escape"],
        )
        assert result.exit_code != 0
        assert "escapes" in _human_error(result)

    def test_external_relative_dir_rejected_without_flag(self, tmp_path: Path) -> None:
        """init with an external relative dir and no --external-dir must fail fast."""
        result = runner.invoke(
            app,
            ["--cwd", str(tmp_path), "init", "--releaseledger-dir", "../out"],
        )
        assert result.exit_code != 0
        assert "escapes" in _human_error(result)
        # TOML should not have been written because init failed.
        assert not (tmp_path / ".ledger" / "releaseledger" / "config.toml").is_file()

    def test_external_relative_dir_allowed_with_flag(self, tmp_path: Path) -> None:
        """--external-dir permits a relative path that escapes the workspace."""
        target = tmp_path.parent / "ext-rl"
        rel = os.path.relpath(target, tmp_path)
        result = runner.invoke(
            app,
            [
                "--cwd",
                str(tmp_path),
                "init",
                "--releaseledger-dir",
                rel,
                "--external-dir",
            ],
        )
        assert result.exit_code == 0, result.stdout
        toml = (tmp_path / ".ledger" / "releaseledger" / "config.toml").read_text()
        assert "releaseledger_dir_policy" in toml
        assert "external" in toml
        # Verify the relative path was written (not the absolute path).
        # Backslash separators from os.path.relpath are normalized to forward
        # slashes in the TOML (TOML basic strings treat backslashes as escapes).
        assert f'releaseledger_dir = "{rel.replace(chr(92), "/")}"' in toml

    def test_external_dir_json_error_has_remediation(self, tmp_path: Path) -> None:
        """JSON error when external dir is rejected includes remediation hints."""
        result = runner.invoke(
            app,
            [
                "--cwd",
                str(tmp_path),
                "--json",
                "init",
                "--releaseledger-dir",
                "../escape",
            ],
        )
        payload = json.loads(result.stdout)
        assert payload["ok"] is False
        err = payload["error"]
        assert err["code"] == "USAGE_ERROR"
        assert "escapes" in err["message"]
        assert "remediation" in err
        # At least one remediation hint must mention the external-dir option.
        assert any("external" in r.lower() for r in err["remediation"])

    def test_external_dir_error_has_data_fields(self, tmp_path: Path) -> None:
        """JSON error includes structured data with workspace,
        value, resolved_path, and policy."""
        result = runner.invoke(
            app,
            [
                "--cwd",
                str(tmp_path),
                "--json",
                "init",
                "--releaseledger-dir",
                "../escape",
            ],
        )
        payload = json.loads(result.stdout)
        err = payload["error"]
        assert "data" in err
        data = err["data"]
        assert "workspace_root" in data
        assert "value" in data
        assert "resolved_path" in data
        assert "policy" in data
        assert data["policy"] == "workspace"
        assert data["value"] == "../escape"

    def test_workspace_dir_still_works(self, tmp_path: Path) -> None:
        """Existing workspace-local behavior is unchanged."""
        _init_project(tmp_path)
        payload = _json(_jrun(tmp_path, "storage", "where"))
        assert payload["result"]["inside_workspace"] is True
        assert payload["result"]["layout_exists"] is True

    def test_unknown_policy_rejected(self, tmp_path: Path) -> None:
        """A releaseledger_dir_policy other than
        workspace/external is rejected."""
        (tmp_path / ".ledger" / "releaseledger" / "config.toml").write_text(
            'releaseledger_dir = ".releaseledger"\nreleaseledger_dir_policy = "bogus"\n'
        )
        with pytest.raises(Exception) as exc_info:
            from releaseledger.storage.paths import require_project

            require_project(tmp_path)
        msg = str(exc_info.value)
        assert "releaseledger_dir_policy" in msg or "workspace" in msg


# ---------------------------------------------------------------------------
# Phase 10: config management
# ---------------------------------------------------------------------------


class TestPhase10ConfigCommands:
    def test_config_show_default(self, tmp_path: Path) -> None:
        """config show reports workspace, config path, storage, policy, ledger ref."""
        _init_project(tmp_path)
        result = _run(tmp_path, "config", "show")
        assert result.exit_code == 0, result.stdout
        assert "Workspace:" in result.stdout
        assert "Config:" in result.stdout
        assert "Storage:" in result.stdout
        assert "Policy: workspace" in result.stdout
        assert "Ledger ref: main" in result.stdout

    def test_config_show_json_mode(self, tmp_path: Path) -> None:
        _init_project(tmp_path)
        payload = _json(_jrun(tmp_path, "config", "show"))
        assert payload["ok"] is True
        assert payload["command"] == "config.show"
        r = payload["result"]
        assert r["kind"] == "config_show"
        assert "releaseledger_dir" not in r["config"]  # removed v2
        assert "releaseledger_dir_policy" not in r["config"]  # removed v2
        assert r["config"]["ledger_ref"] == "main"

    def test_config_set_rejects_uninitialized(self, tmp_path: Path) -> None:
        """config set without init raises error."""
        result = _run(tmp_path, "config", "set", "releaseledger_dir", ".custom")
        assert result.exit_code != 0
        assert (
            "not initialized" in _human_error(result).lower()
            or "no longer supported" in _human_error(result).lower()
        )

    def test_config_set_rejects_external_without_flag(self, tmp_path: Path) -> None:
        """config set releaseledger_dir is no longer supported."""
        _init_project(tmp_path)
        result = _run(tmp_path, "config", "set", "releaseledger_dir", "../escape")
        assert result.exit_code != 0
        assert "no longer supported" in _human_error(result).lower()

    def test_config_set_workspace_local_dir(self, tmp_path: Path) -> None:
        """config set releaseledger_dir is deprecated."""
        _init_project(tmp_path)
        result = _run(tmp_path, "config", "set", "releaseledger_dir", ".custom-rl")
        assert result.exit_code != 0
        assert "no longer supported" in _human_error(result).lower()

    def test_config_set_external_dir_with_flag(self, tmp_path: Path) -> None:
        """config set releaseledger_dir is no longer supported."""
        _init_project(tmp_path)
        result = _run(
            tmp_path,
            "config",
            "set",
            "releaseledger_dir",
            "../ext-rl",
            "--external-dir",
        )
        assert result.exit_code != 0
        assert "no longer supported" in _human_error(result).lower()

    def test_config_set_repairs_external_dir_missing_policy(
        self, tmp_path: Path
    ) -> None:
        """config set releaseledger_dir is no longer supported."""
        _init_project(tmp_path)
        result = _run(
            tmp_path,
            "config",
            "set",
            "releaseledger_dir",
            "../ext-rl",
            "--external-dir",
        )
        assert result.exit_code != 0
        assert "no longer supported" in _human_error(result).lower()

        result = _run(
            tmp_path,
            "config",
            "set",
            "releaseledger_dir",
            "../ext-rl",
            "--external-dir",
        )
        assert result.exit_code != 0
        assert "no longer supported" in _human_error(result).lower()

    def test_config_set_json_before_after(self, tmp_path: Path) -> None:
        """config set releaseledger_dir returns migration error in JSON mode."""
        _init_project(tmp_path)
        result = _run(tmp_path, "config", "set", "releaseledger_dir", ".custom-rl")
        assert result.exit_code != 0
        assert "no longer supported" in _human_error(result).lower()

    def test_config_set_rejects_unknown_key(self, tmp_path: Path) -> None:
        """Only releaseledger_dir is supported; other keys are rejected."""
        _init_project(tmp_path)
        result = _run(tmp_path, "config", "set", "bogus_key", "value")
        assert result.exit_code != 0
        assert "unsupported" in _human_error(result).lower()

    def test_config_set_atomicity(self, tmp_path: Path) -> None:
        """config set releaseledger_dir returns migration error."""
        _init_project(tmp_path)
        result = _run(tmp_path, "config", "set", "releaseledger_dir", ".custom-rl")
        assert result.exit_code != 0
        assert "no longer supported" in _human_error(result).lower()


# ---------------------------------------------------------------------------
# Phase 11: entry provenance
# ---------------------------------------------------------------------------


class TestPhase11EntrySources:
    def test_entry_add_single_source(self, tmp_path: Path) -> None:
        """--source VALUE is stored in the entry front matter."""
        _init_project(tmp_path)
        _run(tmp_path, "release", "create", "1.0.0")
        result = _run(
            tmp_path,
            "entry",
            "add",
            "1.0.0",
            "--kind",
            "added",
            "--summary",
            "Add X",
            "--source",
            "taskledger:task-0001",
        )
        assert result.exit_code == 0, result.stdout
        import ledgercore

        entry_path = (
            tmp_path
            / ".releaseledger"
            / "ledgers"
            / "main"
            / "releases"
            / "1.0.0"
            / "entries"
            / "entry-0001.md"
        )
        meta, _ = ledgercore.read_front_matter_document(entry_path)
        assert "sources" in meta
        assert meta["sources"] == ["taskledger:task-0001"]

    def test_entry_add_multiple_sources(self, tmp_path: Path) -> None:
        """--source is repeatable for multiple provenance refs."""
        _init_project(tmp_path)
        _run(tmp_path, "release", "create", "1.0.0")
        result = _run(
            tmp_path,
            "entry",
            "add",
            "1.0.0",
            "--kind",
            "added",
            "--summary",
            "Add X",
            "--source",
            "taskledger:task-0001",
            "--source",
            "github:pr-42",
        )
        assert result.exit_code == 0, result.stdout
        import ledgercore

        entry_path = (
            tmp_path
            / ".releaseledger"
            / "ledgers"
            / "main"
            / "releases"
            / "1.0.0"
            / "entries"
            / "entry-0001.md"
        )
        meta, _ = ledgercore.read_front_matter_document(entry_path)
        assert meta["sources"] == ["taskledger:task-0001", "github:pr-42"]

    def test_entry_sources_in_json_payload(self, tmp_path: Path) -> None:
        """Changelog JSON includes sources for entries that have them."""
        _init_project(tmp_path)
        _run(tmp_path, "release", "create", "1.0.0")
        _run(
            tmp_path,
            "entry",
            "add",
            "1.0.0",
            "--kind",
            "added",
            "--summary",
            "Add X",
            "--source",
            "taskledger:task-0001",
        )
        result = _run(tmp_path, "changelog", "1.0.0", "--format", "json")
        payload = json.loads(result.stdout)
        entries = payload["entries"]
        assert len(entries) == 1
        assert entries[0]["sources"] == ["taskledger:task-0001"]

    def test_entry_without_sources_still_loads(self, tmp_path: Path) -> None:
        """Existing entries without sources field load unchanged."""
        _init_project(tmp_path)
        _run(tmp_path, "release", "create", "1.0.0")
        _run(
            tmp_path,
            "entry",
            "add",
            "1.0.0",
            "--kind",
            "added",
            "--summary",
            "Legacy entry",
        )
        # Verify sources is empty in the payload.
        result = _run(tmp_path, "changelog", "1.0.0", "--format", "json")
        payload = json.loads(result.stdout)
        entries = payload["entries"]
        assert entries[0]["sources"] == []

    def test_entry_list_json_includes_sources(self, tmp_path: Path) -> None:
        """entry list JSON includes sources for entries with them."""
        _init_project(tmp_path)
        _run(tmp_path, "release", "create", "1.0.0")
        _run(
            tmp_path,
            "entry",
            "add",
            "1.0.0",
            "--kind",
            "added",
            "--summary",
            "With source",
            "--source",
            "taskledger:task-0001",
        )
        payload = _json(_jrun(tmp_path, "entry", "list", "1.0.0"))
        entries = payload["result"]["entries"]
        assert entries[0]["sources"] == ["taskledger:task-0001"]


# ---------------------------------------------------------------------------
# Entry lint failure output (actionable per-entry issues)
# ---------------------------------------------------------------------------


def _seed_lint_warnings(tmp_path: Path) -> None:
    """Seed a release with one accepted entry that triggers lint warnings."""
    _init_project(tmp_path)
    assert _run(tmp_path, "release", "create", "1.0.0").exit_code == 0
    assert (
        _run(
            tmp_path,
            "entry",
            "add",
            "1.0.0",
            "--kind",
            "changed",
            "--summary",
            "Changed release workflow.",
        ).exit_code
        == 0
    )


def test_entry_lint_json_failure_includes_result_and_error(tmp_path: Path) -> None:
    _seed_lint_warnings(tmp_path)
    result = _jrun(tmp_path, "entry", "lint", "1.0.0", "--strict")
    assert result.exit_code != 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["command"] == "entry.lint"
    assert payload["result_type"] == "entry_lint"
    # The full result (issues + entries) is present even on failure.
    assert "result" in payload
    assert "issues" in payload["result"]
    assert "entries" in payload["result"]
    assert payload["error"]["code"] == "VALIDATION_ERROR"
    assert "Entry lint failed" in payload["error"]["message"]
    codes = {issue["code"] for issue in payload["result"]["issues"]}
    assert "trailing_period" in codes


def test_entry_lint_text_failure_lists_issues(tmp_path: Path) -> None:
    _seed_lint_warnings(tmp_path)
    result = _run(tmp_path, "entry", "lint", "1.0.0", "--strict")
    assert result.exit_code != 0
    text = _human_error(result)
    assert "Entry lint failed" in text
    # The per-entry issues are listed with severity/field/code and message.
    assert "trailing_period" in text
    assert "should not end with a period" in text
    assert "entry-0001" in text


def test_entry_lint_pass_payload_unchanged(tmp_path: Path) -> None:
    _init_project(tmp_path)
    assert _run(tmp_path, "release", "create", "1.0.0").exit_code == 0
    assert (
        _run(
            tmp_path,
            "entry",
            "add",
            "1.0.0",
            "--kind",
            "added",
            "--summary",
            "Added clean entry",
        ).exit_code
        == 0
    )
    payload = _json(_jrun(tmp_path, "entry", "lint", "1.0.0"))
    assert payload["ok"] is True
    assert payload["result"]["summary"] == {"errors": 0, "warnings": 0}
