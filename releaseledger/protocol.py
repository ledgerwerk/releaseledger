"""CLI and external skill protocol compatibility metadata."""

from __future__ import annotations

import os
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

import yaml

SKILL_NAME = "releaseledger"
SKILL_FILENAME = "SKILL.md"
SKILL_PROTOCOL_VERSION = 2
_DEFAULT_ADMIN_SKILL_ROOT = Path("/etc/codex/skills")


@dataclass(frozen=True)
class SkillLocation:
    """A supported local location where an Agent Skill may be installed."""

    source: str
    scope: str
    providers: tuple[str, ...]
    path: Path
    legacy: bool = False


@dataclass(frozen=True)
class SkillMetadata:
    """Validated or partially parsed metadata from a skill frontmatter block."""

    name: str | None
    description: str | None
    protocol: int | None


@dataclass(frozen=True)
class SkillProbe:
    """The safe result of inspecting one discovered skill candidate."""

    location: SkillLocation
    exists: bool
    name: str | None
    description: str | None
    protocol: int | None
    error: str | None
    sha256: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Serialize a probe without exposing skill contents."""
        return {
            "path": str(self.location.path),
            "source": self.location.source,
            "scope": self.location.scope,
            "providers": list(self.location.providers),
            "legacy": self.location.legacy,
            "name": self.name,
            "declared_protocol": self.protocol,
            "matches_cli": self.protocol == SKILL_PROTOCOL_VERSION,
            "error": self.error,
        }


_PROJECT_LOCATION_SPECS: tuple[tuple[str, str, tuple[str, ...], bool], ...] = (
    (
        "project_agents",
        ".agents/skills",
        ("codex", "opencode", "copilot", "gemini", "cursor"),
        False,
    ),
    ("project_github", ".github/skills", ("copilot", "vscode"), False),
    ("project_opencode", ".opencode/skills", ("opencode",), False),
    (
        "project_opencode_legacy",
        ".opencode/skill",
        ("opencode",),
        True,
    ),
    (
        "project_claude",
        ".claude/skills",
        ("opencode", "copilot", "claude", "cursor"),
        False,
    ),
    ("project_gemini", ".gemini/skills", ("gemini",), False),
    ("project_cursor", ".cursor/skills", ("cursor",), False),
    ("project_codex", ".codex/skills", ("cursor",), False),
)

_USER_LOCATION_SPECS: tuple[tuple[str, str, tuple[str, ...], bool], ...] = (
    (
        "user_agents",
        ".agents/skills",
        ("codex", "opencode", "copilot", "gemini", "cursor"),
        False,
    ),
    ("user_opencode", ".config/opencode/skills", ("opencode",), False),
    (
        "user_opencode_legacy",
        ".config/opencode/skill",
        ("opencode",),
        True,
    ),
    ("user_copilot", ".copilot/skills", ("copilot",), False),
    (
        "user_claude",
        ".claude/skills",
        ("opencode", "vscode", "claude", "cursor"),
        False,
    ),
    ("user_gemini", ".gemini/skills", ("gemini",), False),
    ("user_cursor", ".cursor/skills", ("cursor",), False),
    ("user_codex", ".codex/skills", ("cursor",), False),
)


def _resolved(path: Path) -> Path:
    return Path(path).expanduser().resolve()


def _project_search_dirs(workspace_root: Path, start_dir: Path) -> tuple[Path, ...]:
    """Return the bounded current-directory-to-workspace search chain."""
    workspace = _resolved(workspace_root)
    start = _resolved(start_dir)
    try:
        start.relative_to(workspace)
    except ValueError:
        return (workspace,)

    result: list[Path] = []
    current = start
    while True:
        if current not in result:
            result.append(current)
        if current == workspace:
            break
        current = current.parent
    return tuple(result)


def _skill_path(root: Path, relative_root: str) -> Path:
    return root / relative_root / SKILL_NAME / SKILL_FILENAME


def _default_user_home() -> Path:
    """Return the user home while honoring the conventional HOME override."""
    configured = os.environ.get("HOME")
    return Path(configured) if configured else Path.home()


def skill_locations(
    workspace_root: Path,
    *,
    start_dir: Path | None = None,
    home: Path | None = None,
    admin_skill_root: Path | None = None,
) -> tuple[SkillLocation, ...]:
    """Enumerate supported local skill locations in deterministic display order."""
    workspace = _resolved(workspace_root)
    search_dirs = _project_search_dirs(workspace, start_dir or workspace)
    user_home = _resolved(home or _default_user_home())
    admin_root = _resolved(admin_skill_root or _DEFAULT_ADMIN_SKILL_ROOT)
    locations: list[SkillLocation] = []
    seen: set[Path] = set()

    def add(location: SkillLocation) -> None:
        if location.path in seen:
            return
        seen.add(location.path)
        locations.append(location)

    for source, relative_root, providers, legacy in _PROJECT_LOCATION_SPECS:
        for directory in search_dirs:
            add(
                SkillLocation(
                    source=source,
                    scope="project",
                    providers=providers,
                    path=_skill_path(directory, relative_root),
                    legacy=legacy,
                )
            )

    add(
        SkillLocation(
            source="project_releaseledger_legacy",
            scope="project",
            providers=(),
            path=_skill_path(workspace, "skills"),
            legacy=True,
        )
    )

    for source, relative_root, providers, legacy in _USER_LOCATION_SPECS:
        add(
            SkillLocation(
                source=source,
                scope="user",
                providers=providers,
                path=_skill_path(user_home, relative_root),
                legacy=legacy,
            )
        )

    add(
        SkillLocation(
            source="admin_codex",
            scope="admin",
            providers=("codex",),
            path=_skill_path(admin_root, ""),
        )
    )
    return tuple(locations)


def _frontmatter(text: str) -> tuple[dict[str, Any] | None, str | None]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, "Skill file is missing YAML frontmatter."

    end = next(
        (index for index in range(1, len(lines)) if lines[index].strip() == "---"), None
    )
    if end is None:
        return None, "Skill frontmatter is not terminated."

    try:
        metadata = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError as exc:
        return None, f"Skill frontmatter is invalid YAML: {exc}"
    if not isinstance(metadata, dict):
        return None, "Skill frontmatter must be a mapping."
    return metadata, None


def read_skill_metadata(path: Path) -> tuple[SkillMetadata | None, str | None]:
    """Read and validate the metadata required by a discoverable skill."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return None, f"Unable to read skill file: {exc}"

    raw, error = _frontmatter(text)
    if error is not None or raw is None:
        return None, error or "Skill metadata could not be parsed."

    raw_name = raw.get("name")
    raw_description = raw.get("description")
    raw_protocol = raw.get("protocol")
    name = raw_name if isinstance(raw_name, str) else None
    description = raw_description if isinstance(raw_description, str) else None
    protocol = (
        raw_protocol
        if isinstance(raw_protocol, int) and not isinstance(raw_protocol, bool)
        else None
    )

    errors: list[str] = []
    if name != SKILL_NAME:
        errors.append(f"Skill name must be {SKILL_NAME!r}.")
    if description is None or not description.strip():
        errors.append("Skill description must be a non-empty string.")
    if raw_protocol is None:
        errors.append("Skill protocol is required.")
    elif protocol is None:
        errors.append("Skill protocol must be an integer, not a Boolean or string.")
    return SkillMetadata(name, description, protocol), "; ".join(errors) or None


