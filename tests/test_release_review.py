"""Acceptance tests for ``releaseledger review``.

Covers the review command named in ``releaseledger_change_tracking_review.md``:
coverage classification (covered / missing / draft_only / internal_only),
orphan detection, deterministic JSON, stable human output, strict failure,
read-only behavior, and boundary_ref handling. Mirrors the ``CliRunner`` +
isolated ``tmp_path`` style of ``tests/test_cli.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from releaseledger.cli import app

runner = CliRunner()


def _json(result) -> dict[str, object]:
    assert result.exit_code == 0, result.stdout
    return json.loads(result.stdout)


def _init(tmp_path: Path) -> None:
    result = runner.invoke(app, ["--cwd", str(tmp_path), "init"])
    assert result.exit_code == 0, result.stdout


def _run(tmp_path: Path, *cmd: str):
    return runner.invoke(app, ["--cwd", str(tmp_path), *cmd])


def _jrun(tmp_path: Path, *cmd: str):
    return runner.invoke(app, ["--cwd", str(tmp_path), "--json", *cmd])


def _add_entry(
    tmp_path: Path,
    version: str,
    *,
    kind: str = "added",
    summary: str = "Added X",
    status: str = "accepted",
    source_ref: str | None = None,
    source: str | None = None,
    internal: bool = False,
) -> None:
    cmd = [
        "entry",
        "add",
        version,
        "--kind",
        kind,
        "--summary",
        summary,
        "--status",
        status,
    ]
    if source_ref:
        cmd += ["--source-ref", source_ref]
    if source:
        cmd += ["--source", source]
    if internal:
        cmd += ["--internal"]
    result = _run(tmp_path, *cmd)
    assert result.exit_code == 0, result.stdout


def _create_release(
    tmp_path: Path,
    version: str,
    *,
    source_refs: tuple[str, ...] = (),
    boundary_ref: str | None = None,
) -> None:
    cmd = ["release", "create", version]
    for ref in source_refs:
        cmd += ["--source-ref", ref]
    if boundary_ref:
        cmd += ["--boundary-ref", boundary_ref]
    result = _run(tmp_path, *cmd)
    assert result.exit_code == 0, result.stdout


# ---------------------------------------------------------------------------
# 1. covered
# ---------------------------------------------------------------------------


def test_review_reports_covered_source_refs(tmp_path: Path) -> None:
    _init(tmp_path)
    _create_release(
        tmp_path, "0.5.0", source_refs=("tl:task-0103",), boundary_ref="tl:task-0105"
    )
    _add_entry(
        tmp_path, "0.5.0", summary="Added coverage report", source_ref="tl:task-0103"
    )
    _add_entry(
        tmp_path, "0.5.0", summary="Added boundary work", source_ref="tl:task-0105"
    )
    payload = _json(_jrun(tmp_path, "review", "0.5.0"))
    coverage = payload["result"]["coverage"]
    by_ref = {row["source_ref"]: row for row in coverage}
    assert by_ref["tl:task-0103"]["status"] == "covered"
    assert by_ref["tl:task-0103"]["accepted_entry_ids"] == ["entry-0001"]
    assert by_ref["tl:task-0105"]["status"] == "covered"
    assert payload["result"]["ok"] is True


# ---------------------------------------------------------------------------
# 2. missing
# ---------------------------------------------------------------------------


def test_review_reports_missing_source_refs(tmp_path: Path) -> None:
    _init(tmp_path)
    _create_release(tmp_path, "0.5.0", source_refs=("tl:task-0103", "tl:task-0104"))
    payload = _json(_jrun(tmp_path, "review", "0.5.0"))
    coverage = payload["result"]["coverage"]
    statuses = {row["source_ref"]: row["status"] for row in coverage}
    assert statuses == {"tl:task-0103": "missing", "tl:task-0104": "missing"}
    assert payload["result"]["ok"] is False
    recs = payload["result"]["recommendations"]
    assert any("tl:task-0103" in r for r in recs)


# ---------------------------------------------------------------------------
# 3. draft_only
# ---------------------------------------------------------------------------


def test_review_reports_draft_only_source_refs(tmp_path: Path) -> None:
    _init(tmp_path)
    _create_release(tmp_path, "0.5.0", source_refs=("tl:task-0103",))
    _add_entry(
        tmp_path,
        "0.5.0",
        summary="Draft coverage report",
        status="draft",
        source_ref="tl:task-0103",
    )
    payload = _json(_jrun(tmp_path, "review", "0.5.0"))
    row = payload["result"]["coverage"][0]
    assert row["source_ref"] == "tl:task-0103"
    assert row["status"] == "draft_only"
    assert row["draft_entry_ids"] == ["entry-0001"]
    assert row["accepted_entry_ids"] == []
    assert payload["result"]["ok"] is False


# ---------------------------------------------------------------------------
# 4. internal_only (hidden by default)
# ---------------------------------------------------------------------------


def test_review_reports_internal_only_when_internal_hidden(tmp_path: Path) -> None:
    _init(tmp_path)
    _create_release(tmp_path, "0.5.0", source_refs=("tl:task-0103",))
    _add_entry(
        tmp_path,
        "0.5.0",
        summary="Internal coverage only",
        source_ref="tl:task-0103",
        internal=True,
    )
    payload = _json(_jrun(tmp_path, "review", "0.5.0"))
    row = payload["result"]["coverage"][0]
    assert row["source_ref"] == "tl:task-0103"
    assert row["status"] == "internal_only"
    assert payload["result"]["ok"] is False
    # Flipping include_internal makes the same entry visible/covered.
    payload_with_internal = _json(
        _jrun(tmp_path, "review", "0.5.0", "--include-internal")
    )
    row_internal = payload_with_internal["result"]["coverage"][0]
    assert row_internal["status"] == "covered"


# ---------------------------------------------------------------------------
# 5. orphan accepted entries
# ---------------------------------------------------------------------------


def test_review_reports_orphan_accepted_entries(tmp_path: Path) -> None:
    _init(tmp_path)
    _create_release(tmp_path, "0.5.0", source_refs=("tl:task-0103",))
    _add_entry(tmp_path, "0.5.0", summary="Covered entry", source_ref="tl:task-0103")
    _add_entry(tmp_path, "0.5.0", summary="No provenance at all")
    payload = _json(_jrun(tmp_path, "review", "0.5.0"))
    orphans = payload["result"]["orphan_entries"]
    assert len(orphans) == 1
    assert orphans[0]["entry_id"] == "entry-0002"
    assert orphans[0]["status"] == "accepted"
    assert "no source_refs" in orphans[0]["reason"]
    # An entry with a free-form source is NOT an orphan.
    recs = payload["result"]["recommendations"]
    assert any("entry-0002" in r for r in recs)


# ---------------------------------------------------------------------------
# 6. deterministic JSON
# ---------------------------------------------------------------------------


def test_review_json_is_deterministic(tmp_path: Path) -> None:
    _init(tmp_path)
    _create_release(tmp_path, "0.5.0", source_refs=("tl:task-0103", "tl:task-0104"))
    _add_entry(
        tmp_path, "0.5.0", summary="Covered entry one", source_ref="tl:task-0103"
    )
    _add_entry(
        tmp_path,
        "0.5.0",
        summary="Orphan entry two",
    )
    first = _jrun(tmp_path, "review", "0.5.0").stdout
    second = _jrun(tmp_path, "review", "0.5.0").stdout
    assert first == second


# ---------------------------------------------------------------------------
# 7. stable human output
# ---------------------------------------------------------------------------


def test_review_human_output_is_stable(tmp_path: Path) -> None:
    _init(tmp_path)
    _create_release(tmp_path, "0.5.0", source_refs=("tl:task-0103",))
    _add_entry(tmp_path, "0.5.0", summary="Covered entry", source_ref="tl:task-0103")
    first = _run(tmp_path, "review", "0.5.0").stdout
    second = _run(tmp_path, "review", "0.5.0").stdout
    assert first == second
    assert "RELEASE REVIEW 0.5.0" in first
    assert "Release:" in first
    assert "Coverage:" in first
    assert "covered" in first
    assert "tl:task-0103" in first
    assert "Entries:" in first
    assert "Result: OK" in first


# ---------------------------------------------------------------------------
# 8. strict fails when build would fail
# ---------------------------------------------------------------------------


def test_review_strict_fails_when_build_would_fail(tmp_path: Path) -> None:
    _init(tmp_path)
    _create_release(tmp_path, "0.5.0", source_refs=("tl:task-0103",))
    # No entry covers tl:task-0103, so a strict build would fail.
    result = _jrun(tmp_path, "review", "0.5.0", "--strict")
    assert result.exit_code != 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["command"] == "review"
    assert payload["result_type"] == "release_review"
    # A covered but still-planned release should still fail strict state checks.
    update = _run(tmp_path, "release", "update", "0.5.0", "--released-at", "2026-06-14")
    assert update.exit_code == 0, update.stdout
    _add_entry(tmp_path, "0.5.0", summary="Covered entry", source_ref="tl:task-0103")
    still_planned = _jrun(tmp_path, "review", "0.5.0", "--strict")
    assert still_planned.exit_code != 0, still_planned.stdout
    _run(tmp_path, "release", "finalize", "0.5.0", "--released-at", "2026-06-14")
    passing = _jrun(tmp_path, "review", "0.5.0", "--strict")
    assert passing.exit_code == 0, passing.stdout
    assert json.loads(passing.stdout)["ok"] is True


# ---------------------------------------------------------------------------
# 9. does not write changelog
# ---------------------------------------------------------------------------


def test_review_does_not_write_changelog(tmp_path: Path) -> None:
    _init(tmp_path)
    _create_release(tmp_path, "0.5.0", source_refs=("tl:task-0103",))
    _add_entry(tmp_path, "0.5.0", summary="Covered entry", source_ref="tl:task-0103")
    config_path = tmp_path / ".ledger" / "releaseledger" / "config.toml"
    toml_before = config_path.read_text()
    _run(tmp_path, "review", "0.5.0", "--strict", "--target-file", "CHANGELOG.md")
    assert not (tmp_path / "CHANGELOG.md").exists()
    assert config_path.read_text() == toml_before


# ---------------------------------------------------------------------------
# 10. boundary_ref used as expected ref
# ---------------------------------------------------------------------------


def test_review_uses_boundary_ref_as_expected_ref(tmp_path: Path) -> None:
    _init(tmp_path)
    _create_release(
        tmp_path,
        "0.5.0",
        source_refs=("tl:task-0103",),
        boundary_ref="tl:task-0105",
    )
    _add_entry(tmp_path, "0.5.0", summary="Covered source", source_ref="tl:task-0103")
    payload = _json(_jrun(tmp_path, "review", "0.5.0"))
    refs = [row["source_ref"] for row in payload["result"]["coverage"]]
    assert refs == ["tl:task-0103", "tl:task-0105"]
    by_ref = {row["source_ref"]: row for row in payload["result"]["coverage"]}
    assert by_ref["tl:task-0103"]["status"] == "covered"
    assert by_ref["tl:task-0105"]["status"] == "missing"
    assert payload["result"]["ok"] is False
    # boundary_ref is surfaced in the release block too.
    assert payload["result"]["release"]["boundary_ref"] == "tl:task-0105"


def test_release_check_reports_planned_dated_release_inconsistency(
    tmp_path: Path,
) -> None:
    _init(tmp_path)
    _create_release(tmp_path, "0.5.0", source_refs=("tl:task-0103",))
    _run(tmp_path, "release", "update", "0.5.0", "--released-at", "2026-06-14")
    _add_entry(tmp_path, "0.5.0", summary="Covered entry", source_ref="tl:task-0103")
    result = _jrun(tmp_path, "release", "check", "0.5.0", "--strict")
    assert result.exit_code != 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["result"]["checks"]["release_state_ok"] is False


def test_release_check_passes_after_finalize(tmp_path: Path) -> None:
    _init(tmp_path)
    _create_release(tmp_path, "0.5.0", source_refs=("tl:task-0103",))
    _add_entry(tmp_path, "0.5.0", summary="Covered entry", source_ref="tl:task-0103")
    _run(tmp_path, "release", "finalize", "0.5.0", "--released-at", "2026-06-14")
    result = _jrun(tmp_path, "release", "check", "0.5.0", "--strict")
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
