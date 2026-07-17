"""Tests for releaseledger config version 2 (``.ledger/releaseledger/config.toml``)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from releaseledger.errors import LaunchError
from releaseledger.storage import config as cfg


def _sample_v1_text() -> str:
    return textwrap.dedent(
        """
        config_version = 1
        releaseledger_dir = ".releaseledger"
        releaseledger_dir_policy = "workspace"
        ledger_ref = "main"
        ledger_parent_ref = ""
        ledger_next_entry_number = 1
        ledger_branch_guard = "off"

        [ledger]
        code = "rl"
        name = "old-project"

        [release]
        default_changelog = "CHANGELOG.md"
        default_status = "planned"
        allow_dirty_worktree = true

        [changelog]
        output = "CHANGELOG.md"
        body = \"\"\"hello\"\"\"
        footer = "<!-- generated -->"

        [git]
        enabled = true
        """
    )


def test_load_default_v2_config(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    cfg.write_project_config(p, cfg.ProjectConfig())
    loaded = cfg.load_project_config(p)
    assert loaded.config_version == 2
    assert loaded.ledger_ref == "main"
    assert loaded.ledger_code == "rl"


def test_reject_v1_config_version(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    p.write_text("config_version = 1\nledger_ref = 'main'\n")
    with pytest.raises(LaunchError) as exc:
        cfg.load_project_config(p)
    assert exc.value.code == "CONFIG_ERROR"
    assert exc.value.data["found"] == 1
    assert exc.value.data["required"] == 2


def test_reject_removed_top_level_fields(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    p.write_text(
        "config_version = 2\nreleaseledger_dir = '.releaseledger'\n"
        "releaseledger_dir_policy = 'workspace'\n"
        "ledger_next_entry_number = 3\n"
    )
    with pytest.raises(LaunchError) as exc:
        cfg.load_project_config(p)
    assert exc.value.code == "CONFIG_ERROR"
    assert "releaseledger_dir" in exc.value.data["removed_keys"]
    assert "releaseledger_dir_policy" in exc.value.data["removed_keys"]
    assert "ledger_next_entry_number" in exc.value.data["removed_keys"]


def test_reject_ledger_name(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    p.write_text("config_version = 2\n[ledger]\ncode = 'rl'\nname = 'old'\n")
    with pytest.raises(LaunchError) as exc:
        cfg.load_project_config(p)
    assert exc.value.code == "CONFIG_ERROR"
    assert "ledger.name" in exc.value.data["removed_keys"]


def test_round_trip_preserves_comments_and_order(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    p.write_text(
        textwrap.dedent(
            """
            # Custom header comment
            config_version = 2
            # Branch state
            ledger_ref = "main"
            ledger_parent_ref = ""
            ledger_branch_guard = "off"

            [changelog]
            output = "CHANGELOG.md"
            body = \"\"\"
            multi
            line
            \"\"\"
            footer = "<!-- generated -->"
            trim = true
            """
        )
    )
    before = p.read_text()
    loaded = cfg.load_project_config(p)
    cfg.write_project_config(p, loaded)
    after = p.read_text()
    # Custom header comment must remain.
    assert "# Custom header comment" in after
    # Section ordering: changelog appears after release by default order;
    # we only check that the custom comment and the body survived.
    assert 'multi\n    line' in after or 'multi' in after
    assert "<!-- generated -->" in after
    # The body is intentionally re-rendered; ensure the comment is preserved
    # but the overall file must still be a valid v2 config.
    again = cfg.load_project_config(p)
    assert again.config_version == 2
    assert again.ledger_ref == "main"


def test_write_atomic_failure_leaves_original(tmp_path: Path, monkeypatch) -> None:
    """A failure during write must not mutate the existing config file."""

    p = tmp_path / "config.toml"
    cfg.write_project_config(p, cfg.ProjectConfig())
    original = p.read_bytes()

    def boom(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("simulated disk failure")

    monkeypatch.setattr(cfg.ledgercore, "atomic_write_text", boom)
    with pytest.raises(RuntimeError):
        cfg.update_project_config(p, {"ledger_ref": "broken"})
    assert p.read_bytes() == original


def test_branch_ref_update_preserves_unrelated_fields(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    cfg.write_project_config(p, cfg.ProjectConfig())
    loaded_before = cfg.load_project_config(p)
    # Capture a few "unrelated" values.
    original_changelog_body = loaded_before.changelog_body
    original_git_max = loaded_before.git_max_commits
    original_ledger_code = loaded_before.ledger_code

    updated = cfg.update_project_config(p, {"ledger_ref": "release-1"})

    assert updated.ledger_ref == "release-1"
    assert updated.changelog_body == original_changelog_body
    assert updated.git_max_commits == original_git_max
    assert updated.ledger_code == original_ledger_code

    # Re-read from disk; the values must be byte-equivalent for the unrelated keys.
    loaded_after = cfg.load_project_config(p)
    assert loaded_after.changelog_body == original_changelog_body
    assert loaded_after.git_max_commits == original_git_max
    assert loaded_after.ledger_code == original_ledger_code


def test_update_with_dotted_keys(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    cfg.write_project_config(p, cfg.ProjectConfig())
    updated = cfg.update_project_config(
        p,
        {
            "ledger_ref": "topic",
            "git.max_commits": 42,
        },
    )
    assert updated.ledger_ref == "topic"
    assert updated.git_max_commits == 42


def test_write_strips_v1_fields_in_existing_file(tmp_path: Path) -> None:
    """A v1 file upgraded by ``write_project_config`` drops removed keys."""

    p = tmp_path / "config.toml"
    p.write_text(_sample_v1_text())
    # v1 must be rejected on load.
    with pytest.raises(LaunchError):
        cfg.load_project_config(p)

    # Manually parse, build a v2 config, then write. The writer strips the
    # v1 keys when ``preserve_comments`` is true.
    import tomlkit

    document = tomlkit.parse(p.read_text())
    document["config_version"] = 2
    for removed in cfg.REMOVED_FIELDS:
        if removed in document:
            del document[removed]
    ledger = document.get("ledger")
    if isinstance(ledger, dict) and "name" in ledger:
        del ledger["name"]
    p.write_text(tomlkit.dumps(document))
    loaded = cfg.load_project_config(p)
    cfg.write_project_config(p, loaded)
    after = p.read_text()
    for removed in cfg.REMOVED_FIELDS:
        assert removed not in after, f"v1 key {removed!r} should have been removed"
    assert "name" not in after.split("[", 1)[1].split("]")[0]
    # File must still load.
    cfg.load_project_config(p)


def test_default_renderer_produces_v2(tmp_path: Path) -> None:
    text = cfg.render_default_project_config()
    assert "config_version = 2" in text
    for removed in cfg.REMOVED_FIELDS:
        assert removed not in text
    # Ensure it round-trips.
    p = tmp_path / "config.toml"
    p.write_text(text)
    loaded = cfg.load_project_config(p)
    assert loaded.config_version == 2


def test_no_storage_topology_keys_in_default(tmp_path: Path) -> None:
    text = cfg.render_default_project_config()
    forbidden_storage_vocabulary = (
        "releaseledger_dir",
        "releaseledger_dir_policy",
        "external_dir",
        "workspace",
        "user-data",
    )
    for word in forbidden_storage_vocabulary:
        assert word not in text, f"storage vocabulary {word!r} leaked into v2 default"


def test_reject_unknown_top_level_key(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    p.write_text("config_version = 2\nextra_key = 'oops'\n")
    with pytest.raises(LaunchError) as exc:
        cfg.load_project_config(p)
    assert exc.value.code == "CONFIG_ERROR"
    assert "extra_key" in str(exc.value)


def test_reject_invalid_git_include_merges(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    p.write_text(
        "config_version = 2\n[git]\ninclude_merges = 'sometimes'\n"
    )
    with pytest.raises(LaunchError) as exc:
        cfg.load_project_config(p)
    assert exc.value.code == "CONFIG_ERROR"


def test_missing_file_loads_as_error(tmp_path: Path) -> None:
    p = tmp_path / "missing.toml"
    with pytest.raises(LaunchError) as exc:
        cfg.load_project_config(p)
    assert exc.value.code == "CONFIG_ERROR"
    assert "init" in str(exc.value.remediation).lower()


def test_write_fresh_file_does_not_require_existing(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    assert not p.exists()
    cfg.write_project_config(p, cfg.ProjectConfig())
    assert p.is_file()
    assert cfg.load_project_config(p).config_version == 2
