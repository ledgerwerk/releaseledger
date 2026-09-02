"""Validation helpers for command examples in Releaseledger documentation."""

from __future__ import annotations

import shlex
from pathlib import Path

from releaseledger.command_registry import COMMAND_INVENTORY

__all__ = ["find_deprecated_command_examples", "find_invalid_command_examples"]
_COMPATIBILITY_HEADINGS = (
    "compatibility",
    "migration from",
    "deprecated aliases",
    "historical changelog",
)


def _is_compatibility_heading(heading: str) -> bool:
    normalized = heading.casefold()
    return any(marker in normalized for marker in _COMPATIBILITY_HEADINGS)


def _command_tokens(tokens: list[str]) -> list[str]:
    """Return command tokens after the documented global CLI options."""
    if not tokens or tokens[0] != "releaseledger":
        return []
    tokens = tokens[1:]
    index = 0
    global_options_with_values = {"--root", "--cwd"}
    while index < len(tokens):
        token = tokens[index]
        if token in global_options_with_values:
            index += 2
            continue
        if token.startswith("--"):
            index += 1
            continue
        break
    command: list[str] = []
    while index < len(tokens) and not tokens[index].startswith("-"):
        command.append(tokens[index])
        index += 1
    return command


def find_deprecated_command_examples(paths: list[Path]) -> list[dict[str, object]]:
    """Find registry aliases used in non-compatibility documentation sections.

    The registry supplies the aliases, so this check stays synchronized with the
    CLI inventory instead of maintaining a second list of deprecated spellings.
    Each result contains ``path``, ``line``, ``command``, ``replacement``, and
    ``heading`` for use by tests or documentation tooling.
    """
    aliases: list[tuple[tuple[str, ...], str, str]] = []
    canonical_paths = {
        tuple(metadata.path.split()) for metadata in COMMAND_INVENTORY.entries
    }
    for metadata in COMMAND_INVENTORY.entries:
        for alias in metadata.aliases:
            aliases.append((tuple(alias.split()), alias, metadata.path))
    aliases.sort(key=lambda item: len(item[0]), reverse=True)

    findings: list[dict[str, object]] = []
    for path in paths:
        heading = ""
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        ):
            stripped = line.strip()
            if stripped.startswith("#"):
                heading = stripped.lstrip("#").strip()
            if not stripped.startswith("releaseledger"):
                continue
            try:
                tokens = shlex.split(stripped)
            except ValueError:
                continue
            command = _command_tokens(tokens)
            for alias_tokens, alias, replacement in aliases:
                if tuple(command[: len(alias_tokens)]) != alias_tokens:
                    continue
                # A short alias can prefix a canonical subcommand, e.g.
                # `changelog build`; only flag the alias when no canonical
                # command consumes the following token(s).
                if any(
                    tuple(command[: len(canonical)]) == canonical
                    for canonical in canonical_paths
                    if len(canonical) > len(alias_tokens)
                ):
                    continue
                if _is_compatibility_heading(heading):
                    continue
                findings.append(
                    {
                        "path": str(path),
                        "line": line_number,
                        "command": f"releaseledger {alias}",
                        "replacement": f"releaseledger {replacement}",
                        "heading": heading,
                    }
                )
    return findings


def find_invalid_command_examples(paths: list[Path]) -> list[dict[str, object]]:
    """Find releaseledger examples whose command path is absent from the registry."""
    canonical_paths = {
        tuple(metadata.path.split()) for metadata in COMMAND_INVENTORY.entries
    }
    findings: list[dict[str, object]] = []
    for path in paths:
        heading = ""
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        ):
            stripped = line.strip()
            if stripped.startswith("#"):
                heading = stripped.lstrip("#").strip()
            if not stripped.startswith("releaseledger") or _is_compatibility_heading(
                heading
            ):
                continue
            try:
                command = _command_tokens(shlex.split(stripped))
            except ValueError:
                continue
            if command and not any(
                tuple(command[: len(candidate)]) == candidate
                for candidate in canonical_paths
            ):
                findings.append(
                    {
                        "path": str(path),
                        "line": line_number,
                        "command": "releaseledger " + " ".join(command),
                        "heading": heading,
                    }
                )
    return findings