def probe_skill(location: SkillLocation) -> SkillProbe:
    """Probe one location without allowing filesystem or YAML errors to escape."""
    try:
        exists = location.path.is_file()
    except OSError as exc:
        return SkillProbe(
            location, True, None, None, None, f"Unable to inspect skill file: {exc}"
        )
    if not exists:
        return SkillProbe(location, False, None, None, None, None)

    metadata, error = read_skill_metadata(location.path)
    digest: str | None = None
    try:
        digest = sha256(location.path.read_bytes()).hexdigest()
    except (OSError, UnicodeError):
        pass
    if metadata is None:
        return SkillProbe(location, True, None, None, None, error, digest)
    return SkillProbe(
        location,
        True,
        metadata.name,
        metadata.description,
        metadata.protocol,
        error,
        digest,
    )


def discover_skills(
    workspace_root: Path,
    *,
    start_dir: Path | None = None,
    home: Path | None = None,
    admin_skill_root: Path | None = None,
) -> tuple[SkillProbe, ...]:
    """Inspect every existing skill candidate in the supported local locations."""
    probes: list[SkillProbe] = []
    for location in skill_locations(
        workspace_root,
        start_dir=start_dir,
        home=home,
        admin_skill_root=admin_skill_root,
    ):
        try:
            exists = location.path.is_file()
        except OSError as exc:
            probes.append(
                SkillProbe(
                    location,
                    True,
                    None,
                    None,
                    None,
                    f"Unable to inspect skill file: {exc}",
                )
            )
            continue
        if exists:
            probes.append(probe_skill(location))
    return tuple(probes)


