"""Keep primary documentation examples aligned with the command registry."""

from __future__ import annotations

from pathlib import Path

from releaseledger.documentation import find_deprecated_command_examples

ROOT = Path(__file__).resolve().parents[1]
PRIMARY_DOCS = [
    ROOT / "skills" / "releaseledger" / "SKILL.md",
    ROOT / "README.md",
    ROOT / "docs" / "quickstart.md",
    ROOT / "docs" / "commands.md",
]


def test_primary_releaseledger_docs_use_canonical_commands() -> None:
    assert find_deprecated_command_examples(PRIMARY_DOCS) == []


def test_command_lint_allows_explicit_compatibility_sections(tmp_path: Path) -> None:
    document = tmp_path / "commands.md"
    document.write_text(
        "# Current workflow\n"
        "releaseledger entry add-many VERSION --file entries.yaml\n"
        "\n"
        "## Compatibility\n"
        "releaseledger build VERSION\n",
        encoding="utf-8",
    )
    findings = find_deprecated_command_examples([document])
    assert len(findings) == 1
    assert findings[0]["command"] == "releaseledger entry add-many"
    assert findings[0]["replacement"] == "releaseledger entry apply"


def test_command_lint_rejects_unknown_registry_path(tmp_path: Path) -> None:
    document = tmp_path / "commands.md"
    document.write_text(
        "# Current workflow\nreleaseledger release not-a-command VERSION\n",
        encoding="utf-8",
    )
    from releaseledger.documentation import find_invalid_command_examples

    findings = find_invalid_command_examples([document])
    assert len(findings) == 1
    assert "not-a-command" in findings[0]["command"]
