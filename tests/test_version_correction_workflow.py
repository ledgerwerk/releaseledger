"""Regression characterization for the safe release-version correction flow."""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from releaseledger.cli import _render_release_check_human, app
from releaseledger.domain.audit import CommitAuditRow, CommitAuditSheetRecord, initial_versioning
from releaseledger.services.review import build_release_review
from releaseledger.storage.store import load_commit_audit_sheet, save_commit_audit_sheet


runner = CliRunner()


def _run(tmp_path: Path, *args: str):
    return runner.invoke(app, ["--cwd", str(tmp_path), *args])


def _json_run(tmp_path: Path, *args: str) -> dict[str, object]:
    result = runner.invoke(app, ["--cwd", str(tmp_path), "--json", *args])
    assert result.exit_code == 0, result.stdout
    return json.loads(result.stdout)


def test_release_check_renders_every_failed_gate() -> None:
    result = {
        "ok": False,
        "checks": {
            "coverage_ok": True,
            "lint_ok": True,
            "changelog_ok": True,
            "release_state_ok": True,
            "chain_ok": False,
            "reconciliation_ok": False,
        },
        "coverage": [],
        "lint": {"errors": 0, "warnings": 0},
        "release": {"status": "released", "released_at": "2026-08-01"},
        "chain": {
            "ok": False,
            "problems": [
                {"kind": "missing_previous", "version": "0.2.8"},
            ],
        },
        "reconciliation": {
            "ok": False,
            "problems": [
                {"kind": "release_without_tag", "version": "0.2.8"},
                {"kind": "changelog_without_release", "version": "0.3.0"},
            ],
        },
    }

    human = _render_release_check_human("0.2.8", result)

    assert "Release chain" in human
    assert "Reconciliation" in human
    assert "missing_previous" in human
    assert "release_without_tag" in human
    assert "changelog_without_release" in human
    assert "Result          FAIL" in human


def test_version_correction_dry_run_previews_changelog_and_preserves_metadata(
    tmp_path: Path,
) -> None:
    assert _run(tmp_path, "init").exit_code == 0
    assert (
        _run(
            tmp_path,
            "release",
            "create",
            "0.3.0",
            "--title",
            "Chapter-Level Markdown Export",
        ).exit_code
        == 0
    )
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## 0.3.0 - 2026-07-31\n\n- Existing release\n",
        encoding="utf-8",
    )

    preview = _json_run(
        tmp_path,
        "release",
        "rename",
        "0.3.0",
        "0.2.8",
        "--previous",
        "v0.2.7",
        "--target-file",
        "CHANGELOG.md",
        "--rename-changelog-section",
        "--dry-run",
    )

    assert preview["result"]["old_version"] == "0.3.0"
    assert preview["result"]["new_version"] == "0.2.8"
    assert preview["result"]["bundle_move"] is True
    assert preview["result"]["old_changelog_section_found"] is True
    assert preview["result"]["changelog_action"] == "rename"
    assert preview["result"]["would_write"] is False
    old_release = _json_run(tmp_path, "release", "show", "0.3.0")
    assert old_release["result"]["release"]["version"] == "0.3.0"
    new_release = _run(tmp_path, "release", "show", "0.2.8")
    assert new_release.exit_code != 0

    applied = _run(
        tmp_path,
        "release",
        "rename",
        "0.3.0",
        "0.2.8",
        "--previous",
        "v0.2.7",
        "--target-file",
        "CHANGELOG.md",
        "--rename-changelog-section",
    )
    assert applied.exit_code == 0, applied.stdout
    changelog = (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## 0.3.0" not in changelog
    assert "## 0.2.8" in changelog

    renamed = _json_run(tmp_path, "release", "show", "0.2.8")
    assert renamed["result"]["release"]["title"] == "Chapter-Level Markdown Export"
    assert renamed["result"]["release"]["status"] == "planned"


def test_version_correction_without_changelog_flag_reports_exact_next_action(
    tmp_path: Path,
) -> None:
    assert _run(tmp_path, "init").exit_code == 0
    assert _run(tmp_path, "release", "create", "0.3.0").exit_code == 0
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## 0.3.0\n\n- Existing release\n", encoding="utf-8"
    )

    renamed = _run(
        tmp_path,
        "release",
        "rename",
        "0.3.0",
        "0.2.8",
        "--target-file",
        "CHANGELOG.md",
    )

    assert renamed.exit_code == 0, renamed.stdout
    assert "old section 0.3.0 still exists" in renamed.stdout
    assert (
        "releaseledger changelog-section rename-section 0.3.0 0.2.8"
        in renamed.stdout
    )


