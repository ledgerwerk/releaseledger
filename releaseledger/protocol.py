"""CLI and external skill protocol compatibility metadata."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

SKILL_PROTOCOL_VERSION = 2


def skill_path(workspace_root: Path) -> Path | None:
    """Find the repository's external releaseledger skill."""
    candidates = (
        workspace_root / "skills" / "releaseledger" / "SKILL.md",
        Path(__file__).resolve().parents[1] / "skills" / "releaseledger" / "SKILL.md",
    )
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def skill_protocol(path: Path | None) -> int | None:
    """Read the protocol field from a skill front matter block."""
    if path is None:
        return None
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end < 0:
        return None
    metadata: Any = yaml.safe_load(text[3:end])
    if not isinstance(metadata, dict):
        return None
    value = metadata.get("protocol")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def protocol_diagnostics(workspace_root: Path) -> dict[str, object]:
    """Return deterministic CLI/skill compatibility diagnostics."""
    path = skill_path(workspace_root)
    protocol = skill_protocol(path)
    return {
        "releaseledger_version": __import__(
            "releaseledger", fromlist=["__version__"]
        ).__version__,
        "skill_protocol": SKILL_PROTOCOL_VERSION,
        "skill_path": str(path) if path is not None else None,
        "skill_declared_protocol": protocol,
        "skill_matches_cli": protocol == SKILL_PROTOCOL_VERSION,
    }
