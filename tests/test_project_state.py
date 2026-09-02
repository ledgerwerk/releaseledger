"""Project discovery and diagnostics behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from releaseledger.errors import LaunchError
from releaseledger.services import project_state


def _invalid_canonical_storage() -> dict[str, object]:
    return {
        "project_root": "/workspace",
        "project_uuid": "project-uuid",
        "manifest_path": "/workspace/.ledger/ledger.toml",
        "migration_state": "canonical-invalid",
        "discovered": True,
        "canonical": True,
        "layout_valid": False,
        "active_ledger_ref": "main",
    }


def test_project_status_discovered_invalid_layout_does_not_recommend_init(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        project_state, "storage_where", lambda _root: _invalid_canonical_storage()
    )
    monkeypatch.setattr(project_state, "_records", lambda _root: [])

    result = project_state.project_status(tmp_path)

    assert result["discovered"] is True
    assert result["canonical"] is True
    assert result["layout_valid"] is False
    assert result["initialized"] is True
    assert result["next_action"] == {
        "command": "storage validate --strict",
        "reason": "The canonical project was discovered but its storage layout is invalid.",
    }


def test_project_doctor_reports_release_record_parse_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        project_state, "storage_where", lambda _root: _invalid_canonical_storage()
    )

    def fail_records(_root: Path) -> list[dict[str, object]]:
        raise LaunchError("malformed release record")

    monkeypatch.setattr(project_state, "list_release_records", fail_records)
    result = project_state.project_doctor(tmp_path)
    checks = {str(item["code"]): item for item in result["checks"]}

    assert checks["project_discovery"]["status"] == "pass"
    assert checks["storage_layout"]["status"] == "fail"
    assert checks["release_records"]["status"] == "fail"
    assert "malformed release record" in str(checks["release_records"]["message"])
    assert result["ok"] is False


def test_next_action_uses_storage_validation_for_discovered_invalid_project(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        project_state, "storage_where", lambda _root: _invalid_canonical_storage()
    )

    result = project_state.next_action(tmp_path)

    assert result["command"] == "storage validate --strict"
