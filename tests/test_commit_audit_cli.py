"""CLI tests for the commit audit sheet (``releaseledger audit ...``).

These exercise the git-backed ``audit init`` path and the show/update/validate/
sync commands against a real fixture repository, mirroring
``tests/test_git_review.py``.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import yaml
from typer.testing import CliRunner

from releaseledger.cli import app

runner = CliRunner()


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


def _run(repo: Path, *cmd: str):
    return runner.invoke(app, ["--cwd", str(repo), *cmd])


def _jrun(repo: Path, *cmd: str) -> dict:
    result = runner.invoke(app, ["--cwd", str(repo), "--json", *cmd])
    assert result.exit_code == 0, f"exit={result.exit_code}: {result.output}"
    return json.loads(result.output)


def _human_error(result) -> str:
    try:
        stderr = result.stderr or ""
    except ValueError:
        stderr = ""
    return stderr + (result.stdout or getattr(result, "output", "") or "")


def _seed_range(tmp_path: Path) -> tuple[Path, str, str]:
    """Create a repo with v0.1.0..v0.2.0 range and a seeded releaseledger project."""
    repo = _init_repo(tmp_path)
    _commit(repo, "root", "README.md")
    _git(repo, "tag", "v0.1.0")
    sha_a = _commit(repo, "feat: add a", "a.txt")
    sha_b = _commit(repo, "fix: handle b", "b.txt")
    _git(repo, "tag", "v0.2.0")
    assert _run(repo, "init").exit_code == 0
    assert (
        _run(
            repo,
            "release",
            "create",
            "0.2.0",
            "--previous",
            "0.1.0",
            "--released-at",
            "2026-06-14",
        ).exit_code
        == 0
    )
    assert (
        _run(
            repo,
            "release",
            "update",
            "0.2.0",
            "--git-base",
            "v0.1.0",
            "--git-head",
            "v0.2.0",
        ).exit_code
        == 0
    )
    return repo, sha_a, sha_b


class TestAuditInit:
    def test_init_creates_one_row_per_commit(self, tmp_path: Path) -> None:
        repo, sha_a, sha_b = _seed_range(tmp_path)
        payload = _jrun(repo, "audit", "init", "0.2.0")
        assert payload["result_type"] == "commit_audit_sheet_created"
        assert int(payload["result"]["row_count"]) == 2

    def test_init_uses_stored_release_range_when_omitted(self, tmp_path: Path) -> None:
        repo, _sha_a, _sha_b = _seed_range(tmp_path)
        payload = _jrun(repo, "audit", "init", "0.2.0")
        assert payload["result"]["git_range"]
        assert int(payload["result"]["row_count"]) == 2

    def test_init_refuses_overwrite_without_flag(self, tmp_path: Path) -> None:
        repo, _sha_a, _sha_b = _seed_range(tmp_path)
        assert _run(repo, "audit", "init", "0.2.0").exit_code == 0
        again = _run(repo, "audit", "init", "0.2.0")
        assert again.exit_code != 0
        assert "exists" in _human_error(again).lower()
        ok = _run(repo, "audit", "init", "0.2.0", "--overwrite")
        assert ok.exit_code == 0

    def test_init_uses_stored_head_sha_when_head_moves(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _commit(repo, "root", "README.md")
        _git(repo, "tag", "v0.1.0")
        sha_a = _commit(repo, "feat: add a", "a.txt")
        sha_b = _commit(repo, "fix: handle b", "b.txt")
        assert _run(repo, "init").exit_code == 0
        assert (
            _run(
                repo,
                "release",
                "create",
                "0.2.0",
                "--previous",
                "0.1.0",
                "--released-at",
                "2026-06-14",
            ).exit_code
            == 0
        )
        assert (
            _run(
                repo,
                "release",
                "update",
                "0.2.0",
                "--git-base",
                "v0.1.0",
                "--git-head",
                "HEAD",
            ).exit_code
            == 0
        )
        extra_sha = _commit(repo, "feat: add c", "c.txt")
        payload = _jrun(repo, "audit", "init", "0.2.0")
        assert int(payload["result"]["row_count"]) == 2
        assert payload["result"]["git_head_ref"] == "HEAD"
        show = _jrun(repo, "audit", "show", "0.2.0", "--format", "json")
        rows = show["result"]["sheet"]["rows"]
        refs = {row["source_ref"] for row in rows}
        assert f"git:{sha_a}" in refs
        assert f"git:{sha_b}" in refs
        assert f"git:{extra_sha}" not in refs

    def test_overwrite_after_update_advances_revision(self, tmp_path: Path) -> None:
        repo, _sha_a, _sha_b = _seed_range(tmp_path)
        assert _run(repo, "audit", "init", "0.2.0").exit_code == 0
        sheet_path = tmp_path / "edited.yaml"
        export = _jrun(repo, "audit", "show", "0.2.0", "--format", "json")
        data = export["result"]["sheet"]
        data["rows"][0]["inspected"] = True
        data["rows"][0]["inspected_paths"] = ["src/example.py"]
        data["rows"][0]["decision"] = "accepted"
        data["rows"][0]["observed_behavior"] = "Reviewed behavior."
        sheet_path.write_text(yaml.safe_dump(data))
        assert (
            _run(repo, "audit", "update", "0.2.0", "--file", str(sheet_path)).exit_code
            == 0
        )
        payload = _jrun(repo, "audit", "init", "0.2.0", "--overwrite")
        assert int(payload["result"]["revision"]) == 3


class TestAuditShow:
    def test_show_markdown_renders_worksheet(self, tmp_path: Path) -> None:
        repo, _sha_a, _sha_b = _seed_range(tmp_path)
        assert _run(repo, "audit", "init", "0.2.0").exit_code == 0
        result = _run(repo, "audit", "show", "0.2.0", "--format", "markdown")
        assert result.exit_code == 0, _human_error(result)
        out = result.stdout
        assert "Commit audit sheet" in out
        assert "| sha |" in out
        assert "| decision |" in out

    def test_show_json(self, tmp_path: Path) -> None:
        repo, _sha_a, _sha_b = _seed_range(tmp_path)
        assert _run(repo, "audit", "init", "0.2.0").exit_code == 0
        payload = _jrun(repo, "audit", "show", "0.2.0", "--format", "json")
        assert payload["result_type"] == "commit_audit_sheet"
        assert int(payload["result"]["sheet"]["commit_count"]) == 2

    def test_global_json_show_defaults_to_sheet_payload(self, tmp_path: Path) -> None:
        repo, _sha_a, _sha_b = _seed_range(tmp_path)
        assert _run(repo, "audit", "init", "0.2.0").exit_code == 0
        payload = _jrun(repo, "audit", "show", "0.2.0")
        assert payload["result"]["format"] == "json"
        assert "sheet" in payload["result"]
        assert "document" not in payload["result"]

    def test_show_writes_output_file(self, tmp_path: Path) -> None:
        repo, _sha_a, _sha_b = _seed_range(tmp_path)
        assert _run(repo, "audit", "init", "0.2.0").exit_code == 0
        out = tmp_path / "sheet.md"
        result = _run(
            repo, "audit", "show", "0.2.0", "--format", "markdown", "--output", str(out)
        )
        assert result.exit_code == 0, _human_error(result)
        assert out.is_file()
        assert "Commit audit sheet" in out.read_text()


class TestAuditUpdate:
    def test_update_validates_decision_enum(self, tmp_path: Path) -> None:
        repo, sha_a, _sha_b = _seed_range(tmp_path)
        assert _run(repo, "audit", "init", "0.2.0").exit_code == 0
        # Export, edit with a bad decision, re-import.
        sheet_path = tmp_path / "edited.yaml"
        export = _jrun(repo, "audit", "show", "0.2.0", "--format", "json")
        data = export["result"]["sheet"]
        data["rows"][0]["decision"] = "bogus"
        sheet_path.write_text(yaml.safe_dump(data))
        result = _run(repo, "audit", "update", "0.2.0", "--file", str(sheet_path))
        assert result.exit_code != 0
        assert "decision" in _human_error(result).lower()

    def test_update_rejects_missing_rows(self, tmp_path: Path) -> None:
        repo, sha_a, sha_b = _seed_range(tmp_path)
        assert _run(repo, "audit", "init", "0.2.0").exit_code == 0
        sheet_path = tmp_path / "edited.yaml"
        export = _jrun(repo, "audit", "show", "0.2.0", "--format", "json")
        data = export["result"]["sheet"]
        # Drop one of the two rows.
        data["rows"] = [r for r in data["rows"] if r["sha"] == sha_a]
        data["commit_count"] = 1
        sheet_path.write_text(yaml.safe_dump(data))
        result = _run(repo, "audit", "update", "0.2.0", "--file", str(sheet_path))
        assert result.exit_code != 0
        assert "missing" in _human_error(result).lower()

    def test_update_malformed_yaml_returns_validation_error(
        self, tmp_path: Path
    ) -> None:
        repo, _sha_a, _sha_b = _seed_range(tmp_path)
        assert _run(repo, "audit", "init", "0.2.0").exit_code == 0
        bad = tmp_path / "bad.yaml"
        bad.write_text("rows:\n  - sha: abcdef:\n")
        result = _run(repo, "audit", "update", "0.2.0", "--file", str(bad))
        assert result.exit_code != 0
        text = _human_error(result)
        assert "traceback" not in text.lower()
        assert "line" in text.lower() or "column" in text.lower()


class TestAuditValidate:
    @staticmethod
    def _seed_sheet(
        repo: Path,
        sha_a: str,
        sha_b: str,
        *,
        entries: list[dict] | None = None,
    ) -> None:
        assert _run(repo, "audit", "init", "0.2.0").exit_code == 0
        # Edit the sheet so every row is inspected + decided.
        sheet_path = repo / "edited.yaml"
        export = _jrun(repo, "audit", "show", "0.2.0", "--format", "json")
        data = export["result"]["sheet"]
        for row in data["rows"]:
            row["inspected"] = True
            row["inspected_paths"] = ["src/example.py"]
            row["decision"] = "accepted"
            row["observed_behavior"] = "Reviewed behavior written by the reviewer."
            if not row.get("evidence_subject"):
                row["evidence_subject"] = "internal: scaffold"
        sheet_path.write_text(yaml.safe_dump(data))
        assert (
            _run(repo, "audit", "update", "0.2.0", "--file", str(sheet_path)).exit_code
            == 0
        )
        if entries:
            batch = {"entries": entries}
            (repo / "entries.yaml").write_text(yaml.safe_dump(batch))
            assert (
                _run(
                    repo,
                    "entry",
                    "add-many",
                    "0.2.0",
                    "--file",
                    str(repo / "entries.yaml"),
                ).exit_code
                == 0
            )

    def test_strict_fails_uninspected(self, tmp_path: Path) -> None:
        repo, sha_a, sha_b = _seed_range(tmp_path)
        # init leaves rows uninspected by default.
        assert _run(repo, "audit", "init", "0.2.0").exit_code == 0
        result = _run(repo, "audit", "validate", "0.2.0", "--strict")
        assert result.exit_code != 0

    def test_strict_fails_needs_review(self, tmp_path: Path) -> None:
        repo, sha_a, sha_b = _seed_range(tmp_path)
        assert _run(repo, "audit", "init", "0.2.0").exit_code == 0
        # Mark rows inspected but leave decision=needs_review.
        sheet_path = repo / "edited.yaml"
        export = _jrun(repo, "audit", "show", "0.2.0", "--format", "json")
        data = export["result"]["sheet"]
        for row in data["rows"]:
            row["inspected"] = True
        sheet_path.write_text(yaml.safe_dump(data))
        assert (
            _run(repo, "audit", "update", "0.2.0", "--file", str(sheet_path)).exit_code
            == 0
        )
        result = _run(repo, "audit", "validate", "0.2.0", "--strict")
        assert result.exit_code != 0

    def test_strict_fails_when_accepted_lacks_coverage(self, tmp_path: Path) -> None:
        repo, sha_a, sha_b = _seed_range(tmp_path)
        self._seed_sheet(repo, sha_a, sha_b, entries=[])
        result = _run(repo, "audit", "validate", "0.2.0", "--strict")
        assert result.exit_code != 0
        assert "coverage" in _human_error(result).lower()

    def test_strict_passes_when_covered(self, tmp_path: Path) -> None:
        repo, sha_a, sha_b = _seed_range(tmp_path)
        self._seed_sheet(
            repo,
            sha_a,
            sha_b,
            entries=[
                {
                    "kind": "added",
                    "summary": "Added features A and B from reviewed behavior",
                    "source_refs": [f"git:{sha_a}", f"git:{sha_b}"],
                    "status": "accepted",
                }
            ],
        )
        result = _run(repo, "audit", "validate", "0.2.0", "--strict")
        assert result.exit_code == 0, _human_error(result)

    def test_evidence_phase_passes_before_entries(self, tmp_path: Path) -> None:
        repo, sha_a, sha_b = _seed_range(tmp_path)
        self._seed_sheet(repo, sha_a, sha_b, entries=[])
        result = _run(
            repo,
            "audit",
            "validate",
            "0.2.0",
            "--phase",
            "evidence",
            "--strict",
        )
        assert result.exit_code == 0, _human_error(result)

    def test_complete_phase_fails_before_entries(self, tmp_path: Path) -> None:
        repo, sha_a, sha_b = _seed_range(tmp_path)
        self._seed_sheet(repo, sha_a, sha_b, entries=[])
        result = _run(
            repo,
            "audit",
            "validate",
            "0.2.0",
            "--phase",
            "complete",
            "--strict",
        )
        assert result.exit_code != 0
        assert "coverage" in _human_error(result).lower()


class TestAuditSync:
    def test_sync_fills_target_entry_id(self, tmp_path: Path) -> None:
        repo, sha_a, sha_b = _seed_range(tmp_path)
        assert _run(repo, "audit", "init", "0.2.0").exit_code == 0
        batch = {
            "entries": [
                {
                    "kind": "added",
                    "summary": "Added features A and B from reviewed behavior",
                    "source_refs": [f"git:{sha_a}", f"git:{sha_b}"],
                    "status": "accepted",
                }
            ]
        }
        (repo / "entries.yaml").write_text(yaml.safe_dump(batch))
        assert (
            _run(
                repo, "entry", "add-many", "0.2.0", "--file", str(repo / "entries.yaml")
            ).exit_code
            == 0
        )
        payload = _jrun(repo, "audit", "sync", "0.2.0")
        assert payload["result_type"] == "commit_audit_sync"
        assert int(payload["result"]["updated_rows"]) == 2
        show = _jrun(repo, "audit", "show", "0.2.0", "--format", "json")
        targets = {r.get("target_entry_id") for r in show["result"]["sheet"]["rows"]}
        assert targets == {"entry-0001"}


class TestAuditApply:
    def test_apply_merges_row_annotations(self, tmp_path: Path) -> None:
        repo, sha_a, _sha_b = _seed_range(tmp_path)
        assert _run(repo, "audit", "init", "0.2.0").exit_code == 0
        decisions = {
            "rows": [
                {
                    "sha": sha_a,
                    "inspected": True,
                    "inspected_paths": ["a.txt"],
                    "observed_behavior": "Reviewed behavior for commit A.",
                    "public_impact": "public",
                    "decision": "accepted",
                    "target_entry_key": "entry-a",
                }
            ]
        }
        decisions_path = tmp_path / "audit-decisions.yaml"
        decisions_path.write_text(yaml.safe_dump(decisions))
        preview = _jrun(
            repo,
            "audit",
            "apply",
            "0.2.0",
            "--file",
            str(decisions_path),
            "--dry-run",
        )
        assert preview["result"]["written"] is False
        applied = _jrun(
            repo,
            "audit",
            "apply",
            "0.2.0",
            "--file",
            str(decisions_path),
        )
        assert applied["result"]["written"] is True
        show = _jrun(repo, "audit", "show", "0.2.0", "--format", "json")
        row = next(r for r in show["result"]["sheet"]["rows"] if r["sha"] == sha_a)
        assert row["inspected"] is True
        assert row["decision"] == "accepted"
        assert row["target_entry_key"] == "entry-a"


class TestAuditRefresh:
    def test_refresh_preserves_reviewed_rows_and_appends_new_commit(
        self, tmp_path: Path
    ) -> None:
        repo, sha_a, sha_b = _seed_range(tmp_path)
        assert _run(repo, "audit", "init", "0.2.0").exit_code == 0
        decisions = {
            "rows": [
                {
                    "sha": sha_a,
                    "inspected": True,
                    "inspected_paths": ["a.txt"],
                    "observed_behavior": "Reviewed behavior for commit A.",
                    "public_impact": "public",
                    "decision": "accepted",
                    "target_entry_key": "entry-a",
                }
            ]
        }
        decisions_path = tmp_path / "audit-decisions.yaml"
        decisions_path.write_text(yaml.safe_dump(decisions))
        assert (
            _run(
                repo, "audit", "apply", "0.2.0", "--file", str(decisions_path)
            ).exit_code
            == 0
        )
        _commit(repo, "feat: add c", "c.txt")
        refreshed = _jrun(repo, "audit", "refresh", "0.2.0", "--head", "HEAD")
        assert refreshed["result"]["preserved_reviewed_rows"] == 1
        assert refreshed["result"]["new_rows"] == 1
        show = _jrun(repo, "audit", "show", "0.2.0", "--format", "json")
        rows = show["result"]["sheet"]["rows"]
        assert len(rows) == 3
        row = next(r for r in rows if r["sha"] == sha_a)
        assert row["decision"] == "accepted"
        assert row["target_entry_key"] == "entry-a"


class TestEntryGuardCommitSubjects:
    def test_guard_rejects_summary_matching_subject(self, tmp_path: Path) -> None:
        repo, sha_a, sha_b = _seed_range(tmp_path)
        assert _run(repo, "audit", "init", "0.2.0").exit_code == 0
        # Entry summary copies a commit subject verbatim.
        batch = {
            "entries": [
                {
                    "kind": "added",
                    "summary": "feat: add a",  # == commit subject
                    "source_refs": [f"git:{sha_a}"],
                    "status": "accepted",
                }
            ]
        }
        (repo / "entries.yaml").write_text(yaml.safe_dump(batch))
        result = _run(
            repo,
            "entry",
            "add-many",
            "0.2.0",
            "--file",
            str(repo / "entries.yaml"),
            "--guard-commit-subjects",
        )
        assert result.exit_code != 0
        assert "commit subjects" in _human_error(result).lower()

    def test_guard_allows_distinct_summary(self, tmp_path: Path) -> None:
        repo, sha_a, sha_b = _seed_range(tmp_path)
        assert _run(repo, "audit", "init", "0.2.0").exit_code == 0
        batch = {
            "entries": [
                {
                    "kind": "added",
                    "summary": "Added the A feature from reviewed diff evidence",
                    "source_refs": [f"git:{sha_a}"],
                    "status": "accepted",
                }
            ]
        }
        (repo / "entries.yaml").write_text(yaml.safe_dump(batch))
        result = _run(
            repo,
            "entry",
            "add-many",
            "0.2.0",
            "--file",
            str(repo / "entries.yaml"),
            "--guard-commit-subjects",
        )
        assert result.exit_code == 0, _human_error(result)


class TestEntryBatchValidation:
    def test_strict_blocks_warning_batch_before_write(self, tmp_path: Path) -> None:
        repo, sha_a, _sha_b = _seed_range(tmp_path)
        batch = {
            "entries": [
                {
                    "kind": "added",
                    "summary": "Added a reviewed change.",
                    "source_refs": [f"git:{sha_a}"],
                    "status": "accepted",
                }
            ]
        }
        entries_path = repo / "entries.yaml"
        entries_path.write_text(yaml.safe_dump(batch))
        result = _run(
            repo,
            "entry",
            "add-many",
            "0.2.0",
            "--file",
            str(entries_path),
            "--strict",
        )
        assert result.exit_code != 0
        listed = _jrun(repo, "entry", "list", "0.2.0")
        assert listed["result"]["entries"] == []

    def test_sync_audit_updates_targets_in_same_batch(self, tmp_path: Path) -> None:
        repo, sha_a, sha_b = _seed_range(tmp_path)
        assert _run(repo, "audit", "init", "0.2.0").exit_code == 0
        batch = {
            "entries": [
                {
                    "kind": "added",
                    "summary": "Added features A and B from reviewed behavior",
                    "source_refs": [f"git:{sha_a}", f"git:{sha_b}"],
                    "status": "accepted",
                }
            ]
        }
        entries_path = repo / "entries.yaml"
        entries_path.write_text(yaml.safe_dump(batch))
        payload = _jrun(
            repo,
            "entry",
            "add-many",
            "0.2.0",
            "--file",
            str(entries_path),
            "--sync-audit",
        )
        assert payload["result"]["audit_sync"]["updated_rows"] == 2
        show = _jrun(repo, "audit", "show", "0.2.0", "--format", "json")
        targets = {r.get("target_entry_id") for r in show["result"]["sheet"]["rows"]}
        assert targets == {"entry-0001"}


class TestGitImportWarning:
    def test_import_output_mentions_audit_init(self, tmp_path: Path) -> None:
        repo, _sha_a, _sha_b = _seed_range(tmp_path)
        out = tmp_path / "entries.yaml"
        result = _run(
            repo,
            "git",
            "import",
            "0.2.0",
            "--output",
            str(out),
        )
        assert result.exit_code == 0, _human_error(result)
        assert "audit init" in result.stdout
        assert "scaffold" in result.stdout.lower()
