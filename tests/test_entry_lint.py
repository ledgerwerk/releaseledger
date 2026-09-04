from __future__ import annotations

from releaseledger.domain.entry import ReleaseEntryRecord
from releaseledger.services.entry_lint import lint_entry_records, validate_entry_record


def _entry(kind: str, summary: str) -> ReleaseEntryRecord:
    return ReleaseEntryRecord(
        entry_id="entry-0001",
        release_version="1.0.0",
        kind=kind,
        summary=summary,
    )


def test_changed_entry_rejects_added_action_in_strict_lint() -> None:
    result = lint_entry_records(
        [_entry("changed", "Added a feature from reviewed behavior")], strict=True
    )
    mismatch = next(
        issue for issue in result["issues"] if issue["code"] == "kind_action_mismatch"
    )
    assert mismatch["severity"] == "warning"
    assert mismatch["actual_action"] == "Added"
    assert result["passed"] is False


def test_changed_entry_accepts_changed_and_improved_actions() -> None:
    for summary in ("Changed the feature behavior", "Improved the feature behavior"):
        issues = validate_entry_record(_entry("changed", summary))
        assert not any(issue["code"] == "kind_action_mismatch" for issue in issues)


def test_summary_helper_remains_kind_agnostic() -> None:
    result = lint_entry_records(
        [_entry("changed", "Added a feature from reviewed behavior")], strict=False
    )
    assert any(issue["code"] == "kind_action_mismatch" for issue in result["issues"])
