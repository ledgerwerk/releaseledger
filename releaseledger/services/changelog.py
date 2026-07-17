"""Changelog context service.

Renders a deterministic, agent-friendly changelog source document (Markdown) or
machine payload (JSON) from a release and its entries. The output is meant to be
fed to an LLM or a human to produce the final ``CHANGELOG.md`` section.
"""

from __future__ import annotations

from pathlib import Path

from releaseledger.domain.entry import ReleaseEntryRecord, normalize_entry_status
from releaseledger.domain.release import ReleaseRecord
from releaseledger.domain.states import ENTRY_KIND_TITLES
from releaseledger.services.entry_lint import lint_release_entries
from releaseledger.storage.config import DEFAULT_LEDGER_NAME
from releaseledger.storage.paths import ProjectPaths, resolve_project_paths
from releaseledger.storage.store import load_entries, load_release

__all__ = ["build_changelog_context"]

# Fixed group order for candidate changes output.
_GROUP_ORDER = (
    "added",
    "changed",
    "fixed",
    "removed",
    "deprecated",
    "security",
    "docs",
    "quality",
    "internal",
)


def _project_name(paths: ProjectPaths) -> str:
    try:
        from releaseledger.storage.config import project_name_or_default

        return project_name_or_default(paths.project)
    except Exception:  # pragma: no cover - defensive fallback
        return paths.workspace_root.name or DEFAULT_LEDGER_NAME


def _entry_payload(entry: ReleaseEntryRecord) -> dict[str, object]:
    return {
        "entry_id": entry.entry_id,
        "kind": entry.kind,
        "summary": entry.summary,
        "body": entry.body,
        "paths": list(entry.paths),
        "issues": list(entry.issues),
        "prs": list(entry.prs),
        "sources": list(entry.sources),
        "status": entry.status,
        "audience": entry.audience,
        "scopes": list(entry.scopes),
        "source_refs": list(entry.source_refs),
        "breaking": entry.breaking,
        "internal": entry.internal,
    }


def _grouped_entries(
    entries: list[ReleaseEntryRecord],
) -> list[tuple[str, list[ReleaseEntryRecord]]]:
    grouped: list[tuple[str, list[ReleaseEntryRecord]]] = []
    for kind in _GROUP_ORDER:
        members = [entry for entry in entries if entry.kind == kind]
        if members:
            grouped.append((kind, members))
    return grouped


def _render_candidate_changes(
    entries: list[ReleaseEntryRecord],
    *,
    include_sources: bool = False,
) -> str:
    lines: list[str] = ["## Candidate changes", ""]
    grouped = _grouped_entries(entries)
    if not grouped:
        lines.append("(no candidate changes)")
        return "\n".join(lines)
    for kind, members in grouped:
        title = ENTRY_KIND_TITLES.get(kind, kind.capitalize())
        lines.append(f"### {title}")
        lines.append("")
        for entry in members:
            marker = " (breaking)" if entry.breaking else ""
            lines.append(f"- {entry.summary}{marker}")
            if entry.paths:
                quoted = ", ".join(f"`{p}`" for p in entry.paths)
                lines.append(f"  - Paths: {quoted}")
            if include_sources and entry.sources:
                src = ", ".join(entry.sources)
                lines.append(f"  - Sources: {src}")
        lines.append("")
    # Drop the trailing blank line for a tidy single final newline.
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def _render_edit_guidance(
    *,
    target_changelog: str,
    release_date: str | None,
) -> list[str]:
    lines = ["## Changelog edit guidance", ""]
    lines.append(f"- Target changelog: {target_changelog}")
    lines.append(
        "- Insert the final section below `## Unreleased`"
        " and above the previous release."
    )
    lines.append("- Preserve existing release history.")
    if release_date:
        lines.append(f"- Use release date: {release_date}")
    else:
        lines.append("- No release date was provided; do not invent a release date.")
    return lines