def _skill_state(probes: tuple[SkillProbe, ...]) -> str:
    if not probes:
        return "missing"

    valid = tuple(probe for probe in probes if probe.error is None)
    if not valid:
        return "invalid"

    protocols = {probe.protocol for probe in valid}
    has_invalid = len(valid) != len(probes)
    has_match = SKILL_PROTOCOL_VERSION in protocols
    has_mismatch = any(probe.protocol != SKILL_PROTOCOL_VERSION for probe in valid)
    if has_invalid and len(probes) > 1:
        return "conflict"
    if has_match and has_mismatch:
        return "conflict"
    if has_match:
        return "match"
    return "mismatch"


def _skill_conflicts(
    probes: tuple[SkillProbe, ...], state: str
) -> list[dict[str, object]]:
    if state != "conflict":
        return []
    return [
        {
            "path": str(probe.location.path),
            "source": probe.location.source,
            "declared_protocol": probe.protocol,
            "error": probe.error,
        }
        for probe in probes
    ]


def skill_path(
    workspace_root: Path,
    *,
    start_dir: Path | None = None,
    home: Path | None = None,
    admin_skill_root: Path | None = None,
) -> Path | None:
    """Return the preferred existing discovered skill path."""
    probes = discover_skills(
        workspace_root,
        start_dir=start_dir,
        home=home,
        admin_skill_root=admin_skill_root,
    )
    return probes[0].location.path if probes else None


def skill_protocol(path: Path | None) -> int | None:
    """Read a valid skill protocol field as a compatibility wrapper."""
    if path is None:
        return None
    metadata, error = read_skill_metadata(path)
    if metadata is None or error is not None:
        return None
    return metadata.protocol


def protocol_diagnostics(
    workspace_root: Path,
    *,
    start_dir: Path | None = None,
    home: Path | None = None,
    admin_skill_root: Path | None = None,
) -> dict[str, object]:
    """Return deterministic CLI and skill compatibility diagnostics."""
    probes = discover_skills(
        workspace_root,
        start_dir=start_dir,
        home=home,
        admin_skill_root=admin_skill_root,
    )
    state = _skill_state(probes)
    selected = probes[0] if probes else None
    return {
        "releaseledger_version": __import__(
            "releaseledger", fromlist=["__version__"]
        ).__version__,
        "skill_protocol": SKILL_PROTOCOL_VERSION,
        "skill_path": str(selected.location.path) if selected else None,
        "skill_declared_protocol": selected.protocol if selected else None,
        "skill_matches_cli": state == "match",
        "skill_state": state,
        "skill_candidates": [probe.to_dict() for probe in probes],
        "skill_candidate_count": len(probes),
        "skill_conflicts": _skill_conflicts(probes, state),
    }
