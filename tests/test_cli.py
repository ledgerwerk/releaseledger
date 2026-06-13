"""Releaseledger CLI tests.

Uses ``typer.testing.CliRunner`` with isolated filesystem (``tmp_path``) per the
brief's test plan. JSON helpers parse the deterministic success/error envelopes.
"""

from __future__ import annotations

import json
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
    """Return the stderr/stdout text for a non-zero human-mode result."""
    return (result.stderr or "") + (result.stdout or "")


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
        assert (tmp_path / ".releaseledger.toml").is_file()
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
        text = (tmp_path / ".releaseledger.toml").read_text()
        assert "releaseledger_dir = \".releaseledger\"" in text
        assert "config_version = 1" in text
        assert "[ledger]" in text
        assert "[release]" in text

    def test_init_human_output_mentions_config_and_dir(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["--cwd", str(tmp_path), "init"])
        assert result.exit_code == 0, result.stdout
        assert "initialized releaseledger in .releaseledger" in result.stdout
        assert "wrote .releaseledger.toml" in result.stdout

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
        text = (tmp_path / ".releaseledger.toml").read_text()
        assert "releaseledger_dir = \".custom-rl\"" in text

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
        (tmp_path / ".releaseledger.toml").write_text(
            "bogus_key = true\nreleaseledger_dir = \".releaseledger\"\n"
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
        payload = _json(
            runner.invoke(app, ["--cwd", str(tmp_path), "--json", "init"])
        )
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
            tmp_path / ".releaseledger" / "ledgers" / "main"
            / "releases" / "1.2.0" / "release.md"
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
            tmp_path, "release", "create", "1.2.0",
            "--status", "planned", "--title", "T",
        )
        result = _run(tmp_path, "release", "show", "1.2.0")
        assert result.exit_code == 0, result.stdout
        assert "version: 1.2.0" in result.stdout
        assert "status: planned" in result.stdout

    def test_release_show_not_found(self, tmp_path: Path) -> None:
        _init_project(tmp_path)
        result = _run(tmp_path, "release", "show", "9.9.9")
        assert result.exit_code != 0
        assert "not found" in _human_error(result).lower()

    def test_release_finalize_transitions_to_released(self, tmp_path: Path) -> None:
        _init_project(tmp_path)
        _run(tmp_path, "release", "create", "1.2.0")
        result = _run(
            tmp_path, "release", "finalize", "1.2.0", "--released-at", "2026-06-13",
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
            tmp_path, "entry", "add", "1.2.0",
            "--kind", "added",
            "--summary", "Add release bundle storage",
            "--path", "releaseledger/storage/store.py",
        )
        assert result.exit_code == 0, result.stdout
        assert "added entry entry-0001 to release 1.2.0" in result.stdout
        entry_path = (
            tmp_path / ".releaseledger" / "ledgers" / "main"
            / "releases" / "1.2.0" / "entries" / "entry-0001.md"
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
            tmp_path, "entry", "add", "1.2.0",
            "--kind", "added", "--summary", "x", "--path", "../escape.py",
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
            tmp_path, "entry", "add", "1.2.0", "--kind", "added",
            "--summary", "Added X",
        )
        result = _run(tmp_path, "entry", "list", "1.2.0")
        assert "ENTRIES" in result.stdout
        assert "entry-0001" in result.stdout
        assert "Added X" in result.stdout

    def test_entry_internal_flag_persisted(self, tmp_path: Path) -> None:
        _init_project(tmp_path)
        _run(tmp_path, "release", "create", "1.2.0")
        _run(
            tmp_path, "entry", "add", "1.2.0",
            "--kind", "internal", "--summary", "Refactor internals", "--internal",
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
                tmp_path, "entry", "add", "1.2.0", "--kind", "added",
                "--summary", "Add release bundle storage",
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
            tmp_path, "entry", "add", "1.2.0", "--kind", "added",
            "--summary", "Add release bundle storage",
            "--path", "releaseledger/storage/store.py",
        )
        _run(
            tmp_path, "entry", "add", "1.2.0", "--kind", "fixed",
            "--summary", "Fix version filename validation",
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
            tmp_path, "changelog", "1.2.0",
            "--target-changelog", "CHANGELOG.md", "--release-date", "2026-06-13",
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
            tmp_path, "entry", "add", "1.2.0", "--kind", "added",
            "--summary", "visible",
        )
        _run(
            tmp_path, "entry", "add", "1.2.0",
            "--kind", "internal", "--summary", "hidden refactors", "--internal",
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
        result = _run(
            tmp_path, "changelog", "1.2.0", "--output", str(out_file)
        )
        assert result.exit_code == 0, result.stdout
        assert out_file.is_file()
        assert "# Changelog source" in out_file.read_text()
        assert "wrote" in result.stdout

    def test_output_json_file(self, tmp_path: Path) -> None:
        self._seed(tmp_path)
        out_file = tmp_path / "changes.json"
        result = _run(
            tmp_path, "changelog", "1.2.0", "--format", "json",
            "--output", str(out_file),
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
            tmp_path, "entry", "add", "1.2.0", "--kind", "added",
            "--summary", "Add release bundle storage",
            "--path", "releaseledger/storage/store.py",
        )
        _run(
            tmp_path, "entry", "add", "1.2.0", "--kind", "fixed",
            "--summary", "Fix version filename validation",
        )

    @staticmethod
    def _write_config(tmp_path: Path, *, body: str | None = None,
                     postprocessors: str | None = None) -> None:
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
        (tmp_path / ".releaseledger.toml").write_text(
            "\n".join(lines) + "\n"
        )

    def test_build_dry_run_renders_final_section(self, tmp_path: Path) -> None:
        self._seed(tmp_path)
        result = _run(
            tmp_path, "build", "1.2.0", "--dry-run",
            "--release-date", "2026-06-13",
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
            tmp_path, "build", "1.2.0",
            "--release-date", "2026-06-13", "--target-file", "CHANGELOG.md",
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

    def test_build_refuses_duplicate_without_replace(self, tmp_path: Path) -> None:
        self._seed(tmp_path)
        first = _run(
            tmp_path, "build", "1.2.0", "--release-date", "2026-06-13",
            "--target-file", "CHANGELOG.md",
        )
        assert first.exit_code == 0, _human_error(first)
        second = _run(
            tmp_path, "build", "1.2.0", "--release-date", "2026-06-13",
            "--target-file", "CHANGELOG.md",
        )
        assert second.exit_code != 0
        assert "section" in _human_error(second).lower()

    def test_build_replace_existing(self, tmp_path: Path) -> None:
        self._seed(tmp_path)
        _run(
            tmp_path, "build", "1.2.0", "--release-date", "2026-06-13",
            "--target-file", "CHANGELOG.md",
        )
        # Add another entry after the first build.
        _run(
            tmp_path, "entry", "add", "1.2.0", "--kind", "changed",
            "--summary", "Change rendering pipeline",
        )
        replaced = _run(
            tmp_path, "build", "1.2.0", "--release-date", "2026-06-13",
            "--target-file", "CHANGELOG.md", "--replace-existing",
        )
        assert replaced.exit_code == 0, _human_error(replaced)
        text = (tmp_path / "CHANGELOG.md").read_text()
        assert text.count("## [1.2.0] - 2026-06-13") == 1
        assert "Change rendering pipeline" in text

    def test_build_hides_internal_by_default(self, tmp_path: Path) -> None:
        _init_project(tmp_path)
        _run(tmp_path, "release", "create", "1.2.0")
        _run(
            tmp_path, "entry", "add", "1.2.0", "--kind", "added",
            "--summary", "visible feature",
        )
        _run(
            tmp_path, "entry", "add", "1.2.0", "--kind", "internal",
            "--summary", "secret refactor", "--internal",
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
            tmp_path, "entry", "add", "1.2.0", "--kind", "added",
            "--summary", "Add release bundle storage",
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
            postprocessors="[{ pattern = \"releaseledger\", "
                             "replace = \"Releaseledger\" }]"
        )
        _run(tmp_path, "release", "create", "1.2.0")
        _run(
            tmp_path, "entry", "add", "1.2.0", "--kind", "added",
            "--summary", "Add releaseledger build command",
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
        text = (tmp_path / ".releaseledger.toml").read_text()
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
            tmp_path / ".releaseledger" / "ledgers" / "main"
            / "events" / "events.jsonl"
        )

    @staticmethod
    def _index_path(tmp_path: Path, name: str) -> Path:
        return (
            tmp_path / ".releaseledger" / "ledgers" / "main"
            / "indexes" / name
        )

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict]:
        return [
            json.loads(ln)
            for ln in path.read_text().splitlines()
            if ln.strip()
        ]

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
            tmp_path, "entry", "add", "1.2.0", "--kind", "added",
            "--summary", "Add release bundle storage",
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
        _run(
            tmp_path, "entry", "add", "1.2.0", "--kind", "added", "--summary", "X"
        )
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
        _run(
            tmp_path, "entry", "add", "1.2.0", "--kind", "fixed", "--summary", "Y"
        )
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
            tmp_path, "entry", "add", "1.0.0", "--kind", "added",
            "--summary", "in 1.0.0",
        )
        _run(
            tmp_path, "entry", "add", "2.0.0", "--kind", "changed",
            "--summary", "in 2.0.0",
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
