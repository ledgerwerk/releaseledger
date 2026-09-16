"""Acceptance tests for the release-correction layer.

Covers every named acceptance test in
``releaseledger_v050_cancellation_review.md`` plus an end-to-end regression
fixture that reproduces the run (v0.1.1..v0.4.3, then v0.1.0 backfilled with a
broken predecessor chain) and drives it through chain check -> repair ->
rename v0.4.3 -> v0.5.0.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

from releaseledger.cli import app
from releaseledger.domain.audit import (
    CommitAuditRow,
    CommitAuditSheetRecord,
    CommitAuditStats,
)
from releaseledger.domain.states import RELEASE_STATUSES
from releaseledger.domain.versioning import RecordVersioning, bump_versioning
from releaseledger.errors import LaunchError
from releaseledger.services.audit import validate_commit_audit_sheet
from releaseledger.services.changelog_build import (
    find_release_section,
    remove_release_section,
    rename_release_section,
)
from releaseledger.services.releases import (
    _infer_previous_version,
    cancel_release,
    check_release_chain,
    create_release,
    rename_release,
    repair_release_chain,
    tag_release,
    update_release,
)
from releaseledger.storage.store import (
    list_releases,
    load_commit_audit_sheet,
    load_release,
    save_commit_audit_sheet,
    save_release,
)

runner = CliRunner()


def _init(tmp_path: Path) -> Path:
    assert runner.invoke(app, ["--cwd", str(tmp_path), "init"]).exit_code == 0
    return tmp_path


def _run(tmp_path: Path, *cmd: str):
    return runner.invoke(app, ["--cwd", str(tmp_path), *cmd])


def _jrun(tmp_path: Path, *cmd: str):
    return runner.invoke(app, ["--cwd", str(tmp_path), "--json", *cmd])


def _jout(result) -> dict[str, object]:
    assert result.exit_code == 0, result.stdout
    return json.loads(result.stdout)


def _human_error(result) -> str:
    try:
        stderr = result.stderr or ""
    except ValueError:
        stderr = ""
    return stderr + (result.stdout or "")


# ---------------------------------------------------------------------------
# Status / constants
# ---------------------------------------------------------------------------


class TestStatusAndConstants:
    def test_canceled_status_exists(self) -> None:
        assert "canceled" in RELEASE_STATUSES


# ---------------------------------------------------------------------------
# ReleaseRecord cancellation fields
# ---------------------------------------------------------------------------


class TestReleaseRecordFields:
    def test_record_round_trips_cancellation_fields_without_date(self) -> None:
        from releaseledger.domain.release import ReleaseRecord, release_from_dict

        record = ReleaseRecord(
            version="v0.5.0",
            status="canceled",
            cancel_reason="never shipped",
            superseded_by="v0.6.0",
        )
        rebuilt = release_from_dict(record.to_dict())
        assert "canceled_at" not in rebuilt.to_dict()
        assert rebuilt.cancel_reason == "never shipped"
        assert rebuilt.superseded_by == "v0.6.0"

    def test_optional_cancellation_fields_default_to_none(self) -> None:
        from releaseledger.domain.release import release_from_dict

        legacy = {
            "schema_version": 2,
            "versioning": {"schema_version": 1, "revision": 1},
            "object_type": "release",
            "version": "v0.1.0",
            "status": "released",
            "title": "Release v0.1.0",
            "previous_version": None,
        }
        record = release_from_dict(legacy)
        assert record.cancel_reason is None
        assert record.superseded_by is None

    def test_superseded_by_validates_release_version_shape(self) -> None:
        from releaseledger.domain.release import release_from_dict

        bad = {
            "schema_version": 2,
            "versioning": {"schema_version": 1, "revision": 1},
            "object_type": "release",
            "version": "v0.5.0",
            "status": "canceled",
            "title": "x",
            "superseded_by": "a/b",
        }
        with pytest.raises(LaunchError):
            release_from_dict(bad)


# ---------------------------------------------------------------------------
# release update --clear-* flags
# ---------------------------------------------------------------------------


class TestUpdateClearFlags:
    def test_release_update_clear_previous_sets_none(self, tmp_path: Path) -> None:
        _init(tmp_path)
        assert (
            _run(
                tmp_path, "release", "tag", "v0.4.2", "--released-at", "2026-04-01"
            ).exit_code
            == 0
        )
        assert (
            _run(
                tmp_path, "release", "tag", "v0.4.3", "--released-at", "2026-05-01"
            ).exit_code
            == 0
        )
        result = _jout(
            _jrun(tmp_path, "release", "update", "v0.4.3", "--clear-previous")
        )
        assert result["result"]["release"]["previous_version"] is None

    def test_release_update_clear_previous_conflicts_with_previous_option(
        self, tmp_path: Path
    ) -> None:
        _init(tmp_path)
        _run(tmp_path, "release", "tag", "v0.4.2", "--released-at", "2026-04-01")
        result = _jrun(
            tmp_path,
            "release",
            "update",
            "v0.4.2",
            "--previous",
            "v0.1.0",
            "--clear-previous",
        )
        assert result.exit_code != 0
        payload = json.loads(result.stdout)
        assert payload["ok"] is False
        assert payload["error"]["code"] == "USAGE_ERROR"

    def test_release_update_clear_source_refs(self, tmp_path: Path) -> None:
        _init(tmp_path)
        _run(
            tmp_path,
            "release",
            "tag",
            "v0.4.2",
            "--released-at",
            "2026-04-01",
            "--source-ref",
            "tl:task-0001",
        )
        result = _jout(
            _jrun(tmp_path, "release", "update", "v0.4.2", "--clear-source-refs")
        )
        assert result["result"]["release"]["source_refs"] == []

    def test_release_update_clear_released_at_requires_force_for_released(
        self, tmp_path: Path
    ) -> None:
        _init(tmp_path)
        _run(tmp_path, "release", "tag", "v0.4.2", "--released-at", "2026-04-01")
        # Without --force: rejected.
        result = _jrun(tmp_path, "release", "update", "v0.4.2", "--clear-released-at")
        assert result.exit_code != 0
        assert json.loads(result.stdout)["error"]["code"] == "USAGE_ERROR"
        # With --force: succeeds.
        ok = _jout(
            _jrun(
                tmp_path,
                "release",
                "update",
                "v0.4.2",
                "--clear-released-at",
                "--force",
            )
        )
        assert ok["result"]["release"]["released_at"] is None


# ---------------------------------------------------------------------------
# Previous-version inference and same-date sort
# ---------------------------------------------------------------------------


class TestInferenceAndSort:
    def test_create_historical_release_does_not_infer_future_previous(
        self, tmp_path: Path
    ) -> None:
        _init(tmp_path)
        tag_release(tmp_path, version="v0.4.3", released_at="2026-05-01")
        # Backfill an older release.
        previous, _ = _infer_previous_version(
            tmp_path, candidate_version="v0.1.0", candidate_released_at="2026-01-01"
        )
        assert previous is None

    def test_create_same_date_release_uses_semver_order_when_parseable(
        self, tmp_path: Path
    ) -> None:
        _init(tmp_path)
        # v0.1.1 released on 2026-01-01 first.
        create_release(
            tmp_path, version="v0.1.1", status="released", released_at="2026-01-01"
        )
        # A later release on the same date but a higher version should still see
        # v0.1.1 as its predecessor.
        previous, _ = _infer_previous_version(
            tmp_path, candidate_version="v0.1.2", candidate_released_at="2026-01-01"
        )
        assert previous == "v0.1.1"

    def test_same_date_releases_sort_by_semver_in_list(self, tmp_path: Path) -> None:
        _init(tmp_path)
        create_release(
            tmp_path, version="v0.1.1", status="released", released_at="2026-01-01"
        )
        create_release(
            tmp_path, version="v0.4.3", status="released", released_at="2026-05-01"
        )
        # Backfill v0.1.0 on the same date as v0.1.1.
        create_release(
            tmp_path, version="v0.1.0", status="released", released_at="2026-01-01"
        )
        order = [r.version for r in list_releases(tmp_path)]
        assert order == ["v0.1.0", "v0.1.1", "v0.4.3"]


# ---------------------------------------------------------------------------
# release cancel
# ---------------------------------------------------------------------------


class TestCancelRelease:
    def test_release_cancel_candidate_sets_canceled_status_and_event(
        self, tmp_path: Path
    ) -> None:
        _init(tmp_path)
        create_release(tmp_path, version="v0.5.0", status="candidate")
        result = cancel_release(tmp_path, version="v0.5.0", reason="never shipped")
        record = load_release(tmp_path, "v0.5.0")
        assert record.status == "canceled"
        assert record.cancel_reason == "never shipped"
        assert isinstance(result.get("events"), list) and result["events"]

    def test_release_cancel_released_requires_force_unshipped(
        self, tmp_path: Path
    ) -> None:
        _init(tmp_path)
        tag_release(tmp_path, version="v0.6.0", released_at="2026-06-01")
        with pytest.raises(LaunchError) as exc_info:
            cancel_release(tmp_path, version="v0.6.0")
        assert exc_info.value.code == "USAGE_ERROR"
        # With the flag, it succeeds.
        cancel_release(
            tmp_path,
            version="v0.6.0",
            force_released_unshipped=True,
            reason="unshipped",
        )
        assert load_release(tmp_path, "v0.6.0").status == "canceled"

    def test_release_cancel_excluded_from_previous_inference(
        self, tmp_path: Path
    ) -> None:
        _init(tmp_path)
        create_release(
            tmp_path, version="v0.4.2", status="released", released_at="2026-04-01"
        )
        tag_release(tmp_path, version="v0.4.3", released_at="2026-05-01")
        cancel_release(
            tmp_path,
            version="v0.4.3",
            reason="never shipped",
            force_released_unshipped=True,
        )
        previous, _ = _infer_previous_version(
            tmp_path, candidate_version="v0.5.0", candidate_released_at="2026-06-14"
        )
        assert previous == "v0.4.2"

    def test_release_cancel_with_superseded_by_validates_version(
        self, tmp_path: Path
    ) -> None:
        _init(tmp_path)
        create_release(tmp_path, version="v0.5.0", status="candidate")
        with pytest.raises(LaunchError):
            cancel_release(tmp_path, version="v0.5.0", superseded_by="bad/version")
        cancel_release(tmp_path, version="v0.5.0", superseded_by="v0.6.0")
        assert load_release(tmp_path, "v0.5.0").superseded_by == "v0.6.0"


# ---------------------------------------------------------------------------
# release rename
# ---------------------------------------------------------------------------


class TestRenameRelease:
    def test_rename_preserves_identity_fields_and_rewrites_audit(
        self, tmp_path: Path
    ) -> None:
        from releaseledger.services.entries import add_release_entry

        _init(tmp_path)
        create_release(
            tmp_path,
            version="0.7.0",
            title="Release 0.7.0",
            history_state="audited",
        )
        original = load_release(tmp_path, "0.7.0")
        save_release(
            tmp_path,
            replace(
                original,
                git_base_ref="v0.6.1",
                git_base_sha="a" * 40,
                versioning=bump_versioning(original.versioning),
                git_head_ref="HEAD",
                git_head_sha="b" * 40,
                git_range="v0.6.1..HEAD",
                git_commit_count=1,
            ),
            overwrite=True,
        )
        source_ref = "git:" + "c" * 40
        add_release_entry(
            tmp_path,
            release_version="0.7.0",
            kind="added",
            summary="Reviewed feature",
            source_refs=(source_ref,),
        )
        row = CommitAuditRow(
            sha="c" * 40,
            source_ref=source_ref,
            evidence_subject="feat: reviewed feature",
            changed_paths=("src/feature.py",),
            stats=CommitAuditStats(files_changed=1, insertions=2),
            inspected=True,
            inspected_paths=("src/feature.py",),
            observed_behavior="Reviewed feature behavior.",
            decision="accepted",
        )
        save_commit_audit_sheet(
            tmp_path,
            CommitAuditSheetRecord(
                release_version="0.7.0",
                versioning=RecordVersioning(),
                git_base_ref="v0.6.1",
                git_base_sha="a" * 40,
                git_head_ref="HEAD",
                git_head_sha="b" * 40,
                git_range="v0.6.1..HEAD",
                commit_count=1,
                rows=(row,),
            ),
        )

        rename_release(tmp_path, old_version="0.7.0", new_version="0.6.2")
        renamed = load_release(tmp_path, "0.6.2")
        assert renamed.title == "Release 0.6.2"
        assert renamed.history_state == "audited"
        assert renamed.git_base_ref == "v0.6.1"
        assert renamed.git_base_sha == "a" * 40
        assert renamed.git_head_ref == "HEAD"
        assert renamed.git_head_sha == "b" * 40
        assert renamed.git_range == "v0.6.1..HEAD"
        assert renamed.git_commit_count == 1
        audit = load_commit_audit_sheet(tmp_path, "0.6.2")
        assert audit is not None
        assert audit.release_version == "0.6.2"
        assert audit.versioning.revision == 2
        assert audit.rows == (row,)
        assert (
            validate_commit_audit_sheet(
                tmp_path, version="0.6.2", phase="evidence", strict=True
            )["ok"]
            is True
        )

    def test_rename_dry_run_exposes_after_identity_without_mutating(
        self, tmp_path: Path
    ) -> None:
        _init(tmp_path)
        create_release(tmp_path, version="0.7.0", title="Release 0.7.0")
        preview = rename_release(
            tmp_path, old_version="0.7.0", new_version="0.6.2", dry_run=True
        )
        assert preview["release_before"]["title"] == "Release 0.7.0"
        assert preview["release_after"]["title"] == "Release 0.6.2"
        assert preview["field_changes"]["version"] == ["0.7.0", "0.6.2"]
        assert load_release(tmp_path, "0.7.0").title == "Release 0.7.0"
        human = _run(
            tmp_path,
            "release",
            "rename",
            "0.7.0",
            "0.6.2",
            "--dry-run",
        )
        assert human.exit_code == 0, human.stdout
        assert "title: Release 0.7.0 -> Release 0.6.2" in human.stdout

    def test_rename_preserves_custom_title(self, tmp_path: Path) -> None:
        _init(tmp_path)
        create_release(tmp_path, version="0.7.0", title="Internal milestone")
        rename_release(tmp_path, old_version="0.7.0", new_version="0.6.2")
        assert load_release(tmp_path, "0.6.2").title == "Internal milestone"

    def test_release_rename_moves_bundle_and_updates_release_frontmatter(
        self, tmp_path: Path
    ) -> None:
        _init(tmp_path)
        tag_release(tmp_path, version="v0.4.2", released_at="2026-04-01")
        tag_release(tmp_path, version="v0.4.3", released_at="2026-05-01")
        rename_release(
            tmp_path,
            old_version="v0.4.3",
            new_version="v0.5.0",
            previous_version="v0.4.2",
            force_released_unshipped=True,
        )
        from releaseledger.storage.paths import resolve_project_paths
        from releaseledger.storage.store import release_dir

        paths = resolve_project_paths(tmp_path)
        assert not release_dir(paths, "v0.4.3").exists()
        renamed = load_release(tmp_path, "v0.5.0")
        assert renamed.version == "v0.5.0"
        assert renamed.previous_version == "v0.4.2"

    def test_release_rename_updates_entry_release_version_frontmatter(
        self, tmp_path: Path
    ) -> None:
        from releaseledger.services.entries import add_release_entry
        from releaseledger.storage.store import load_entries

        _init(tmp_path)
        tag_release(tmp_path, version="v0.4.3", released_at="2026-05-01")
        add_release_entry(
            tmp_path, release_version="v0.4.3", kind="added", summary="feature"
        )
        rename_release(
            tmp_path,
            old_version="v0.4.3",
            new_version="v0.5.0",
            force_released_unshipped=True,
        )
        entries = load_entries(tmp_path, "v0.5.0")
        assert len(entries) == 1
        assert entries[0].release_version == "v0.5.0"

    def test_release_rename_rebuilds_indexes(self, tmp_path: Path) -> None:
        _init(tmp_path)
        tag_release(tmp_path, version="v0.4.3", released_at="2026-05-01")
        rename_release(
            tmp_path,
            old_version="v0.4.3",
            new_version="v0.5.0",
            force_released_unshipped=True,
        )
        from releaseledger.storage.paths import resolve_project_paths

        paths = resolve_project_paths(tmp_path)
        index = json.loads(paths.releases_index_path.read_text())
        versions = [row["version"] for row in index]
        assert "v0.5.0" in versions and "v0.4.3" not in versions

    def test_release_rename_appends_event_with_old_and_new_versions(
        self, tmp_path: Path
    ) -> None:
        _init(tmp_path)
        tag_release(tmp_path, version="v0.4.3", released_at="2026-05-01")
        result = rename_release(
            tmp_path,
            old_version="v0.4.3",
            new_version="v0.5.0",
            force_released_unshipped=True,
        )
        from releaseledger.services.events import load_events

        events = load_events(tmp_path)
        renamed = [e for e in events if e.event == "release.renamed"]
        assert renamed
        assert renamed[-1].data.get("old_release_version") == "v0.4.3"
        assert renamed[-1].release_version == "v0.5.0"
        assert "to_version" not in renamed[-1].data
        assert result.get("events")

    def test_release_rename_refuses_existing_target_version(
        self, tmp_path: Path
    ) -> None:
        _init(tmp_path)
        tag_release(tmp_path, version="v0.4.2", released_at="2026-04-01")
        tag_release(tmp_path, version="v0.4.3", released_at="2026-05-01")
        with pytest.raises(LaunchError) as exc_info:
            rename_release(
                tmp_path,
                old_version="v0.4.3",
                new_version="v0.4.2",
                force_released_unshipped=True,
            )
        assert exc_info.value.code == "CONFLICT"

    def test_release_rename_refuses_released_without_force_unshipped(
        self, tmp_path: Path
    ) -> None:
        _init(tmp_path)
        tag_release(tmp_path, version="v0.4.3", released_at="2026-05-01")
        with pytest.raises(LaunchError) as exc_info:
            rename_release(tmp_path, old_version="v0.4.3", new_version="v0.5.0")
        assert exc_info.value.code == "USAGE_ERROR"

    def test_release_rename_rewrite_successors_updates_previous_version(
        self, tmp_path: Path
    ) -> None:
        _init(tmp_path)
        create_release(
            tmp_path, version="v0.4.2", status="released", released_at="2026-04-01"
        )
        create_release(tmp_path, version="v0.4.3", status="candidate")
        create_release(
            tmp_path, version="v0.5.0", status="candidate", previous_version="v0.4.3"
        )
        rename_release(
            tmp_path,
            old_version="v0.4.3",
            new_version="v0.4.5",
            rewrite_successors=True,
        )
        assert load_release(tmp_path, "v0.5.0").previous_version == "v0.4.5"

    def test_release_rename_without_rewrite_successors_fails_when_successors_exist(
        self, tmp_path: Path
    ) -> None:
        _init(tmp_path)
        create_release(tmp_path, version="v0.4.3", status="candidate")
        create_release(
            tmp_path, version="v0.5.0", status="candidate", previous_version="v0.4.3"
        )
        with pytest.raises(LaunchError) as exc_info:
            rename_release(tmp_path, old_version="v0.4.3", new_version="v0.4.5")
        assert exc_info.value.code == "CONFLICT"
        assert exc_info.value.data.get("successors") == ["v0.5.0"]


# ---------------------------------------------------------------------------
# Chain check and repair
# ---------------------------------------------------------------------------


class TestChainCheckAndRepair:
    def test_release_chain_check_reports_missing_previous(self, tmp_path: Path) -> None:
        _init(tmp_path)
        create_release(tmp_path, version="v0.5.0", previous_version="v0.4.2")
        result = check_release_chain(tmp_path)
        assert result["ok"] is False
        kinds = [p["kind"] for p in result["problems"]]
        assert "missing_previous" in kinds

    def test_release_create_canonicalizes_v_prefixed_predecessor(
        self, tmp_path: Path
    ) -> None:
        _init(tmp_path)
        create_release(tmp_path, version="0.4.3")
        create_release(tmp_path, version="v0.4.4", previous_version="v0.4.3")
        assert load_release(tmp_path, "v0.4.4").previous_version == "0.4.3"

    def test_release_update_canonicalizes_v_prefixed_predecessor(
        self, tmp_path: Path
    ) -> None:
        _init(tmp_path)
        create_release(tmp_path, version="0.4.3")
        create_release(tmp_path, version="v0.4.4")
        update_release(tmp_path, version="v0.4.4", previous_version="v0.4.3")
        assert load_release(tmp_path, "v0.4.4").previous_version == "0.4.3"

    def test_chain_check_resolves_mixed_prefix_previous_identity(
        self, tmp_path: Path
    ) -> None:
        _init(tmp_path)
        create_release(tmp_path, version="0.4.3")
        create_release(tmp_path, version="v0.4.4", previous_version="0.4.3")
        current = load_release(tmp_path, "v0.4.4")
        save_release(
            tmp_path,
            replace(
                current,
                previous_version="v0.4.3",
                versioning=bump_versioning(current.versioning),
            ),
            overwrite=True,
        )
        result = check_release_chain(tmp_path)
        assert not any(
            problem["kind"] == "missing_previous" for problem in result["problems"]
        )

    def test_predecessor_identity_ambiguity_fails_on_write(
        self, tmp_path: Path
    ) -> None:
        _init(tmp_path)
        create_release(tmp_path, version="0.4.3")
        create_release(tmp_path, version="v0.4.3")
        with pytest.raises(LaunchError, match="ambiguous"):
            create_release(tmp_path, version="0.4.4", previous_version="v0.4.3")

    def test_release_chain_check_reports_future_previous(self, tmp_path: Path) -> None:
        _init(tmp_path)
        # Earliest release points at a future release.
        create_release(
            tmp_path,
            version="v0.1.0",
            status="released",
            released_at="2026-01-01",
            previous_version="v0.4.3",
        )
        create_release(
            tmp_path, version="v0.4.3", status="released", released_at="2026-05-01"
        )
        result = check_release_chain(tmp_path)
        kinds = [p["kind"] for p in result["problems"]]
        assert "future_previous" in kinds
        assert "root_has_previous" in kinds

    def test_release_chain_repair_dry_run_for_v010_previous_v043_case(
        self, tmp_path: Path
    ) -> None:
        _init(tmp_path)
        tag_release(tmp_path, version="v0.1.1", released_at="2026-01-01")
        tag_release(
            tmp_path,
            version="v0.1.2",
            released_at="2026-02-01",
            previous_version="v0.1.1",
        )
        tag_release(
            tmp_path,
            version="v0.4.3",
            released_at="2026-05-01",
            previous_version="v0.1.2",
        )
        tag_release(
            tmp_path,
            version="v0.1.0",
            released_at="2026-01-01",
            previous_version="v0.4.3",
        )
        dry = repair_release_chain(tmp_path)
        assert dry["applied"] is False
        # Nothing changed yet.
        assert load_release(tmp_path, "v0.1.0").previous_version == "v0.4.3"
        applied = repair_release_chain(tmp_path, apply_changes=True)
        assert applied["applied"] is True
        assert applied["events"]
        assert load_release(tmp_path, "v0.1.0").previous_version is None
        assert load_release(tmp_path, "v0.1.1").previous_version == "v0.1.0"
        assert check_release_chain(tmp_path)["ok"] is True


# ---------------------------------------------------------------------------
# Changelog section correction
# ---------------------------------------------------------------------------


_SAMPLE_CHANGELOG = """# Changelog

