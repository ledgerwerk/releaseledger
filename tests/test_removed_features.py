from __future__ import annotations

import json
from pathlib import Path

import ledgercore
import pytest
from typer.testing import CliRunner

from releaseledger.cli import app
from releaseledger.domain.entry import entry_from_dict
from releaseledger.domain.release import release_from_dict
from releaseledger.errors import LaunchError
from releaseledger.services.entries import add_many_release_entries
from releaseledger.services.entry_lint import (
    lint_release_entries,
    validate_entry_summary,
)
from releaseledger.storage.paths import initialize_project
from releaseledger.storage.store import rebuild_indexes, save_entry, save_release

runner = CliRunner()


def _run(tmp_path: Path, *args: str):
    return runner.invoke(app, ["--cwd", str(tmp_path), *args])


def _json_run(tmp_path: Path, *args: str) -> dict[str, object]:
    result = runner.invoke(app, ["--cwd", str(tmp_path), "--json", *args])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_entry_defaults_and_kind_alias() -> None:
    record = entry_from_dict(
        {
            "schema_version": 2,
            "versioning": {"schema_version": 1, "revision": 1},
            "object_type": "release_entry",
            "entry_id": "entry-0001",
            "release_version": "1.0.0",
            "kind": "documentation",
            "summary": "Documented release workflow",
        }
    )

    assert record.kind == "docs"
    assert record.status == "accepted"
    assert record.versioning.revision == 1
    assert record.audience is None
    assert record.scopes == ()
    assert record.source_refs == ()


def test_entry_accepts_quality_and_canonicalizes_source_refs() -> None:
    record = entry_from_dict(
        {
            "schema_version": 2,
            "versioning": {"schema_version": 1, "revision": 1},
            "object_type": "release_entry",
            "entry_id": "entry-0001",
            "release_version": "1.0.0",
            "kind": "quality",
            "summary": "Improved release checks",
            "status": "draft",
            "audience": "developer",
            "scopes": ["cli", "cli", "storage"],
            "source_refs": ["TL-TASK-0103"],
        }
    )

    assert record.kind == "quality"
    assert record.status == "draft"
    assert record.scopes == ("cli", "storage")
    assert record.source_refs == ("tl:task-0103",)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", "pending"),
        ("source_refs", ["task-0001"]),
    ],
)
def test_entry_rejects_invalid_new_metadata(field: str, value: object) -> None:
    data: dict[str, object] = {
        "schema_version": 2,
        "versioning": {"schema_version": 1, "revision": 1},
        "object_type": "release_entry",
        "entry_id": "entry-0001",
        "release_version": "1.0.0",
        "kind": "changed",
        "summary": "Changed release workflow",
        field: value,
    }

    with pytest.raises(LaunchError):
        entry_from_dict(data)


def test_legacy_release_defaults_source_metadata() -> None:
    record = release_from_dict(
        {
            "schema_version": 2,
            "versioning": {"schema_version": 1, "revision": 1},
            "object_type": "release",
            "version": "1.0.0",
            "status": "planned",
        }
    )

    assert record.boundary_ref is None
    assert record.source_refs == ()
    assert record.source_count is None