def test_version_correction_changelog_destination_conflict_is_preflighted(
    tmp_path: Path,
) -> None:
    assert _run(tmp_path, "init").exit_code == 0
    assert _run(tmp_path, "release", "create", "0.3.0").exit_code == 0
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## 0.3.0\n\n- Old\n\n## 0.2.8\n\n- Destination\n",
        encoding="utf-8",
    )

    conflict = _run(
        tmp_path,
        "release",
        "rename",
        "0.3.0",
        "0.2.8",
        "--target-file",
        "CHANGELOG.md",
        "--rename-changelog-section",
    )

    assert conflict.exit_code != 0
    assert _run(tmp_path, "release", "show", "0.3.0").exit_code == 0
    assert _run(tmp_path, "release", "show", "0.2.8").exit_code != 0


def test_release_coverage_projects_audit_decisions_without_fake_public_refs(
    tmp_path: Path,
) -> None:
    assert _run(tmp_path, "init").exit_code == 0
    refs = {
        "internal": "git:" + "a" * 40,
        "rejected": "git:" + "b" * 40,
        "accepted": "git:" + "c" * 40,
        "needs_review": "git:" + "d" * 40,
    }
    assert (
        _run(
            tmp_path,
            "release",
            "create",
            "0.2.0",
            *sum((["--source-ref", ref] for ref in refs.values()), []),
        ).exit_code
        == 0
    )
    rows = tuple(
        CommitAuditRow(
            sha=ref.removeprefix("git:"),
            source_ref=ref,
            inspected=decision != "needs_review",
            inspected_paths=("src/internal.py",) if decision != "needs_review" else (),
            observed_behavior="Reviewed commit behavior."
            if decision != "needs_review"
            else "",
            decision=decision,
        )
        for decision, ref in refs.items()
    )
    save_commit_audit_sheet(
        tmp_path,
        CommitAuditSheetRecord(
            release_version="0.2.0",
            versioning=initial_versioning(),
            rows=rows,
        ),
        overwrite=True,
    )

    result = build_release_review(
        tmp_path,
        version="0.2.0",
        require_audit_sheet=True,
    )
    coverage = {row["source_ref"]: row for row in result["coverage"]}
    assert coverage[refs["internal"]]["coverage_requirement"] == "none_for_public"
    assert coverage[refs["internal"]]["accounted_as"] == "internal_audit"
    assert coverage[refs["internal"]]["gate_satisfied"] is True
    assert coverage[refs["rejected"]]["accounted_as"] == "rejected_audit"
    assert coverage[refs["rejected"]]["gate_satisfied"] is True
    assert coverage[refs["accepted"]]["coverage_requirement"] == "public_entry"
    assert coverage[refs["accepted"]]["gate_satisfied"] is False
    assert coverage[refs["needs_review"]]["coverage_requirement"] == "unresolved"
    assert coverage[refs["needs_review"]]["gate_satisfied"] is False
    assert result["checks"]["coverage_ok"] is False


def test_entry_source_ref_mutations_preserve_and_report_operation_intent(
    tmp_path: Path,
) -> None:
    assert _run(tmp_path, "init").exit_code == 0
    assert _run(tmp_path, "release", "create", "0.2.0").exit_code == 0
    assert (
        _run(
            tmp_path,
            "entry",
            "add",
            "0.2.0",
            "--summary",
            "Existing entry",
            "--source-ref",
            "tl:task-0009",
        ).exit_code
        == 0
    )
    git_ref = "git:" + "a" * 40

    added = _json_run(
        tmp_path,
        "entry",
        "update",
        "0.2.0",
        "entry-0001",
        "--add-source-ref",
        git_ref,
        "--reason",
        "Attach reviewed commit provenance.",
    )
    added_entry = added["result"]["entry"]
    assert added["result"]["source_refs_mode"] == "merge"
    assert added_entry["source_refs"] == ["tl:task-0009", git_ref]
    assert added["result"]["added_source_refs"] == [git_ref]

    repeated = _json_run(
        tmp_path,
        "entry",
        "update",
        "0.2.0",
        "entry-0001",
        "--add-source-ref",
        git_ref,
    )
    assert repeated["result"]["source_refs"] == ["tl:task-0009", git_ref]
    assert repeated["result"]["written"] is False

    removed = _json_run(
        tmp_path,
        "entry",
        "update",
        "0.2.0",
        "entry-0001",
        "--remove-source-ref",
        git_ref,
    )
    assert removed["result"]["source_refs"] == ["tl:task-0009"]
    assert removed["result"]["removed_source_refs"] == [git_ref]

    cleared = _json_run(
        tmp_path,
        "entry",
        "update",
        "0.2.0",
        "entry-0001",
        "--clear-source-refs",
    )
    assert cleared["result"]["source_refs"] == []
    assert cleared["result"]["source_refs_mode"] == "clear"


