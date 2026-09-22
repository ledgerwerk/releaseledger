"""Skill protocol discovery behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from releaseledger import protocol


def _write_skill(path: Path, declared_protocol: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        (
            "---\n"
            "name: releaseledger\n"
            "description: Test Releaseledger skill.\n"
            f"protocol: {declared_protocol}\n"
            "---\n"
        ),
        encoding="utf-8",
    )


def _diagnostics(
    workspace: Path,
    tmp_path: Path,
    *,
    start_dir: Path | None = None,
) -> dict[str, object]:
    return protocol.protocol_diagnostics(
        workspace,
        start_dir=start_dir,
        home=tmp_path / "home",
        admin_skill_root=tmp_path / "admin" / "skills",
    )


@pytest.mark.parametrize(
    ("relative_root", "source", "providers", "legacy"),
    [
        (
            ".agents/skills",
            "project_agents",
            ("codex", "opencode", "copilot", "gemini", "cursor"),
            False,
        ),
        (".opencode/skills", "project_opencode", ("opencode",), False),
        (".github/skills", "project_github", ("copilot", "vscode"), False),
        (
            ".claude/skills",
            "project_claude",
            ("opencode", "copilot", "claude", "cursor"),
            False,
        ),
        (".gemini/skills", "project_gemini", ("gemini",), False),
        (".cursor/skills", "project_cursor", ("cursor",), False),
        (".codex/skills", "project_codex", ("cursor",), False),
        (
            "skills",
            "project_releaseledger_legacy",
            (),
            True,
        ),
    ],
)
def test_project_skill_locations(
    tmp_path: Path,
    relative_root: str,
    source: str,
    providers: tuple[str, ...],
    legacy: bool,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    skill = workspace / relative_root / "releaseledger" / "SKILL.md"
    _write_skill(skill, protocol.SKILL_PROTOCOL_VERSION)

    diagnostics = _diagnostics(workspace, tmp_path)

    assert diagnostics["skill_state"] == "match"
    assert diagnostics["skill_candidate_count"] == 1
    candidate = diagnostics["skill_candidates"][0]
    assert candidate == {
        "path": str(skill),
        "source": source,
        "scope": "project",
        "providers": list(providers),
        "legacy": legacy,
        "name": "releaseledger",
        "declared_protocol": protocol.SKILL_PROTOCOL_VERSION,
        "matches_cli": True,
        "error": None,
    }


@pytest.mark.parametrize(
    ("relative_root", "source", "providers", "legacy"),
    [
        (
            ".agents/skills",
            "user_agents",
            ("codex", "opencode", "copilot", "gemini", "cursor"),
            False,
        ),
        (".config/opencode/skills", "user_opencode", ("opencode",), False),
        (
            ".config/opencode/skill",
            "user_opencode_legacy",
            ("opencode",),
            True,
        ),
        (".copilot/skills", "user_copilot", ("copilot",), False),
        (
            ".claude/skills",
            "user_claude",
            ("opencode", "vscode", "claude", "cursor"),
            False,
        ),
        (".gemini/skills", "user_gemini", ("gemini",), False),
        (".cursor/skills", "user_cursor", ("cursor",), False),
        (".codex/skills", "user_codex", ("cursor",), False),
    ],
)
def test_user_skill_locations(
    tmp_path: Path,
    relative_root: str,
    source: str,
    providers: tuple[str, ...],
    legacy: bool,
) -> None:
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    workspace.mkdir()
    skill = home / relative_root / "releaseledger" / "SKILL.md"
    _write_skill(skill, protocol.SKILL_PROTOCOL_VERSION)

    diagnostics = protocol.protocol_diagnostics(
        workspace,
        home=home,
        admin_skill_root=tmp_path / "admin" / "skills",
    )

    assert diagnostics["skill_state"] == "match"
    assert diagnostics["skill_candidate_count"] == 1
    candidate = diagnostics["skill_candidates"][0]
    assert candidate["path"] == str(skill)
    assert candidate["source"] == source
    assert candidate["scope"] == "user"
    assert candidate["providers"] == list(providers)
    assert candidate["legacy"] is legacy
    assert candidate["declared_protocol"] == protocol.SKILL_PROTOCOL_VERSION


def test_project_legacy_opencode_location(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    skill = workspace / ".opencode/skill/releaseledger/SKILL.md"
    _write_skill(skill, protocol.SKILL_PROTOCOL_VERSION)

    diagnostics = _diagnostics(workspace, tmp_path)

    candidate = diagnostics["skill_candidates"][0]
    assert candidate["source"] == "project_opencode_legacy"
    assert candidate["legacy"] is True


def test_admin_skill_location_is_injectable(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    admin_root = tmp_path / "fake-etc" / "codex" / "skills"
    workspace.mkdir()
    skill = admin_root / "releaseledger" / "SKILL.md"
    _write_skill(skill, protocol.SKILL_PROTOCOL_VERSION)

    diagnostics = protocol.protocol_diagnostics(
        workspace,
        home=tmp_path / "home",
        admin_skill_root=admin_root,
    )

    candidate = diagnostics["skill_candidates"][0]
    assert candidate["path"] == str(skill)
    assert candidate["source"] == "admin_codex"
    assert candidate["scope"] == "admin"
    assert candidate["providers"] == ["codex"]


def test_nested_project_discovery_is_specific_first_and_bounded(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    nested = workspace / "packages" / "api"
    workspace_skill = workspace / ".agents/skills/releaseledger/SKILL.md"
    nested_skill = nested / ".agents/skills/releaseledger/SKILL.md"
    workspace.mkdir()
    _write_skill(workspace_skill, protocol.SKILL_PROTOCOL_VERSION)
    _write_skill(nested_skill, protocol.SKILL_PROTOCOL_VERSION)

    diagnostics = _diagnostics(workspace, tmp_path, start_dir=nested)
    candidates = diagnostics["skill_candidates"]

    assert [candidate["path"] for candidate in candidates] == [
        str(nested_skill),
        str(workspace_skill),
    ]
    assert all(candidate["scope"] == "project" for candidate in candidates)


def test_start_directory_outside_workspace_uses_only_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    _write_skill(outside / ".agents/skills/releaseledger/SKILL.md", 2)

    locations = protocol.skill_locations(workspace, start_dir=outside)
    project_agents = [
        location for location in locations if location.source == "project_agents"
    ]

    assert len(project_agents) == 1
    assert project_agents[0].path == workspace / ".agents/skills/releaseledger/SKILL.md"


def test_discovery_does_not_search_above_workspace(tmp_path: Path) -> None:
    parent_skill = tmp_path / ".agents/skills/releaseledger/SKILL.md"
    workspace = tmp_path / "repo"
    workspace.mkdir()
    _write_skill(parent_skill, protocol.SKILL_PROTOCOL_VERSION)

    diagnostics = _diagnostics(workspace, tmp_path)

    assert diagnostics["skill_state"] == "missing"
    assert diagnostics["skill_candidate_count"] == 0


def test_duplicate_matching_copies_are_safe(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_skill(
        workspace / ".agents/skills/releaseledger/SKILL.md",
        protocol.SKILL_PROTOCOL_VERSION,
    )
    _write_skill(
        tmp_path / "home/.agents/skills/releaseledger/SKILL.md",
        protocol.SKILL_PROTOCOL_VERSION,
    )

    diagnostics = _diagnostics(workspace, tmp_path)

    assert diagnostics["skill_state"] == "match"
    assert diagnostics["skill_matches_cli"] is True
    assert diagnostics["skill_candidate_count"] == 2
    assert diagnostics["skill_conflicts"] == []


def test_matching_project_and_stale_user_copy_are_a_conflict(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_skill(
        workspace / ".agents/skills/releaseledger/SKILL.md",
        protocol.SKILL_PROTOCOL_VERSION,
    )
    _write_skill(tmp_path / "home/.claude/skills/releaseledger/SKILL.md", 1)

    diagnostics = _diagnostics(workspace, tmp_path)

    assert diagnostics["skill_state"] == "conflict"
    assert diagnostics["skill_matches_cli"] is False
    assert diagnostics["skill_candidate_count"] == 2
    assert str(tmp_path / "home/.claude/skills/releaseledger/SKILL.md") in {
        item["path"] for item in diagnostics["skill_conflicts"]
    }


def test_all_stale_copies_are_a_mismatch(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_skill(workspace / ".agents/skills/releaseledger/SKILL.md", 1)
    _write_skill(tmp_path / "home/.agents/skills/releaseledger/SKILL.md", 1)

    diagnostics = _diagnostics(workspace, tmp_path)

    assert diagnostics["skill_state"] == "mismatch"
    assert diagnostics["skill_matches_cli"] is False


@pytest.mark.parametrize(
    "content",
    [
        "name: releaseledger\n",
        "---\nprotocol: [\n---\n",
        "---\n- releaseledger\n---\n",
        "---\nname: another\ndescription: test\nprotocol: 2\n---\n",
        "---\nname: releaseledger\nprotocol: 2\n---\n",
        "---\nname: releaseledger\ndescription: '   '\nprotocol: 2\n---\n",
        "---\nname: releaseledger\ndescription: test\n---\n",
        "---\nname: releaseledger\ndescription: test\nprotocol: true\n---\n",
        "---\nname: releaseledger\ndescription: test\nprotocol: '2'\n---\n",
    ],
)
def test_malformed_skill_files_are_reported_without_crashing(
    tmp_path: Path, content: str
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    skill = workspace / ".agents/skills/releaseledger/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(content, encoding="utf-8")

    diagnostics = _diagnostics(workspace, tmp_path)

    assert diagnostics["skill_state"] == "invalid"
    assert diagnostics["skill_matches_cli"] is False
    assert diagnostics["skill_candidate_count"] == 1
    assert diagnostics["skill_candidates"][0]["error"]


def test_unreadable_skill_file_is_reported_without_crashing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    skill = workspace / ".agents/skills/releaseledger/SKILL.md"
    _write_skill(skill, protocol.SKILL_PROTOCOL_VERSION)
    original_read_text = Path.read_text

    def fail_read_text(path: Path, *args: object, **kwargs: object) -> str:
        if path == skill:
            raise OSError("simulated unreadable skill")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_read_text)

    diagnostics = _diagnostics(workspace, tmp_path)

    assert diagnostics["skill_state"] == "invalid"
    assert "simulated unreadable skill" in diagnostics["skill_candidates"][0]["error"]


def test_symlinked_skill_file_is_discovered_when_supported(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    target = tmp_path / "target" / "SKILL.md"
    link = workspace / ".agents/skills/releaseledger/SKILL.md"
    workspace.mkdir()
    _write_skill(target, protocol.SKILL_PROTOCOL_VERSION)
    link.parent.mkdir(parents=True)
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable")

    diagnostics = _diagnostics(workspace, tmp_path)

    assert diagnostics["skill_state"] == "match"
    assert diagnostics["skill_candidates"][0]["path"] == str(link)


def test_skill_path_and_protocol_compatibility_wrappers(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    skill = workspace / ".agents/skills/releaseledger/SKILL.md"
    _write_skill(skill, protocol.SKILL_PROTOCOL_VERSION)

    assert (
        protocol.skill_path(
            workspace,
            home=tmp_path / "home",
            admin_skill_root=tmp_path / "admin" / "skills",
        )
        == skill
    )
    assert protocol.skill_protocol(skill) == protocol.SKILL_PROTOCOL_VERSION


def test_source_tree_skill_is_not_an_active_bundled_candidate(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    fake_package_skill = tmp_path / "package/skills/releaseledger/SKILL.md"
    workspace.mkdir()
    _write_skill(fake_package_skill, protocol.SKILL_PROTOCOL_VERSION)

    assert (
        protocol.skill_path(
            workspace,
            home=tmp_path / "home",
            admin_skill_root=tmp_path / "admin" / "skills",
        )
        is None
    )
