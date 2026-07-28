"""Storage topology and migration lifecycle contract checks."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from releaseledger.cli import app
from releaseledger.migration import (
    ReleaseledgerMigrationRequest,
    load_migration_plan,
    plan_migration,
    validate_migration_plan,
)

runner = CliRunner()


def test_storage_set_dry_run_is_read_only_and_cache_is_rejected(tmp_path: Path) -> None:
    assert runner.invoke(app, ["--root", str(tmp_path), "init"]).exit_code == 0
    manifest = tmp_path / ".ledger" / "ledger.toml"
    before = manifest.read_bytes()

    preview = runner.invoke(
        app,
        [
            "--root",
            str(tmp_path),
            "--json",
            "storage",
            "set",
            "data",
            "--storage",
            "external",
            "--storage-root",
            str(tmp_path / "external"),
            "--dry-run",
        ],
    )
    assert preview.exit_code == 0, preview.stdout + preview.stderr
    assert json.loads(preview.stdout)["result"]["dry_run"] is True
    assert manifest.read_bytes() == before

    rejected = runner.invoke(
        app,
        [
            "--root",
            str(tmp_path),
            "--json",
            "storage",
            "set",
            "data",
            "--storage",
            "cache",
        ],
    )
    assert rejected.exit_code == 2
    assert json.loads(rejected.stdout)["error"]["code"] == "usage_error"


def test_storage_and_config_validate_emit_passed_state(tmp_path: Path) -> None:
    assert runner.invoke(app, ["--root", str(tmp_path), "init"]).exit_code == 0
    for command in (
        ["storage", "validate"],
        ["config", "validate"],
    ):
        result = runner.invoke(app, ["--root", str(tmp_path), "--json", *command])
        assert result.exit_code == 0, result.stdout + result.stderr
        payload = json.loads(result.stdout)
        assert payload["ok"] is True
        assert payload["result"]["passed"] is True


def test_migration_plan_is_hashed_and_stale_source_is_conflict(tmp_path: Path) -> None:
    legacy_config = tmp_path / ".releaseledger.toml"
    legacy_config.write_text(
        'config_version = 1\nreleaseledger_dir = ".releaseledger"\n',
        encoding="utf-8",
    )
    legacy_data = tmp_path / ".releaseledger" / "ledgers" / "main"
    releases = legacy_data / "releases" / "1.0.0"
    releases.mkdir(parents=True)
    (releases / "release.md").write_text(
        "---\nrelease_version: 1.0.0\nstatus: planned\n---\n",
        encoding="utf-8",
    )
    events_file = legacy_data / "events" / "events.jsonl"
    events_file.parent.mkdir()
    events_file.write_text('{"id": 1, "type": "release_created"}\n', encoding="utf-8")

    request = ReleaseledgerMigrationRequest(
        start=tmp_path,
        data_storage="project",
        external_root=None,
        target="project",
        mode="copy",
    )
    plan = plan_migration(request)
    assert plan["schema"] == "releaseledger.storage-migration-plan.v2"
    assert plan["migration_id"]
    assert plan["project"]["root"] == str(tmp_path.resolve())
    assert plan["decisions"]["config"]["target_fingerprint"]
    assert str(plan["plan_hash"]).startswith("sha256:")

    plan_file = tmp_path / "migration-plan.json"
    plan_file.write_text(json.dumps(plan, sort_keys=True), encoding="utf-8")
    assert load_migration_plan(plan_file)["plan_hash"] == plan["plan_hash"]

    events_file.write_text('{"id": 2, "type": "release_created"}\n', encoding="utf-8")
    try:
        validate_migration_plan(plan, tmp_path)
    except Exception as exc:
        assert getattr(exc, "code", None) == "migration_source_changed"
    else:  # pragma: no cover - defensive assertion for the conflict contract
        raise AssertionError("stale migration plan was accepted")


def test_migration_cleanup_requires_confirmation_and_reason(tmp_path: Path) -> None:
    assert runner.invoke(app, ["--root", str(tmp_path), "init"]).exit_code == 0
    (tmp_path / ".releaseledger.toml").write_text(
        'config_version = 1\nreleaseledger_dir = ".releaseledger"\n',
        encoding="utf-8",
    )
    (tmp_path / ".releaseledger").mkdir()

    preview = runner.invoke(
        app,
        ["--root", str(tmp_path), "--json", "migrate", "cleanup", "--dry-run"],
    )
    assert preview.exit_code == 0, preview.stdout + preview.stderr
    assert json.loads(preview.stdout)["result"]["dry_run"] is True

    missing_reason = runner.invoke(
        app,
        ["--root", str(tmp_path), "--json", "migrate", "cleanup", "--yes"],
    )
    assert missing_reason.exit_code == 2
    assert json.loads(missing_reason.stdout)["error"]["code"] == "usage_error"

    removed = runner.invoke(
        app,
        [
            "--root",
            str(tmp_path),
            "--json",
            "migrate",
            "cleanup",
            "--yes",
            "--reason",
            "Remove verified legacy data after migration",
        ],
    )
    assert removed.exit_code == 0, removed.stdout + removed.stderr
    assert not (tmp_path / ".releaseledger.toml").exists()
    assert not (tmp_path / ".releaseledger").exists()
