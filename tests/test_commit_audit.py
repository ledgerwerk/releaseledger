"""Tests for the commit audit sheet domain, storage, and service layers.

Most tests construct sheets and entries directly via public helpers so they do
not require a git worktree. Git-backed ``audit init`` lives in
``tests/test_commit_audit_cli.py``.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from releaseledger.domain.audit import (
    AUDIT_DECISIONS,
    AUDIT_PUBLIC_IMPACTS,
    CommitAuditRow,
    CommitAuditSheetRecord,
    audit_sheet_from_dict,
    audit_sheet_to_dict,
    initial_versioning,
    normalize_audit_decision,
    normalize_public_impact,
    validate_sha,
)
from releaseledger.domain.versioning import RecordVersioning, bump_versioning
from releaseledger.errors import LaunchError
from releaseledger.services.audit import (
    subject_matches_evidence_subject,
    sync_audit_targets_from_entries,
    validate_commit_audit_sheet,
)
from releaseledger.storage.paths import resolve_project_paths
from releaseledger.storage.store import (
    commit_audit_path,
    delete_commit_audit_sheet,
    load_commit_audit_sheet,
    save_commit_audit_sheet,
)

SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40


def _row(
    sha: str = SHA_A,
    *,
    decision: str = "needs_review",
    inspected: bool = False,
    evidence_subject: str | None = None,
    public_impact: str = "unknown",
    target_entry_id: str | None = None,
) -> CommitAuditRow:
    return CommitAuditRow(
        sha=sha,
        source_ref=f"git:{sha}",
        short_sha=sha[:7],
        evidence_subject=evidence_subject,
        decision=decision,
        inspected=inspected,
        public_impact=public_impact,
        target_entry_id=target_entry_id,
    )


def _sheet(rows: tuple[CommitAuditRow, ...] = ()) -> CommitAuditSheetRecord:
    return CommitAuditSheetRecord(
        release_version="0.2.0",
        versioning=initial_versioning(),
        git_base_ref="v0.1.0",
        git_base_sha="1" * 40,
        git_head_ref="v0.2.0",
        git_head_sha="2" * 40,
        git_range=f"{'1' * 40}..{'2' * 40}",
        commit_count=len(rows),
        rows=rows,
    )


# --------------------------------------------------------------------------
# Domain
# --------------------------------------------------------------------------


class TestAuditDomain:
    def test_roundtrip_preserves_fields(self) -> None:
        row = _row(SHA_A, evidence_subject="feat: add x", decision="accepted")
        sheet = _sheet((row,))
        data = audit_sheet_to_dict(sheet)
        back = audit_sheet_from_dict(data)
        assert back.release_version == "0.2.0"
        assert back.rows[0].sha == SHA_A
        assert back.rows[0].evidence_subject == "feat: add x"
        assert back.rows[0].decision == "accepted"

    def test_validate_sha_rejects_short(self) -> None:
        with pytest.raises(LaunchError):
            validate_sha("abcdef")

    def test_validate_sha_lowercases(self) -> None:
        assert validate_sha("A" * 40) == "a" * 40

    def test_normalize_decision_rejects_unknown(self) -> None:
        with pytest.raises(LaunchError):
            normalize_audit_decision("bogus")
        assert AUDIT_DECISIONS == (
            "needs_review",
            "accepted",
            "grouped",
            "internal",
            "rejected",
        )

    def test_normalize_public_impact_rejects_unknown(self) -> None:
        with pytest.raises(LaunchError):
            normalize_public_impact("bogus")
        assert "public" in AUDIT_PUBLIC_IMPACTS

    def test_duplicate_sha_rejected(self) -> None:
        data = audit_sheet_to_dict(_sheet((_row(SHA_A), _row(SHA_A))))
        with pytest.raises(LaunchError):
            audit_sheet_from_dict(data)

    def test_source_ref_must_equal_git_sha(self) -> None:
        data = audit_sheet_to_dict(_sheet((_row(SHA_A),)))
        data["rows"][0]["source_ref"] = f"git:{SHA_B}"
        with pytest.raises(LaunchError):
            audit_sheet_from_dict(data)


class TestSubjectGuard:
    def test_exact_match(self) -> None:
        assert subject_matches_evidence_subject("Add feature", "Add feature")

    def test_case_only_variant(self) -> None:
        assert subject_matches_evidence_subject("add feature", "Add Feature")

    def test_punctuation_only_variant(self) -> None:
        assert subject_matches_evidence_subject("feat: add-thing", "Feat Add Thing")

    def test_distinct_does_not_match(self) -> None:
        assert not subject_matches_evidence_subject(
            "Rewrote the storage layer for atomicity", "feat: add storage"
        )

    def test_empty_does_not_match(self) -> None:
        assert not subject_matches_evidence_subject("", "subject")


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------


class TestAuditStorage:
    @staticmethod
    def _init_project(tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from releaseledger.cli import app

        result = CliRunner().invoke(app, ["--cwd", str(tmp_path), "init"])
        assert result.exit_code == 0, result.stdout

    def test_save_load_roundtrip(self, tmp_path: Path) -> None:
        self._init_project(tmp_path)
        sheet = _sheet((_row(SHA_A),))
        save_commit_audit_sheet(tmp_path, sheet, overwrite=True)
        loaded = load_commit_audit_sheet(tmp_path, "0.2.0")
        assert loaded is not None
        assert loaded.rows[0].sha == SHA_A

    def test_save_refuses_without_overwrite_on_existing(self, tmp_path: Path) -> None:
        self._init_project(tmp_path)
        sheet = _sheet((_row(SHA_A),))
        save_commit_audit_sheet(tmp_path, sheet, overwrite=True)
        with pytest.raises(LaunchError):
            save_commit_audit_sheet(tmp_path, sheet, overwrite=False)

    def test_revision_must_advance_on_change(self, tmp_path: Path) -> None:
        self._init_project(tmp_path)
        sheet = _sheet((_row(SHA_A),))
        save_commit_audit_sheet(tmp_path, sheet, overwrite=True)
        # Same revision but changed content -> rejected.
        changed = CommitAuditSheetRecord(
            release_version="0.2.0",
            versioning=initial_versioning(),
            rows=(_row(SHA_B),),
        )
        with pytest.raises(LaunchError):
            save_commit_audit_sheet(tmp_path, changed, overwrite=True)
        # Bumping revision allows the change.
        bumped = CommitAuditSheetRecord(
            release_version="0.2.0",
            versioning=bump_versioning(RecordVersioning()),
            rows=(_row(SHA_B),),
        )
        save_commit_audit_sheet(tmp_path, bumped, overwrite=True)
        assert commit_audit_path(resolve_project_paths(tmp_path), "0.2.0").is_file()

    def test_delete(self, tmp_path: Path) -> None:
        self._init_project(tmp_path)
        save_commit_audit_sheet(tmp_path, _sheet((_row(SHA_A),)), overwrite=True)
        assert delete_commit_audit_sheet(tmp_path, "0.2.0") is True
        assert delete_commit_audit_sheet(tmp_path, "0.2.0") is False
        assert load_commit_audit_sheet(tmp_path, "0.2.0") is None


# --------------------------------------------------------------------------
# Service: validate + sync (no git required)
# --------------------------------------------------------------------------


def _add_entries(tmp_path: Path, entries: list[dict[str, object]]) -> None:
    from typer.testing import CliRunner

    from releaseledger.cli import app

    runner = CliRunner()
    assert runner.invoke(app, ["--cwd", str(tmp_path), "init"]).exit_code == 0
    assert (
        runner.invoke(
            app,
            [
                "--cwd",
                str(tmp_path),
                "release",
                "create",
                "0.2.0",
                "--released-at",
                "2026-06-14",
            ],
        ).exit_code
        == 0
    )
    import yaml

    batch = {"entries": entries}
    (tmp_path / "entries.yaml").write_text(yaml.safe_dump(batch))
    if not entries:
        return
    result = runner.invoke(
        app,
        [
            "--cwd",
            str(tmp_path),
            "entry",
            "add-many",
            "0.2.0",
            "--file",
            str(tmp_path / "entries.yaml"),
        ],
    )
    assert result.exit_code == 0, result.output


class TestValidateAndSync:
    def test_strict_fails_needs_review(self, tmp_path: Path) -> None:
        _add_entries(tmp_path, [])
        save_commit_audit_sheet(tmp_path, _sheet((_row(SHA_A),)), overwrite=True)
        with pytest.raises(LaunchError):
            validate_commit_audit_sheet(tmp_path, version="0.2.0", strict=True)

    def test_strict_fails_uninspected(self, tmp_path: Path) -> None:
        _add_entries(
            tmp_path,
            [
                {
                    "kind": "added",
                    "summary": "Added feature A",
                    "source_refs": [f"git:{SHA_A}"],
                    "status": "accepted",
                }
            ],
        )
        sheet = _sheet((_row(SHA_A, decision="accepted", inspected=False),))
        save_commit_audit_sheet(tmp_path, sheet, overwrite=True)
        with pytest.raises(LaunchError):
            validate_commit_audit_sheet(tmp_path, version="0.2.0", strict=True)

    def test_strict_fails_missing_coverage_for_accepted(self, tmp_path: Path) -> None:
        _add_entries(tmp_path, [])
        row = replace(
            _row(SHA_A, decision="accepted", inspected=True),
            inspected_paths=("src/a.py",),
            observed_behavior="Reviewed behavior.",
        )
        sheet = _sheet((row,))
        save_commit_audit_sheet(tmp_path, sheet, overwrite=True)
        with pytest.raises(LaunchError):
            validate_commit_audit_sheet(tmp_path, version="0.2.0", strict=True)

    def test_evidence_phase_passes_without_entries(self, tmp_path: Path) -> None:
        _add_entries(tmp_path, [])
        row = replace(
            _row(SHA_A, decision="accepted", inspected=True),
            inspected_paths=("src/a.py",),
            observed_behavior="Reviewed behavior.",
        )
        sheet = _sheet((row,))
        save_commit_audit_sheet(tmp_path, sheet, overwrite=True)
        report = validate_commit_audit_sheet(
            tmp_path,
            version="0.2.0",
            phase="evidence",
            strict=True,
        )
        assert report["ok"] is True
        assert report["phase"] == "evidence"

    def test_complete_phase_fails_without_entries(self, tmp_path: Path) -> None:
        _add_entries(tmp_path, [])
        row = replace(
            _row(SHA_A, decision="accepted", inspected=True),
            inspected_paths=("src/a.py",),
            observed_behavior="Reviewed behavior.",
        )
        sheet = _sheet((row,))
        save_commit_audit_sheet(tmp_path, sheet, overwrite=True)
        with pytest.raises(LaunchError):
            validate_commit_audit_sheet(
                tmp_path,
                version="0.2.0",
                phase="complete",
                strict=True,
            )

    def test_strict_passes_when_accepted_covered(self, tmp_path: Path) -> None:
        _add_entries(
            tmp_path,
            [
                {
                    "kind": "added",
                    "summary": "Added feature A from reviewed behavior",
                    "source_refs": [f"git:{SHA_A}"],
                    "status": "accepted",
                }
            ],
        )
        sheet = _sheet(
            (
                replace(
                    _row(SHA_A, decision="accepted", inspected=True),
                    inspected_paths=("src/a.py",),
                    observed_behavior="Reviewed behavior.",
                ),
            )
        )
        save_commit_audit_sheet(tmp_path, sheet, overwrite=True)
        report = validate_commit_audit_sheet(tmp_path, version="0.2.0", strict=True)
        assert report["ok"] is True

    def test_strict_include_internal_passes_internal_covered(
        self, tmp_path: Path
    ) -> None:
        _add_entries(
            tmp_path,
            [
                {
                    "kind": "internal",
                    "summary": "Internal housekeeping refactor",
                    "source_refs": [f"git:{SHA_A}"],
                    "status": "accepted",
                    "internal": True,
                }
            ],
        )
        sheet = _sheet(
            (
                replace(
                    _row(SHA_A, decision="internal", inspected=True),
                    inspected_paths=("src/a.py",),
                    observed_behavior="Reviewed behavior.",
                ),
            )
        )
        save_commit_audit_sheet(tmp_path, sheet, overwrite=True)
        report = validate_commit_audit_sheet(
            tmp_path,
            version="0.2.0",
            strict=True,
            include_internal=True,
        )
        assert report["ok"] is True

    def test_subject_guard_flags_matching_summary(self, tmp_path: Path) -> None:
        _add_entries(
            tmp_path,
            [
                {
                    "kind": "added",
                    "summary": "feat: add thing",  # copies the subject
                    "source_refs": [f"git:{SHA_A}"],
                    "status": "accepted",
                }
            ],
        )
        sheet = _sheet(
            (
                _row(
                    SHA_A,
                    decision="accepted",
                    inspected=True,
                    evidence_subject="feat: add thing",
                ),
            )
        )
        save_commit_audit_sheet(tmp_path, sheet, overwrite=True)
        report = validate_commit_audit_sheet(tmp_path, version="0.2.0", strict=False)
        violations = report["subject_summary_violations"]
        assert isinstance(violations, list)
        assert f"git:{SHA_A}" in violations

    def test_sync_fills_target_entry_id(self, tmp_path: Path) -> None:
        _add_entries(
            tmp_path,
            [
                {
                    "kind": "added",
                    "summary": "Added feature A from reviewed behavior",
                    "source_refs": [f"git:{SHA_A}", f"git:{SHA_B}"],
                    "status": "accepted",
                }
            ],
        )
        sheet = _sheet(
            (
                _row(SHA_A, decision="accepted"),
                _row(SHA_B, decision="grouped"),
            )
        )
        save_commit_audit_sheet(tmp_path, sheet, overwrite=True)
        result = sync_audit_targets_from_entries(tmp_path, version="0.2.0")
        assert result["updated_rows"] == 2
        loaded = load_commit_audit_sheet(tmp_path, "0.2.0")
        assert loaded is not None
        assert {r.target_entry_id for r in loaded.rows} == {"entry-0001"}
