"""Regression tests for the releaseledger skill protocol text.

These guard the skill prompt against drift away from the mandatory
commit-by-commit git audit and the no-parallel-mutations rules introduced in
``releaseledger_skill_commit_audit_fix.md``. They are prompt-text guards, not
runtime feature tests.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "releaseledger" / "SKILL.md"


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
    assert "audit validate VERSION --phase complete --strict --include-internal" in text
    assert "release check VERSION --strict --target-file CHANGELOG.md" in text


def test_skill_uses_builtin_commit_subject_guard_and_snapshot_rule() -> None:
    text = SKILL.read_text(encoding="utf-8")
    assert "--guard-commit-subjects" in text
    assert "Resolve `HEAD` once" in text
    assert "omit `--head`" in text