def test_entry_source_ref_mutation_conflicts_fail_before_revision_change(
    tmp_path: Path,
) -> None:
    assert _run(tmp_path, "init").exit_code == 0
    assert _run(tmp_path, "release", "create", "0.2.0").exit_code == 0
    assert (
        _run(
            tmp_path,
            "entry",
            "add",
            "0.2.0",
            "--summary",
            "Existing entry",
            "--source-ref",
            "tl:task-0009",
        ).exit_code
        == 0
    )
    conflict = runner.invoke(
        app,
        [
            "--cwd",
            str(tmp_path),
            "--json",
            "entry",
            "update",
            "0.2.0",
            "entry-0001",
            "--source-ref",
            "git:" + "b" * 40,
            "--add-source-ref",
            "git:" + "c" * 40,
        ],
    )
    assert conflict.exit_code != 0
    payload = json.loads(conflict.stdout)
    assert payload["error"]["code"] == "VALIDATION_ERROR"
    shown = _json_run(tmp_path, "entry", "show", "0.2.0", "entry-0001")
    assert shown["result"]["entry"]["source_refs"] == ["tl:task-0009"]


def test_audit_decisions_command_generates_curatable_template(tmp_path: Path) -> None:
    assert _run(tmp_path, "init").exit_code == 0
    assert _run(tmp_path, "release", "create", "0.2.0").exit_code == 0
    sha = "a" * 40
    save_commit_audit_sheet(
        tmp_path,
        CommitAuditSheetRecord(
            release_version="0.2.0",
            versioning=initial_versioning(),
            rows=(
                CommitAuditRow(
                    sha=sha,
                    source_ref=f"git:{sha}",
                    changed_paths=("src/internal.py",),
                ),
            ),
        ),
        overwrite=True,
    )
    output = tmp_path / "audit-decisions.yaml"
    generated = _run(
        tmp_path,
        "audit",
        "decisions",
        "0.2.0",
        "--output",
        str(output),
    )
    assert generated.exit_code == 0, generated.stdout
    data = yaml.safe_load(output.read_text(encoding="utf-8"))
    assert data == {
        "rows": [
            {
                "sha": sha,
                "inspected": False,
                "inspected_paths": ["src/internal.py"],
                "observed_behavior": "",
                "public_impact": "unknown",
                "decision": "needs_review",
                "target_entry_key": None,
                "notes": "",
            }
        ]
    }


def test_audit_apply_dry_run_reports_all_completed_row_evidence_deficiencies(
    tmp_path: Path,
) -> None:
    assert _run(tmp_path, "init").exit_code == 0
    assert _run(tmp_path, "release", "create", "0.2.0").exit_code == 0
    sha = "b" * 40
    save_commit_audit_sheet(
        tmp_path,
        CommitAuditSheetRecord(
            release_version="0.2.0",
            versioning=initial_versioning(),
            rows=(
                CommitAuditRow(
                    sha=sha,
                    source_ref=f"git:{sha}",
                    changed_paths=("src/internal.py",),
                ),
            ),
        ),
        overwrite=True,
    )
    decisions = tmp_path / "incomplete-decisions.yaml"
    decisions.write_text(
        yaml.safe_dump({"rows": [{"sha": sha, "decision": "internal"}]}),
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "--cwd",
            str(tmp_path),
            "--json",
            "audit",
            "apply",
            "0.2.0",
            "--file",
            str(decisions),
            "--dry-run",
        ],
    )
    assert result.exit_code != 0
    assert "inspected" in result.stdout
    assert "inspected_paths" in result.stdout
    assert "observed_behavior" in result.stdout
    saved = load_commit_audit_sheet(tmp_path, "0.2.0")
    assert saved is not None
    assert saved.rows[0].decision == "needs_review"