## [v0.4.3] - 2026-05-01

### Added
- feature X

## [v0.4.2] - 2026-04-01

### Fixed
- bug Y
"""


class TestChangelogSectionHelpers:
    def test_changelog_rename_section_updates_heading_once(self) -> None:
        out = rename_release_section(_SAMPLE_CHANGELOG, "v0.4.3", "v0.5.0")
        assert "## [v0.5.0] - 2026-05-01" in out
        assert "## [v0.4.3]" not in out
        assert out.count("## [v0.5.0]") == 1

    def test_changelog_rename_section_refuses_existing_destination(self) -> None:
        with pytest.raises(LaunchError) as exc_info:
            rename_release_section(_SAMPLE_CHANGELOG, "v0.4.3", "v0.4.2")
        assert exc_info.value.code == "CONFLICT"

    def test_changelog_remove_section_preserves_other_sections(self) -> None:
        out = remove_release_section(_SAMPLE_CHANGELOG, "v0.4.3")
        assert "## [v0.4.3]" not in out
        assert "## [v0.4.2]" in out
        assert "bug Y" in out

    def test_release_rename_can_rename_changelog_section(self, tmp_path: Path) -> None:
        _init(tmp_path)
        tag_release(tmp_path, version="v0.4.2", released_at="2026-04-01")
        tag_release(tmp_path, version="v0.4.3", released_at="2026-05-01")
        target = tmp_path / "CHANGELOG.md"
        target.write_text(_SAMPLE_CHANGELOG, encoding="utf-8")
        rename_release(
            tmp_path,
            old_version="v0.4.3",
            new_version="v0.5.0",
            force_released_unshipped=True,
            target_file=target,
            rename_changelog_section=True,
        )
        text = target.read_text()
        assert "## [v0.5.0]" in text and "## [v0.4.3]" not in text
        assert "## [v0.4.2]" in text

    def test_release_cancel_can_remove_unshipped_changelog_section_with_explicit_flag(
        self, tmp_path: Path
    ) -> None:
        _init(tmp_path)
        tag_release(tmp_path, version="v0.4.3", released_at="2026-05-01")
        target = tmp_path / "CHANGELOG.md"
        target.write_text(_SAMPLE_CHANGELOG, encoding="utf-8")
        cancel_release(
            tmp_path,
            version="v0.4.3",
            reason="never shipped",
            force_released_unshipped=True,
            target_file=target,
            remove_changelog_section=True,
        )
        text = target.read_text()
        assert "## [v0.4.3]" not in text
        assert "## [v0.4.2]" in text


# ---------------------------------------------------------------------------
# CLI surfaces
# ---------------------------------------------------------------------------


class TestCLICommands:
    def test_cli_cancel_command_sets_canceled(self, tmp_path: Path) -> None:
        _init(tmp_path)
        _run(tmp_path, "release", "tag", "v0.4.3", "--released-at", "2026-05-01")
        human = _run(
            tmp_path, "release", "cancel", "v0.4.3", "--force-released-unshipped"
        )
        assert human.exit_code == 0
        assert "canceled release v0.4.3" in human.stdout
        payload = _jout(_jrun(tmp_path, "release", "show", "v0.4.3"))
        assert payload["result"]["release"]["status"] == "canceled"

    def test_cli_chain_subcommands_registered(self, tmp_path: Path) -> None:
        _init(tmp_path)
        check = _run(tmp_path, "release", "chain", "check")
        assert check.exit_code == 0
        assert "CHAIN OK" in check.stdout
        repair = _run(tmp_path, "release", "chain", "repair", "--dry-run")
        assert repair.exit_code == 0

    def test_cli_changelog_section_subcommands(self, tmp_path: Path) -> None:
        _init(tmp_path)
        target = tmp_path / "CHANGELOG.md"
        target.write_text(_SAMPLE_CHANGELOG, encoding="utf-8")
        rename = _run(
            tmp_path,
            "changelog-section",
            "rename-section",
            "v0.4.3",
            "v0.5.0",
            "--target-file",
            "CHANGELOG.md",
        )
        assert rename.exit_code == 0
        assert "renamed section v0.4.3 to v0.5.0" in rename.stdout
        assert "## [v0.5.0]" in target.read_text()
        remove = _run(
            tmp_path,
            "changelog-section",
            "remove-section",
            "v0.5.0",
            "--target-file",
            "CHANGELOG.md",
        )
        assert remove.exit_code == 0
        assert "## [v0.5.0]" not in target.read_text()


# ---------------------------------------------------------------------------
# End-to-end regression fixture for the reviewed run
# ---------------------------------------------------------------------------


class TestRegressionFixture:
    def test_full_run_backfill_chain_repair_and_rename(self, tmp_path: Path) -> None:
        _init(tmp_path)
        # 1. Existing releases v0.1.1 .. v0.4.3.
        _run(tmp_path, "release", "tag", "v0.1.1", "--released-at", "2026-01-01")
        _run(
            tmp_path,
            "release",
            "tag",
            "v0.1.2",
            "--released-at",
            "2026-02-01",
            "--previous",
            "v0.1.1",
        )
        _run(
            tmp_path,
            "release",
            "tag",
            "v0.4.3",
            "--released-at",
            "2026-05-01",
            "--previous",
            "v0.1.2",
        )
        # 2. Backfill v0.1.0 after v0.4.3 with a broken (future) predecessor.
        _run(
            tmp_path,
            "release",
            "tag",
            "v0.1.0",
            "--released-at",
            "2026-01-01",
            "--previous",
            "v0.4.3",
        )
        # 3. Chain check flags the broken predecessor.
        check_payload = _jout(_jrun(tmp_path, "release", "chain", "check"))
        assert check_payload["result"]["ok"] is False
        problems = check_payload["result"]["problems"]
        kinds = [(p["kind"], p["version"]) for p in problems]
        assert ("future_previous", "v0.1.0") in kinds
        # 4. Repair the chain.
        repair_payload = _jout(_jrun(tmp_path, "release", "chain", "repair", "--apply"))
        assert repair_payload["result"]["applied"] is True
        # 5. Build a changelog with a stale v0.4.3 section and an entry.
        _run(
            tmp_path,
            "entry",
            "add",
            "v0.4.3",
            "--kind",
            "added",
            "--summary",
            "feature",
        )
        changelog = tmp_path / "CHANGELOG.md"
        changelog.write_text(_SAMPLE_CHANGELOG, encoding="utf-8")
        # 6. Rename v0.4.3 -> v0.5.0.
        rename_payload = _jout(
            _jrun(
                tmp_path,
                "release",
                "rename",
                "v0.4.3",
                "v0.5.0",
                "--previous",
                "v0.1.2",
                "--force-released-unshipped",
                "--target-file",
                "CHANGELOG.md",
                "--rename-changelog-section",
            )
        )
        assert rename_payload["result"]["release"]["version"] == "v0.5.0"
        # 7. Assertions from the review.
        # No v0.4.3 release bundle remains.
        from releaseledger.storage.paths import resolve_project_paths
        from releaseledger.storage.store import load_entries, release_dir

        paths = resolve_project_paths(tmp_path)
        assert not release_dir(paths, "v0.4.3").exists()
        # previous_version is v0.1.2 (the last shipped release before v0.5.0).
        assert load_release(tmp_path, "v0.5.0").previous_version == "v0.1.2"
        # Entries now carry release_version == v0.5.0.
        entries = load_entries(tmp_path, "v0.5.0")
        assert entries and all(e.release_version == "v0.5.0" for e in entries)
        # Changelog has one v0.5.0 section and no stale v0.4.3 section.
        text = changelog.read_text()
        assert find_release_section(text, "v0.5.0") is not None
        assert find_release_section(text, "v0.4.3") is None
        # release list sorts v0.1.0 before v0.1.1.
        order = [r.version for r in list_releases(tmp_path)]
        assert order.index("v0.1.0") < order.index("v0.1.1")
