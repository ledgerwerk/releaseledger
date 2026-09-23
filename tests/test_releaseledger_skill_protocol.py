"""Regression tests for the releaseledger skill protocol text.

These guard the skill prompt against drift away from the mandatory
commit-by-commit git audit and the no-parallel-mutations rules introduced in
``releaseledger_skill_commit_audit_fix.md``. They are prompt-text guards, not
runtime feature tests.
"""

from __future__ import annotations

from pathlib import Path

from releaseledger import protocol

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "releaseledger" / "SKILL.md"

COMMANDS = ROOT / "docs" / "commands.md"
CONCEPTS = ROOT / "docs" / "concepts.md"
QUICKSTART = ROOT / "docs" / "quickstart.md"


def test_skill_requires_commit_by_commit_git_audit() -> None:
    text = SKILL.read_text(encoding="utf-8")
    assert "For any non-empty git range" in text
    assert "Every `git:<sha>`" in text
    assert "Aggregate `git log`, aggregate `git diff --stat`" in text
    assert "No coverage, no build" in text


def test_skill_disallows_parallel_releaseledger_mutations() -> None:
    text = SKILL.read_text(encoding="utf-8")
    assert "Do not run multiple releaseledger mutating commands concurrently" in text
    assert "Never run a command and its verification concurrently." in text
    assert (
        "mutation before issuing `show`, `list`, `review`, `validate`, or file checks"
        in text
    )


def test_skill_uses_phase_aware_audit_validation_and_release_check() -> None:
    text = SKILL.read_text(encoding="utf-8")
    assert "audit validate VERSION --phase evidence --strict" in text
    assert "audit validate VERSION --phase complete --strict`" in text
    assert "--include-internal" in text
    assert "release check VERSION --strict --target-file CHANGELOG.md" in text


def test_skill_uses_builtin_commit_subject_guard_and_snapshot_rule() -> None:
    text = SKILL.read_text(encoding="utf-8")
    assert "--guard-commit-subjects" in text
    assert "Resolve `HEAD` once" in text
    assert "omit `--head`" in text


def test_prepare_guidance_is_structured_and_lifecycle_aware() -> None:
    skill_text = SKILL.read_text(encoding="utf-8")
    commands_text = COMMANDS.read_text(encoding="utf-8")
    concepts_text = CONCEPTS.read_text(encoding="utf-8")
    quickstart_text = QUICKSTART.read_text(encoding="utf-8")

    assert "structured `next_actions`" in skill_text
    assert "preparation-only" in commands_text
    assert "never finalizes" in concepts_text
    assert "--unreleased" in quickstart_text
    assert "dry-run/apply audit" in quickstart_text


def test_skill_documents_external_git_tag_ownership() -> None:
    text = SKILL.read_text(encoding="utf-8")
    assert "git.tag_creation" in text
    assert 'When `git.tag_creation = "external"`' in text
    assert "do not run `git tag`" in text
    assert "do not use `gh release create`" in text
    assert "git fetch --tags" in text


def test_skill_does_not_document_undated_strict_single_build() -> None:
    text = SKILL.read_text(encoding="utf-8")
    assert "changelog build VERSION --dry-run --strict --unreleased" in text
    assert "release prepare VERSION --previous PREV_VERSION" in text


def test_skill_documents_safe_index_cache_recovery() -> None:
    text = SKILL.read_text(encoding="utf-8")
    assert "releaseledger repair index --dry-run" in text
    assert "releaseledger repair index --apply" in text
    assert "releaseledger repair index --apply --quarantine-foreign" in text
    assert "inspect `unexpected_paths` and mismatch details" in text
    assert "Do not use `rm`, `mv`, or direct edits under the cache root." in text
    assert "Do not route index-cache repair through storage migration." in text
    assert "same read-only validation command that just failed" in text


def test_canonical_skill_has_valid_discoverable_metadata() -> None:
    metadata, error = protocol.read_skill_metadata(SKILL)
    assert error is None
    assert metadata is not None
    assert metadata.name == protocol.SKILL_NAME
    assert metadata.description
    assert metadata.protocol == protocol.SKILL_PROTOCOL_VERSION


def test_canonical_skill_is_source_only_not_package_data() -> None:
    package_skill = (
        ROOT / "releaseledger" / "resources" / "skills" / "releaseledger" / "SKILL.md"
    )
    assert not package_skill.exists()
    assert not (
        ROOT / "releaseledger" / "skills" / "releaseledger" / "SKILL.md"
    ).exists()