def test_indexes_include_new_source_and_status_fields(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    release = release_from_dict(
        {
            "schema_version": 2,
            "versioning": {"schema_version": 1, "revision": 1},
            "object_type": "release",
            "version": "1.0.0",
            "status": "planned",
            "boundary_ref": "tl:task-0105",
            "source_refs": ["tl:task-0103"],
            "source_count": 3,
        }
    )
    save_release(tmp_path, release)
    entry = entry_from_dict(
        {
            "schema_version": 2,
            "versioning": {"schema_version": 1, "revision": 1},
            "object_type": "release_entry",
            "entry_id": "entry-0001",
            "release_version": "1.0.0",
            "kind": "quality",
            "summary": "Improved release checks",
            "status": "draft",
            "audience": "developer",
            "scopes": ["cli"],
            "source_refs": ["tl:task-0103"],
        }
    )
    save_entry(tmp_path, entry)

    rebuild_indexes(tmp_path)

    from releaseledger.storage.paths import resolve_project_paths

    paths = resolve_project_paths(tmp_path)
    releases = json.loads(paths.releases_index_path.read_text())
    entries = json.loads(paths.entries_index_path.read_text())
    assert releases[0]["boundary_ref"] == "tl:task-0105"
    assert releases[0]["source_refs"] == ["tl:task-0103"]
    assert releases[0]["source_count"] == 3
    assert entries[0]["status"] == "draft"
    assert entries[0]["audience"] == "developer"
    assert entries[0]["scopes"] == ["cli"]
    assert entries[0]["source_refs"] == ["tl:task-0103"]


def test_release_source_metadata_create_and_update(tmp_path: Path) -> None:
    assert _run(tmp_path, "init").exit_code == 0
    payload = _json_run(
        tmp_path,
        "release",
        "create",
        "1.0.0",
        "--boundary-ref",
        "tl:task-0105",
        "--source-ref",
        "tl:task-0103",
        "--source-count",
        "3",
    )
    release = payload["result"]["release"]
    assert release["boundary_ref"] == "tl:task-0105"
    assert release["source_refs"] == ["tl:task-0103"]
    assert release["source_count"] == 3

    updated = _json_run(
        tmp_path,
        "release",
        "update",
        "1.0.0",
        "--source-ref",
        "tl:task-0104",
    )
    assert updated["result"]["release"]["source_refs"] == ["tl:task-0104"]


def test_entry_add_preview_show_and_update(tmp_path: Path) -> None:
    assert _run(tmp_path, "init").exit_code == 0
    assert _run(tmp_path, "release", "create", "1.0.0").exit_code == 0
    preview = _json_run(
        tmp_path,
        "entry",
        "add",
        "1.0.0",
        "--kind",
        "documentation",
        "--summary",
        "Documented release workflow",
        "--status",
        "draft",
        "--audience",
        "developer",
        "--scope",
        "cli",
        "--source-ref",
        "tl:task-0103",
        "--dry-run",
    )
    assert preview["result"]["written"] is False
    assert preview["result"]["entry"]["entry_id"] == "entry-0001"
    assert not list(
        (
            tmp_path
            / ".releaseledger"
            / "ledgers"
            / "main"
            / "releases"
            / "1.0.0"
            / "entries"
        ).glob("*.md")
    )

    added = _json_run(
        tmp_path,
        "entry",
        "add",
        "1.0.0",
        "--kind",
        "quality",
        "--summary",
        "Improved release checks",
        "--source-ref",
        "tl:task-0103",
    )
    assert added["result"]["entry"]["entry_id"] == "entry-0001"
    shown = _json_run(tmp_path, "entry", "show", "1.0.0", "entry-0001")
    assert shown["result"]["entry"]["kind"] == "quality"
    updated = _json_run(
        tmp_path,
        "entry",
        "update",
        "1.0.0",
        "entry-0001",
        "--status",
        "draft",
    )
    assert updated["result"]["entry"]["status"] == "draft"
    assert updated["result"]["entry"]["versioning"]["revision"] == 2


def test_entry_batch_is_atomic_and_deterministic(tmp_path: Path) -> None:
    assert _run(tmp_path, "init").exit_code == 0
    assert _run(tmp_path, "release", "create", "1.0.0").exit_code == 0
    batch = tmp_path / "entries.yaml"
    batch.write_text(
        "entries:\n"
        "  - kind: changed\n"
        "    summary: Changed release workflow\n"
        "  - kind: quality\n"
        "    summary: Improved release checks\n"
    )
    preview = _json_run(
        tmp_path, "entry", "add-many", "1.0.0", "--file", str(batch), "--dry-run"
    )
    assert preview["result"]["entry_ids"] == ["entry-0001", "entry-0002"]
    assert preview["result"]["written"] is False
    added = _json_run(tmp_path, "entry", "add-many", "1.0.0", "--file", str(batch))
    assert added["result"]["entry_ids"] == ["entry-0001", "entry-0002"]
    assert len(added["result"]["events"]) == 1

    invalid = tmp_path / "invalid.yaml"
    invalid.write_text(
        "entries:\n"
        "  - kind: fixed\n"
        "    summary: Fixed valid item\n"
        "  - kind: unknown\n"
        "    summary: Changed invalid item\n"
    )
    failed = _run(tmp_path, "entry", "add-many", "1.0.0", "--file", str(invalid))
    assert failed.exit_code != 0
    listed = _json_run(tmp_path, "entry", "list", "1.0.0")
    assert len(listed["result"]["entries"]) == 2


def test_entry_batch_returns_all_structured_issues(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    save_release(
        tmp_path,
        release_from_dict(
            {
                "schema_version": 2,
                "versioning": {"schema_version": 1, "revision": 1},
                "object_type": "release",
                "version": "1.0.0",
                "status": "planned",
            }
        ),
    )
    result = add_many_release_entries(
        tmp_path,
        release_version="1.0.0",
        entries=[
            {"kind": "unknown", "summary": "Changed invalid kind"},
            {
                "kind": "changed",
                "summary": "Changed invalid ref",
                "source_refs": ["task-0001"],
            },
        ],
    )
    assert result["written"] is False
    assert result["entry_ids"] == []
    assert len(result["issues"]) == 3
    assert result["issues"][0]["index"] == 0
    assert result["issues"][0]["entry_id"] == "entry-0001"
    assert result["issues"][1]["index"] == 1
    assert result["issues"][1]["entry_id"] == "entry-0002"
    assert result["issues"][2]["severity"] == "warning"
    assert result["issues"][2]["code"] == "no_accepted_entries"
    assert result["issues"][2]["field"] == "status"
    assert result["issues"][2]["message"] == "Release has no accepted entries."
    assert all(
        {"index", "entry_id", "field", "severity", "message"} <= issue.keys()
        for issue in result["issues"][:2]
    )


def test_import_legacy_entry_requires_source_ledger(tmp_path: Path) -> None:
    assert _run(tmp_path, "init").exit_code == 0
    assert _run(tmp_path, "release", "create", "1.0.0").exit_code == 0
    source = tmp_path / "legacy.md"
    ledgercore.write_front_matter_document(
        source,
        {
            "object_type": "changelog_entry",
            "entry_id": "entry-0007",
            "category": "documentation",
            "summary": "Documented legacy import",
            "task_id": "task-0103",
            "source_run_id": "run-0007",
        },
        body="Legacy details",
    )
    failed = _run(tmp_path, "entry", "import", "1.0.0", "--file", str(source))
    assert failed.exit_code != 0
    imported = _json_run(
        tmp_path,
        "entry",
        "import",
        "1.0.0",
        "--file",
        str(source),
        "--source-ledger",
        "tl",
    )
    assert imported["result"]["entry"]["kind"] == "docs"
    assert imported["result"]["entry"]["source_refs"] == [
        "tl:task-0103",
        "tl:run-0007",
    ]


def test_summary_lint_rules() -> None:
    errors = validate_entry_summary("# TODO [ ] " + ("x" * 181))
    codes = {issue["code"] for issue in errors}
    assert {"heading", "todo", "unchecked", "too_long", "action_prefix"} <= codes
    warnings = validate_entry_summary("Changed task-0001 behavior.")
    assert {issue["code"] for issue in warnings} == {
        "raw_task_id",
        "trailing_period",
    }
    assert validate_entry_summary("Improved release entry linting") == []


def test_entry_lint_strict_fails_on_warnings(tmp_path: Path) -> None:
    assert _run(tmp_path, "init").exit_code == 0
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
    normal = _json_run(tmp_path, "entry", "lint", "1.0.0")
    assert normal["result"]["summary"] == {"errors": 0, "warnings": 1}
    strict = _run(tmp_path, "entry", "lint", "1.0.0", "--strict")
    assert strict.exit_code != 0


def test_entry_lint_warns_without_accepted_entries(tmp_path: Path) -> None:
    assert _run(tmp_path, "init").exit_code == 0
    assert _run(tmp_path, "release", "create", "1.0.0").exit_code == 0
    assert (
        _run(
            tmp_path,
            "entry",
            "add",
            "1.0.0",
            "--summary",
            "Added draft release entry",
            "--status",
            "draft",
        ).exit_code
        == 0
    )
    payload = _json_run(tmp_path, "entry", "lint", "1.0.0")
    assert any(
        issue["code"] == "no_accepted_entries" for issue in payload["result"]["issues"]
    )


def test_entry_lint_reports_malformed_entry_files(tmp_path: Path) -> None:
    assert _run(tmp_path, "init").exit_code == 0
    assert _run(tmp_path, "release", "create", "1.0.0").exit_code == 0
    from releaseledger.storage.paths import resolve_project_paths

    rpaths = resolve_project_paths(tmp_path)
    entries_dir = rpaths.releases_dir / "1.0.0" / "entries"
    base = {
        "schema_version": 2,
        "versioning": {"schema_version": 1, "revision": 1},
        "object_type": "release_entry",
        "release_version": "1.0.0",
        "kind": "changed",
        "summary": "Changed malformed fixture",
    }
    invalid_records = [
        ("entry-0001", {"schema_version": 99}),
        ("entry-0002", {"status": "pending"}),
        ("entry-0003", {"kind": "unknown"}),
        ("entry-0004", {"source_refs": ["task-0001"]}),
        ("entry-0005", {"paths": ["../escape"]}),
    ]
    for entry_id, override in invalid_records:
        metadata = {**base, "entry_id": entry_id, **override}
        ledgercore.write_front_matter_document(
            entries_dir / f"{entry_id}.md", metadata, body=""
        )

    payload = lint_release_entries(tmp_path, release_version="1.0.0")
    fields = {issue["field"] for issue in payload["issues"]}
    assert {
        "schema_version",
        "status",
        "kind",
        "source_refs",
        "paths",
    } <= fields
    strict = _run(tmp_path, "entry", "lint", "1.0.0", "--strict")
    assert strict.exit_code != 0


def test_entry_prompt_includes_opaque_context_and_workflow(tmp_path: Path) -> None:
    assert _run(tmp_path, "init").exit_code == 0
    assert (
        _run(
            tmp_path,
            "release",
            "create",
            "1.0.0",
            "--boundary-ref",
            "tl:task-0105",
        ).exit_code
        == 0
    )
    context = tmp_path / "context.json"
    context.write_text('{"validation": "passed"}')
    result = _run(
        tmp_path,
        "entry",
        "prompt",
        "1.0.0",
        "--source-ref",
        "tl:task-0103",
        "--context-file",
        str(context),
    )
    assert result.exit_code == 0, result.output
    assert '{"validation": "passed"}' in result.output
    assert "source_refs: [tl:task-0103]" in result.output
    assert "entry add-many 1.0.0" in result.output
    assert ".taskledger/" not in result.output

    json_result = _run(
        tmp_path,
        "entry",
        "prompt",
        "1.0.0",
        "--source-ref",
        "tl:task-0103",
        "--format",
        "json",
    )
    assert json_result.exit_code == 0
    payload = json.loads(json_result.output)
    assert payload["source_refs"] == ["tl:task-0103"]


def test_changelog_and_build_filter_statuses_and_render_quality(
    tmp_path: Path,
) -> None:
    assert _run(tmp_path, "init").exit_code == 0
    assert _run(tmp_path, "release", "create", "1.0.0").exit_code == 0
    assert (
        _run(
            tmp_path,
            "entry",
            "add",
            "1.0.0",
            "--kind",
            "quality",
            "--summary",
            "Improved accepted checks",
        ).exit_code
        == 0
    )
    assert (
        _run(
            tmp_path,
            "entry",
            "add",
            "1.0.0",
            "--kind",
            "changed",
            "--summary",
            "Changed draft behavior",
            "--status",
            "draft",
        ).exit_code
        == 0
    )
    context = _json_run(tmp_path, "changelog", "1.0.0", "--format", "json")
    assert context["entry_count"] == 1
    assert context["status_counts"] == {
        "accepted": 1,
        "draft": 1,
        "rejected": 0,
    }
    assert context["filtered_counts"]["draft"] == 1

    default_build = _json_run(
        tmp_path, "build", "1.0.0", "--dry-run", "--format", "json"
    )
    section = default_build["result"]["section"]
    # In extended mode (default), quality entries have their own heading
    assert "### Quality" in section
    assert "Improved accepted checks" in section
    assert "Changed draft behavior" not in section
    draft_build = _json_run(
        tmp_path,
        "build",
        "1.0.0",
        "--dry-run",
        "--format",
        "json",
        "--include-status",
        "accepted",
        "--include-status",
        "draft",
    )
    assert "Changed draft behavior" in draft_build["result"]["section"]
    assert any("draft-quality" in item for item in draft_build["result"]["warnings"])


def test_strict_build_empty_and_source_coverage_gates(tmp_path: Path) -> None:
    assert _run(tmp_path, "init").exit_code == 0
    assert (
        _run(
            tmp_path,
            "release",
            "create",
            "1.0.0",
            "--source-ref",
            "tl:task-0103",
        ).exit_code
        == 0
    )
    empty = _run(tmp_path, "build", "1.0.0", "--dry-run", "--strict")
    assert empty.exit_code != 0
    allowed = _run(
        tmp_path,
        "build",
        "1.0.0",
        "--dry-run",
        "--strict",
        "--allow-empty",
        "--release-date",
        "2026-06-14",
    )
    assert allowed.exit_code == 0, allowed.output

    assert (
        _run(
            tmp_path,
            "entry",
            "add",
            "1.0.0",
            "--summary",
            "Added release entry",
        ).exit_code
        == 0
    )
    uncovered = _run(tmp_path, "build", "1.0.0", "--dry-run", "--strict")
    assert uncovered.exit_code != 0
    assert (
        _run(
            tmp_path,
            "entry",
            "update",
            "1.0.0",
            "entry-0001",
            "--source-ref",
            "tl:task-0103",
        ).exit_code
        == 0
    )
    covered = _run(
        tmp_path,
        "build",
        "1.0.0",
        "--dry-run",
        "--strict",
        "--release-date",
        "2026-06-14",
    )
    assert covered.exit_code == 0, covered.output