def _render_markdown(
    *,
    project_name: str,
    release: ReleaseRecord,
    entries: list[ReleaseEntryRecord],
    target_changelog: str | None,
    release_date: str | None,
    include_sources: bool = False,
    included_statuses: tuple[str, ...] = ("accepted",),
    filtered_counts: dict[str, int] | None = None,
) -> str:
    version = release.version
    previous = release.previous_version or "none"
    date_text = release_date or "not provided"
    status = release.status

    sections: list[str] = []
    sections.append(f"# Changelog source for {project_name} {version}")
    sections.append("")
    sections.append("## LLM instruction")
    sections.append("")
    sections.append(
        f"Write a concise human changelog for {project_name} version {version}."
    )
    sections.append("Use only the releaseledger data below. Do not invent changes.")
    sections.append(
        "Group entries under headings such as Added, Changed, Fixed, Removed, "
        "Security, Documentation, and Internal when useful."
    )
    sections.append(
        "Mention user-visible CLI/API/storage changes. Avoid internal IDs in the "
        "final changelog unless useful."
    )
    sections.append("")
    sections.append("## Release metadata")
    sections.append("")
    sections.append(f"- Version being prepared: {version}")
    sections.append(f"- Project: {project_name}")
    sections.append(f"- Previous release: {previous}")
    sections.append(f"- Status: {status}")
    sections.append(f"- Boundary ref: {release.boundary_ref or 'none'}")
    sections.append(
        "- Source refs: "
        + (", ".join(release.source_refs) if release.source_refs else "none")
    )
    sections.append(f"- Included statuses: {', '.join(included_statuses)}")
    sections.append(f"- Release date: {date_text}")
    sections.append("")
    if target_changelog is not None:
        sections.extend(
            _render_edit_guidance(
                target_changelog=target_changelog, release_date=release_date
            )
        )
        sections.append("")
    sections.append(_render_candidate_changes(entries, include_sources=include_sources))
    counts = filtered_counts or {}
    hidden = [f"{name}: {count}" for name, count in counts.items() if count]
    if hidden:
        sections.append("")
        sections.append("## Filtered entries")
        sections.append("")
        sections.extend(f"- {item} hidden" for item in hidden)
    return "\n".join(sections) + "\n"


def build_changelog_context(
    workspace_root: Path,
    *,
    version: str,
    format_name: str = "markdown",
    include_internal: bool = False,
    include_sources: bool = False,
    target_changelog: str | None = None,
    release_date: str | None = None,
    include_statuses: tuple[str, ...] = ("accepted",),
    lint: bool = False,
) -> str | dict[str, object]:
    """Render the changelog context for ``version`` as Markdown (str) or JSON (dict)."""
    paths = resolve_project_paths(workspace_root)
    release = load_release(workspace_root, version)
    all_entries = load_entries(workspace_root, version)
    statuses = tuple(normalize_entry_status(value) for value in include_statuses)
    entries = [
        entry
        for entry in all_entries
        if entry.status in statuses and (include_internal or not entry.internal)
    ]
    project_name = _project_name(paths)
    effective_date = release_date or release.released_at

    status_counts = {
        status: sum(entry.status == status for entry in all_entries)
        for status in ("accepted", "draft", "rejected")
    }
    filtered_counts = {
        status: count
        for status, count in status_counts.items()
        if status not in statuses
    }
    filtered_counts["internal"] = sum(
        entry.internal for entry in all_entries if not include_internal
    )
    warnings: list[object] = []
    if lint:
        lint_result = lint_release_entries(
            workspace_root,
            release_version=version,
            include_statuses=statuses,
        )
        raw_issues = lint_result["issues"]
        if isinstance(raw_issues, list):
            warnings = list(raw_issues)
    context: dict[str, object] = {
        "kind": "release_changelog_context",
        "version": version,
        "project_name": project_name,
        "ledger_ref": paths.ledger_ref,
        "release": release.to_dict(),
        "entry_count": len(entries),
        "entries": [_entry_payload(entry) for entry in entries],
        "target_changelog": target_changelog,
        "release_date": effective_date,
        "included_statuses": list(statuses),
        "status_counts": status_counts,
        "filtered_counts": filtered_counts,
        "source_refs": sorted(
            set(release.source_refs)
            | {ref for entry in entries for ref in entry.source_refs}
        ),
        "boundary_ref": release.boundary_ref,
        "warnings": warnings,
    }

    if format_name == "json":
        return context
    return _render_markdown(
        project_name=project_name,
        release=release,
        entries=entries,
        target_changelog=target_changelog,
        release_date=effective_date,
        include_sources=include_sources,
        included_statuses=statuses,
        filtered_counts=filtered_counts,
    )
