"""Changelog build service.

Renders a final, template-driven changelog section for a release and inserts or
replaces it deterministically in the target ``CHANGELOG.md``. This is distinct
from :mod:`releaseledger.services.changelog`, which renders agent-facing changelog
*source/context*; this module renders the *final* human changelog section.

The data source is releaseledger release records and ``ReleaseEntryRecord``
entries — never Git commits. Templates use a sandboxed Jinja2 environment
(``{{ ... }}`` expressions, ``{% ... %}`` statements) and may access
``project``, ``release``, ``entries``, ``groups``, and ``releases``.
"""

from __future__ import annotations

import datetime
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import ledgercore
from jinja2 import StrictUndefined
from jinja2.exceptions import SecurityError, TemplateError
from jinja2.sandbox import SandboxedEnvironment

from releaseledger.domain.entry import ReleaseEntryRecord, normalize_entry_status
from releaseledger.domain.release import ReleaseRecord, parse_release_version_tuple
from releaseledger.domain.states import (
    DEFAULT_KEEPACHANGELOG_KIND_MAP,
    ENTRY_KIND_TITLES,
    KEEPACHANGELOG_GROUP_ORDER,
    KEEPACHANGELOG_GROUP_TITLES,
)
from releaseledger.errors import (
    CODE_CONFLICT,
    CODE_NOT_FOUND,
    CODE_USAGE_ERROR,
    CODE_VALIDATION_ERROR,
    LaunchError,
)
from releaseledger.services.entry_lint import lint_release_entries
from releaseledger.services.git_sources import (
    collect_git_candidates,
    resolve_release_snapshot,
)
from releaseledger.storage.config import (
    DEFAULT_CHANGELOG,
    KEEPACHANGELOG_PREAMBLE,
    ProjectConfig,
    load_project_config,
)
from releaseledger.storage.paths import ProjectPaths, resolve_project_paths
from releaseledger.storage.store import list_releases, load_entries, load_release

__all__ = [
    "build_changelog_file",
    "build_changelog_render_context",
    "build_full_changelog_file",
    "extract_unreleased_section_body",
    "find_release_section",
    "insert_release_section",
    "render_changelog_section",
    "render_full_changelog_document",
    "remove_release_section",
    "rename_release_section",
    "replace_release_section",
]

# Fixed group order for rendered changelog output (extended mode).
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

# ``--release-date`` must be an ISO calendar date.
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Release section heading detection: ``## [1.2.0] ...`` or ``## 1.2.0 ...``.
_HEADING_RE_TEMPLATE = r"^## \[?{version}\]?\b.*$"

# Top-level (``#``) and second-level (``##``) heading line detection.
_TOP_TITLE_RE = re.compile(r"^#\s+\S")
_LEVEL2_RE = re.compile(r"^##\s+\S")
_UNRELEASED_RE = re.compile(r"^##\s+\[?\s*Unreleased\s*\]?\s*$", re.IGNORECASE)


def _project_name(paths: ProjectPaths) -> str:
    try:
        from releaseledger.storage.config import project_name_or_default

        return project_name_or_default(paths.project)
    except Exception:  # pragma: no cover - defensive fallback
        return paths.workspace_root.name or "releaseledger"


def _load_config(paths: ProjectPaths) -> ProjectConfig:
    try:
        return load_project_config(paths.config_path)
    except LaunchError:
        return ProjectConfig()


def _entry_payload(entry: ReleaseEntryRecord) -> dict[str, object]:
    return {
        "entry_id": entry.entry_id,
        "kind": entry.kind,
        "title": ENTRY_KIND_TITLES.get(entry.kind, entry.kind.capitalize()),
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
    *,
    config: ProjectConfig | None = None,
) -> list[tuple[str, list[dict[str, object]]]]:
    if config and config.changelog_group_mode == "keepachangelog":
        return _grouped_entries_keepachangelog(entries, config)
    return _grouped_entries_extended(entries)


def _grouped_entries_extended(
    entries: list[ReleaseEntryRecord],
) -> list[tuple[str, list[dict[str, object]]]]:
    grouped: list[tuple[str, list[dict[str, object]]]] = []
    for kind in _GROUP_ORDER:
        members = [_entry_payload(e) for e in entries if e.kind == kind]
        if members:
            grouped.append((kind, members))
    return grouped


def _grouped_entries_keepachangelog(
    entries: list[ReleaseEntryRecord],
    config: ProjectConfig,
) -> list[tuple[str, list[dict[str, object]]]]:
    mapped: dict[str, list[dict[str, object]]] = {
        k: [] for k in KEEPACHANGELOG_GROUP_ORDER
    }
    for entry in entries:
        effective_group = DEFAULT_KEEPACHANGELOG_KIND_MAP.get(entry.kind, "changed")
        mapped[effective_group].append(_entry_payload(entry))
    grouped: list[tuple[str, list[dict[str, object]]]] = []
    for group_key in KEEPACHANGELOG_GROUP_ORDER:
        members = mapped[group_key]
        if members:
            grouped.append((group_key, members))
    return grouped


def _groups_payload(
    grouped: list[tuple[str, list[dict[str, object]]]],
    *,
    config: ProjectConfig | None = None,
) -> list[dict[str, object]]:
    if config and config.changelog_group_mode == "keepachangelog":
        titles = KEEPACHANGELOG_GROUP_TITLES
    else:
        titles = ENTRY_KIND_TITLES
    return [
        {
            "kind": kind,
            "title": titles.get(kind, kind.capitalize()),
            "entries": members,
        }
        for kind, members in grouped
    ]


def _effective_date(
    *,
    release: ReleaseRecord,
    release_date: str | None,
    unreleased: bool,
) -> str | None:
    if unreleased:
        return None
    return release_date or release.released_at


def _resolve_release_date(value: str | None) -> str | None:
    if value is None:
        return None
    if not _DATE_RE.match(value):
        raise LaunchError(
            f"Invalid --release-date {value!r}; expected YYYY-MM-DD.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    # Validate real calendar date
    try:
        datetime.date.fromisoformat(value)
    except ValueError as exc:
        raise LaunchError(
            f"Invalid --release-date {value!r}; not a valid calendar date.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        ) from exc
    return value


def build_changelog_render_context(
    workspace_root: Path,
    *,
    version: str,
    include_internal: bool = False,
    release_date: str | None = None,
    unreleased: bool = False,
    include_statuses: tuple[str, ...] = ("accepted",),
) -> dict[str, object]:
    """Build the deterministic render context for ``version``.

    The context exposes ``project``, ``release``, ``entries``, ``groups``, and
    ``releases``. Internal entries are filtered unless ``include_internal`` is
    true. ``release.date``/``release.released_at`` reflect the effective date:
    ``--release-date`` overrides ``released_at``; ``--unreleased`` forces None.
    """
    paths = resolve_project_paths(workspace_root)
    config = _load_config(paths)
    release = load_release(workspace_root, version)
    all_entries = load_entries(workspace_root, version)
    statuses = tuple(normalize_entry_status(value) for value in include_statuses)
    entries = [
        entry
        for entry in all_entries
        if entry.status in statuses and (include_internal or not entry.internal)
    ]
    project_name = _project_name(paths)
    effective_date = _effective_date(
        release=release,
        release_date=_resolve_release_date(release_date),
        unreleased=unreleased,
    )
    grouped = _grouped_entries(entries, config=config)

    release_payload: dict[str, object] = {
        "version": release.version,
        "title": release.title or f"Release {release.version}",
        "status": release.status,
        "yanked": release.status == "yanked",
        "date": effective_date,
        "released_at": effective_date,
        "previous_version": release.previous_version,
        "changelog_file": release.changelog_file,
        "entry_count": len(entries),
        "boundary_ref": release.boundary_ref,
        "source_refs": list(release.source_refs),
        "tag": f"v{release.version}",
    }

    releases_list: list[dict[str, object]] = []
    try:
        for record in list_releases(workspace_root):
            releases_list.append(
                {
                    "version": record.version,
                    "date": record.released_at,
                }
            )
    except Exception:  # pragma: no cover - defensive: list is best-effort
        releases_list = []

    status_counts = {
        status: sum(entry.status == status for entry in all_entries)
        for status in ("accepted", "draft", "rejected")
    }
    warnings: list[str] = []
    if "draft" in statuses and status_counts["draft"]:
        warnings.append("Draft entries are included; output is draft-quality.")
    return {
        "project": {"name": project_name},
        "release": release_payload,
        "entries": [_entry_payload(e) for e in entries],
        "groups": _groups_payload(grouped, config=config),
        "releases": releases_list,
        "included_statuses": list(statuses),
        "status_counts": status_counts,
        "warnings": warnings,
    }


def _make_environment(
    *, trim_blocks: bool, lstrip_blocks: bool
) -> SandboxedEnvironment:
    env = SandboxedEnvironment(
        trim_blocks=trim_blocks,
        lstrip_blocks=lstrip_blocks,
        keep_trailing_newline=False,
        autoescape=False,
        undefined=StrictUndefined,
    )
    return env


def _render_template(
    env: SandboxedEnvironment, source: str, context: dict[str, object]
) -> str:
    try:
        template = env.from_string(source)
        return template.render(**context)
    except SecurityError as exc:
        raise LaunchError(
            f"Changelog template rejected for security: {exc}",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        ) from exc
    except TemplateError as exc:
        raise LaunchError(
            f"Changelog template render failed: {exc}",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        ) from exc


def _trim_section(text: str) -> str:
    """Collapse 3+ consecutive newlines to a single blank line and strip ends."""
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip("\n")


def _literal_replacer(replacement: str) -> Callable[[re.Match[str]], str]:
    """Build a typed re.sub callback for literal (non-backreference) replace."""

    def _replace(_match: re.Match[str]) -> str:
        return replacement

    return _replace


def _apply_postprocessors(text: str, postprocessors: tuple[dict[str, str], ...]) -> str:
    for step in postprocessors:
        # Literal replacement: a closure returning the replacement so the
        # replacement string is never interpreted for backreferences.
        text = re.sub(step["pattern"], _literal_replacer(step["replace"]), text)
    return text


def _extract_heading(section: str) -> str | None:
    for line in section.splitlines():
        if _LEVEL2_RE.match(line):
            return line.strip()
    return None


def _resolve_template_profile(
    config: ProjectConfig,
    template_name: str,
) -> dict[str, Any]:
    """Resolve a template profile from config.

    Returns the template profile dict, or an empty dict for the default profile.
    Raises LaunchError if a named template is not found.
    """
    if template_name == "default":
        return {}

    if not config.changelog_templates:
        raise LaunchError(
            f"Template {template_name!r} not found. No templates configured.",
            code=CODE_USAGE_ERROR,
            exit_code=2,
            remediation=["Add [changelog.templates.NAME] to .releaseledger.toml"],
        )

    if template_name not in config.changelog_templates:
        available = sorted(config.changelog_templates.keys())
        avail_str = ", ".join(available)
        raise LaunchError(
            f"Template {template_name!r} not found. Available: {avail_str}",
            code=CODE_USAGE_ERROR,
            exit_code=2,
            remediation=[f"Use one of: {', '.join(available)}"],
        )

    return config.changelog_templates[template_name]


def render_changelog_section(
    workspace_root: Path,
    *,
    version: str,
    include_internal: bool = False,
    release_date: str | None = None,
    unreleased: bool = False,
    template_name: str = "default",
    include_statuses: tuple[str, ...] = ("accepted",),
) -> dict[str, object]:
    """Render the final changelog section for ``version`` without writing files.

    Returns render metadata and the section text. The section has exactly one
    trailing newline. ``section_heading`` is the first ``##`` line in the
    rendered section (or None if the template produced none).

    If ``template_name`` is not ``"default"``, looks up the template profile
    from ``config.changelog_templates[template_name]``. Raises ``LaunchError``
    if the template is not found.
    """
    paths = resolve_project_paths(workspace_root)
    config = _load_config(paths)

    # Resolve template profile
    template_config = _resolve_template_profile(config, template_name)

    context = build_changelog_render_context(
        workspace_root,
        version=version,
        include_internal=include_internal,
        release_date=release_date,
        unreleased=unreleased,
        include_statuses=include_statuses,
    )

    trim_blocks = bool(template_config.get("trim", config.changelog_trim))
    env = _make_environment(trim_blocks=trim_blocks, lstrip_blocks=trim_blocks)
    render_context = dict(context)

    parts: list[str] = []
    header = template_config.get("header", config.changelog_header)
    if header.strip():
        parts.append(_render_template(env, header, render_context))
    body = template_config.get("body", config.changelog_body)
    parts.append(_render_template(env, body, render_context))
    footer = template_config.get("footer", config.changelog_footer)
    if footer.strip():
        parts.append(_render_template(env, footer, render_context))

    section = "\n\n".join(part for part in parts if part)
    if template_config.get("trim", config.changelog_trim):
        section = _trim_section(section)
    postprocessors = template_config.get(
        "postprocessors", config.changelog_postprocessors
    )
    section = _apply_postprocessors(section, postprocessors)
    # Normalize newlines and ensure exactly one final newline.
    section = ledgercore.normalize_newlines(section)
    section = section.strip("\n") + "\n"

    release_payload = context["release"]
    assert isinstance(release_payload, dict)
    entry_count = int(release_payload.get("entry_count", 0))
    effective_date = release_payload.get("date")

    warnings: list[str] = []
    context_warnings = context.get("warnings", [])
    if isinstance(context_warnings, list):
        warnings.extend(str(item) for item in context_warnings)
    if entry_count == 0 and not config.changelog_render_always:
        warnings.append("Release has no changelog entries; rendered an empty section.")

    return {
        "kind": "changelog_build",
        "version": version,
        "section": section,
        "section_heading": _extract_heading(section),
        "entry_count": entry_count,
        "included_internal": bool(include_internal),
        "release_date": effective_date,
        "template_name": template_name,
        "warnings": warnings,
        "included_statuses": context["included_statuses"],
        "status_counts": context["status_counts"],
    }


@dataclass(frozen=True)
class _Span:
    start: int  # line index (inclusive)
    end: int  # line index (exclusive)


def find_release_section(text: str, version: str) -> _Span | None:
    """Locate an existing release section for ``version``.

    Returns the inclusive-start, exclusive-end line indices, or None if the
    version heading is absent. The section runs from its ``## [?]VERSION[?]``
    heading through just before the next ``## `` heading or EOF.
    """
    escaped = re.escape(version)
    heading_re = re.compile(_HEADING_RE_TEMPLATE.format(version=escaped), re.MULTILINE)
    lines = text.splitlines(keepends=True)
    heading_line_index: int | None = None
    for index, line in enumerate(lines):
        if heading_re.match(line):
            heading_line_index = index
            break
    if heading_line_index is None:
        return None
    end = len(lines)
    for index in range(heading_line_index + 1, len(lines)):
        if _LEVEL2_RE.match(lines[index]):
            end = index
            break
    return _Span(start=heading_line_index, end=end)


def _ensure_final_newline(text: str) -> str:
    text = ledgercore.normalize_newlines(text)
    if text == "":
        return text
    return text if text.endswith("\n") else text + "\n"


# ---------------------------------------------------------------------------
# Link reference management for Keep a Changelog 1.1.0
# ---------------------------------------------------------------------------

_LINK_REF_RE = re.compile(r"^\[([^\]]+)\]:\s+(\S+)\s*$")


def _format_tag(version: str, tag_prefix: str) -> str:
    """Format a version as a git tag."""
    if tag_prefix and version.startswith(tag_prefix):
        return version
    return f"{tag_prefix}{version}"


def parse_changelog_link_refs(text: str) -> dict[str, str]:
    """Parse existing link references from changelog text.

    Returns a dict mapping reference names to URLs.
    Only parses references at the end of the file (after all content).
    """
    refs: dict[str, str] = {}
    lines = text.splitlines()
    # Find the last non-empty, non-link-ref line
    last_content_idx = len(lines) - 1
    while last_content_idx >= 0:
        line = lines[last_content_idx].strip()
        if line and not _LINK_REF_RE.match(line):
            break
        last_content_idx -= 1

    # Parse link refs after the last content line
    for line in lines[last_content_idx + 1 :]:
        match = _LINK_REF_RE.match(line.strip())
        if match:
            refs[match.group(1)] = match.group(2)
    return refs


def render_release_link(
    config: ProjectConfig,
    version: str,
    previous_version: str | None = None,
) -> str | None:
    """Render a link reference for a release version.

    Returns the link reference line, or None if repository_url is not configured.
    """
    if not config.changelog_repository_url:
        return None

    repo_url = config.changelog_repository_url.rstrip("/")
    current_tag = _format_tag(version, config.changelog_tag_prefix)

    if config.changelog_compare_url_template:
        # Use custom template
        previous_tag = (
            _format_tag(previous_version, config.changelog_tag_prefix)
            if previous_version
            else ""
        )
        url = config.changelog_compare_url_template.format(
            previous=previous_version or "",
            current=version,
            previous_tag=previous_tag,
            current_tag=current_tag,
        )
    elif previous_version:
        previous_tag = _format_tag(previous_version, config.changelog_tag_prefix)
        url = f"{repo_url}/compare/{previous_tag}...{current_tag}"
    else:
        url = f"{repo_url}/releases/tag/{current_tag}"

    return f"[{version}]: {url}"


def render_unreleased_link(
    config: ProjectConfig,
    latest_version: str | None = None,
) -> str | None:
    """Render a link reference for the Unreleased section.

    Returns the link reference line, or None if repository_url is not configured.
    """
    if not config.changelog_repository_url:
        return None

    repo_url = config.changelog_repository_url.rstrip("/")

    if latest_version:
        latest_tag = _format_tag(latest_version, config.changelog_tag_prefix)
        url = f"{repo_url}/compare/{latest_tag}...HEAD"
    else:
        url = f"{repo_url}"

    return f"[Unreleased]: {url}"


def update_changelog_link_refs(
    text: str,
    new_refs: dict[str, str],
) -> str:
    """Update link references in changelog text without deleting unrelated refs.

    Only updates/adds refs that are in ``new_refs``. Preserves all other refs.
    """
    lines = text.splitlines(keepends=True)

    # Find the last non-empty, non-link-ref line
    last_content_idx = len(lines) - 1
    while last_content_idx >= 0:
        line = lines[last_content_idx].strip()
        if line and not _LINK_REF_RE.match(line):
            break
        last_content_idx -= 1

    # Split into content and existing refs
    content_lines = lines[: last_content_idx + 1]
    existing_ref_lines = lines[last_content_idx + 1 :]

    # Parse existing refs
    existing_refs: dict[str, str] = {}
    for line in existing_ref_lines:
        match = _LINK_REF_RE.match(line.strip())
        if match:
            existing_refs[match.group(1)] = match.group(2)

    # Merge: new_refs override existing, but keep unrelated refs
    merged_refs = {**existing_refs, **new_refs}

    # Rebuild the file
    result_lines = list(content_lines)
    if merged_refs:
        # Add a blank line before refs if content doesn't end with one
        if result_lines and result_lines[-1].strip():
            result_lines.append("\n")
        for name, url in sorted(merged_refs.items()):
            result_lines.append(f"[{name}]: {url}\n")

    return _ensure_final_newline("".join(result_lines))


def insert_release_section(
    text: str,
    section: str,
    *,
    config: ProjectConfig | None = None,
    version: str = "",
) -> str:
    """Insert a rendered release section into existing changelog ``text``.

    Insertion precedence:
    1. below ``## Unreleased`` (before the next ``## `` heading), if present;
    2. before the first ``## `` heading, if any;
    3. after the title/intro (first ``# `` line and following non-heading lines);
    4. otherwise create a new changelog with a ``# Changelog`` title.

    In Keep a Changelog mode, case 4 creates a full skeleton with preamble,
    ``## [Unreleased]`` section, and optional link references.

    ``section`` must already have exactly one trailing newline.
    """
    lines = text.splitlines(keepends=True)
    section = _ensure_final_newline(section)

    # 1. Below ## Unreleased.
    unreleased_idx = next(
        (i for i, line in enumerate(lines) if _UNRELEASED_RE.match(line)), None
    )
    if unreleased_idx is not None:
        insert_at = len(lines)
        for index in range(unreleased_idx + 1, len(lines)):
            if _LEVEL2_RE.match(lines[index]):
                insert_at = index
                break
        return _splice(lines, insert_at, section)

    # 2. Before the first ## heading.
    first_level2 = next(
        (i for i, line in enumerate(lines) if _LEVEL2_RE.match(line)), None
    )
    if first_level2 is not None:
        return _splice(lines, first_level2, section)

    # 3. After the title/intro.
    top_title_idx = next(
        (i for i, line in enumerate(lines) if _TOP_TITLE_RE.match(line)), None
    )
    if top_title_idx is not None:
        after = top_title_idx + 1
        while after < len(lines) and not lines[after].strip():
            after += 1
        # Skip non-heading intro lines directly under the title.
        while (
            after < len(lines)
            and lines[after].strip()
            and not lines[after].lstrip().startswith("#")
        ):
            after += 1
        return _splice(lines, after, section)

    # 4. New changelog.
    if config and config.changelog_standard == "keepachangelog-1.1.0":
        return _create_keepachangelog_skeleton(section, config=config, version=version)
    body = "# Changelog\n\n" + section
    return _ensure_final_newline(body)


def _create_keepachangelog_skeleton(
    section: str,
    *,
    config: ProjectConfig,
    version: str = "",
) -> str:
    """Create a full Keep a Changelog 1.1.0 skeleton for a new file."""
    parts = ["# Changelog"]

    # Add preamble.
    preamble = config.changelog_preamble or KEEPACHANGELOG_PREAMBLE
    if preamble.strip():
        parts.append("")
        parts.append(preamble.strip())

    # Add Unreleased section.
    parts.append("")
    parts.append("## [Unreleased]")

    # Add the release section.
    parts.append("")
    parts.append(section.strip())

    # Add link references if repository_url is configured.
    if config.changelog_link_references and config.changelog_repository_url:
        repo_url = config.changelog_repository_url.rstrip("/")
        tag_prefix = config.changelog_tag_prefix
        parts.append("")
        # Unreleased link
        if version:
            tag = (
                f"{tag_prefix}{version}"
                if not version.startswith(tag_prefix)
                else version
            )
            parts.append(f"[Unreleased]: {repo_url}/compare/{tag}...HEAD")
            # Release link
            parts.append(f"[{version}]: {repo_url}/releases/tag/{tag}")
        else:
            parts.append(f"[Unreleased]: {repo_url}")
    elif config.changelog_link_references and not config.changelog_repository_url:
        # Warn if link_references is on but no repository_url
        pass  # Warning will be handled by the caller

    return _ensure_final_newline("\n".join(parts))


def replace_release_section(text: str, version: str, section: str) -> str:
    """Replace an existing release section for ``version`` with ``section``."""
    span = find_release_section(text, version)
    if span is None:
        # Nothing to replace; fall back to insertion.
        return insert_release_section(text, section)
    lines = text.splitlines(keepends=True)
    section = _ensure_final_newline(section)
    new_lines = lines[: span.start] + [section] + lines[span.end :]
    return _ensure_final_newline("".join(new_lines))


def remove_release_section(
    text: str,
    version: str,
    *,
    ignore_missing: bool = False,
) -> str:
    """Remove the changelog section for ``version``.

    Fails unless ``ignore_missing`` is set when the section is absent. Preserves
    all other sections and the final newline. Never invoked by commands that do
    not explicitly remove/cancel/rename a section.
    """
    span = find_release_section(text, version)
    if span is None:
        if ignore_missing:
            return _ensure_final_newline(text)
        raise LaunchError(
            f"Changelog has no section for {version}.",
            code=CODE_NOT_FOUND,
            exit_code=2,
            remediation=["Pass --ignore-missing to skip a missing section."],
        )
    lines = text.splitlines(keepends=True)
    new_lines = lines[: span.start] + lines[span.end :]
    return _collapse_blank_runs(_ensure_final_newline("".join(new_lines)))


def rename_release_section(
    text: str,
    old_version: str,
    new_version: str,
    *,
    ignore_missing: bool = False,
    replace_existing: bool = False,
) -> str:
    """Rename the changelog section heading ``old_version`` to ``new_version``.

    Rewrites only the section heading line; the section body and every other
    section are preserved. Fails unless ``ignore_missing`` when the old section
    is absent, and fails unless ``replace_existing`` when a section for
    ``new_version`` already exists.
    """
    span = find_release_section(text, old_version)
    if span is None:
        if ignore_missing:
            return _ensure_final_newline(text)
        raise LaunchError(
            f"Changelog has no section for {old_version}.",
            code=CODE_NOT_FOUND,
            exit_code=2,
            remediation=["Pass --ignore-missing to skip a missing section."],
        )
    if find_release_section(text, new_version) is not None and not replace_existing:
        raise LaunchError(
            f"Changelog already has a section for {new_version}.",
            code=CODE_CONFLICT,
            exit_code=2,
            remediation=[
                "Pass --replace-existing to overwrite the destination section."
            ],
        )
    lines = text.splitlines(keepends=True)
    heading = lines[span.start]
    escaped = re.escape(old_version)
    new_heading = re.sub(escaped, _literal_replacer(new_version), heading, count=1)
    lines[span.start] = new_heading
    return _ensure_final_newline("".join(lines))


def _splice(lines: list[str], at: int, section: str) -> str:
    """Insert ``section`` at line index ``at``, preserving a blank separator."""
    section = _ensure_final_newline(section)
    # Keep exactly one blank line between the preceding block and the section.
    prefix = lines[:at]
    while prefix and not prefix[-1].strip():
        prefix.pop()
    rebuilt = list(prefix)
    if rebuilt:
        rebuilt.append("\n")
    rebuilt.append(section)
    # Ensure a blank line separates the new section from following content.
    tail = lines[at:]
    rebuilt.append("\n")
    rebuilt.extend(tail)
    return _collapse_blank_runs("".join(rebuilt))


def _collapse_blank_runs(text: str) -> str:
    text = ledgercore.normalize_newlines(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def _resolve_target_file(
    *,
    workspace_root: Path,
    config: ProjectConfig,
    target_file: Path | None,
) -> Path:
    if target_file is not None:
        chosen = str(target_file)
    elif config.changelog_output:
        chosen = config.changelog_output
    else:
        chosen = config.default_changelog or DEFAULT_CHANGELOG
    path = Path(chosen)
    resolved = path if path.is_absolute() else (workspace_root / path)
    return resolved


def _relative_target(workspace_root: Path, target: Path) -> str:
    try:
        return str(target.resolve().relative_to(workspace_root.resolve()))
    except ValueError:
        return str(target)


def _read_target(target: Path) -> str:
    if not target.is_file():
        return ""
    try:
        return target.read_text(encoding="utf-8")
    except OSError as exc:
        raise LaunchError(
            f"Failed to read changelog target {target}: {exc}",
            code=CODE_USAGE_ERROR,
            exit_code=2,
        ) from exc


# ---------------------------------------------------------------------------
# Full-build release-chain ordering
# ---------------------------------------------------------------------------


def _order_releases_for_changelog(
    releases: list[ReleaseRecord],
) -> tuple[list[ReleaseRecord], list[str]]:
    """Order releases by explicit previous_version chain, newest-first.

    Walks each chain from its head (a release not referenced as
    previous_version by any other release) and collects connected releases.
    Chain heads are ordered by date/semver fallback then walked back.
    Remaining disconnected releases are appended with fallback ordering.
    Returns the ordered list and any warnings.
    """
    by_version = {record.version: record for record in releases}
    fallback = sorted(releases, key=_full_changelog_release_key, reverse=True)
    if not fallback:
        return [], []

    # A release is a chain head if no other release points to it via previous_version.
    referenced: set[str] = set()
    for record in releases:
        if record.previous_version:
            referenced.add(record.previous_version)
    heads = [r for r in fallback if r.version not in referenced]

    ordered: list[ReleaseRecord] = []
    seen: set[str] = set()
    warnings: list[str] = []

    for head in heads:
        current: ReleaseRecord | None = head
        chain_seen: set[str] = set()
        while current is not None:
            if current.version in chain_seen:
                warnings.append(
                    f"Release chain cycle detected at {current.version}; "
                    "appending remaining releases with fallback ordering."
                )
                break
            chain_seen.add(current.version)
            if current.version not in seen:
                ordered.append(current)
                seen.add(current.version)

            previous = current.previous_version
            if not previous or previous not in by_version:
                current = None
            else:
                current = by_version[previous]

    remaining = [record for record in fallback if record.version not in seen]
    if remaining:
        warnings.append(
            "Some releases are disconnected from the primary previous_version chain: "
            + ", ".join(record.version for record in remaining)
        )
        ordered.extend(remaining)

    return ordered, warnings


# ---------------------------------------------------------------------------
# Generated Unreleased block markers
# ---------------------------------------------------------------------------

_GENERATED_UNRELEASED_START_RE = re.compile(
    r"^<!--\s*releaseledger:unreleased-start\s+version=(?P<version>[^>\s]+)\s*-->\s*$"
)
_GENERATED_UNRELEASED_END_RE = re.compile(
    r"^<!--\s*releaseledger:unreleased-end\s*-->\s*$"
)


def _wrap_generated_unreleased_body(version: str, body: str) -> str:
    body = body.strip("\n")
    if not body:
        return ""
    return (
        f"<!-- releaseledger:unreleased-start version={version} -->\n"
        f"{body}\n"
        "<!-- releaseledger:unreleased-end -->"
    )


def _strip_stale_generated_unreleased_body(
    body: str,
    *,
    selected_versions: set[str],
) -> tuple[str, str | None]:
    """Remove generated Unreleased blocks for finalized releases.

    Returns (cleaned_body, removed_version) where removed_version is None
    when no stale block was found.
    """
    lines = body.splitlines()
    start_idx: int | None = None
    version: str | None = None

    for index, line in enumerate(lines):
        match = _GENERATED_UNRELEASED_START_RE.match(line.strip())
        if match:
            start_idx = index
            version = match.group("version")
            break

    if start_idx is None or version is None:
        return body, None

    end_idx: int | None = None
    for index in range(start_idx + 1, len(lines)):
        if _GENERATED_UNRELEASED_END_RE.match(lines[index].strip()):
            end_idx = index
            break

    if end_idx is None:
        return body, None

    if version not in selected_versions:
        return body, None

    remaining = lines[:start_idx] + lines[end_idx + 1 :]
    return "\n".join(remaining).strip("\n"), version


# ---------------------------------------------------------------------------
# Duplicate bullet detection between Unreleased and releases
# ---------------------------------------------------------------------------

_BULLET_RE = re.compile(r"^\s*[-*]\s+(?P<body>.+?)\s*$")


def _normalized_changelog_bullets(text: str) -> set[str]:
    bullets: set[str] = set()
    for line in text.splitlines():
        match = _BULLET_RE.match(line)
        if not match:
            continue
        value = re.sub(r"\s+", " ", match.group("body").strip()).lower()
        if value:
            bullets.add(value)
    return bullets


def _duplicate_unreleased_release_bullets(
    unreleased_body: str,
    sections: list[str],
) -> set[str]:
    unreleased = _normalized_changelog_bullets(unreleased_body)
    released = _normalized_changelog_bullets("\n".join(sections))
    return unreleased & released


# ---------------------------------------------------------------------------
# Strict git-range coverage enforcement
# ---------------------------------------------------------------------------


def _strict_git_range_coverage(
    workspace_root: Path,
    *,
    release: ReleaseRecord,
    entries: list[ReleaseEntryRecord],
    all_entries: list[ReleaseEntryRecord],
    include_internal: bool,
    allow_empty: bool,
    statuses: tuple[str, ...],
) -> tuple[list[str], int]:
    """Enforce git-range commit coverage in strict builds.

    Returns (warnings, hidden_internal_commit_count). Raises LaunchError when
    a commit in the stored git range has no accepted entry coverage and
    allow_empty is False.
    """
    if not (release.git_base_sha or release.git_base_ref):
        return [], 0
    if not (release.git_head_sha or release.git_head_ref):
        return [], 0
    snapshot = resolve_release_snapshot(workspace_root, release)

    candidates = collect_git_candidates(
        workspace_root,
        base_ref=snapshot.base_spec,
        head_ref=snapshot.head_spec,
    )
    expected = {
        candidate.source_ref for candidate in candidates if candidate.include_by_default
    }
    if not expected:
        return [], 0

    accepted_refs = {
        ref
        for entry in all_entries
        if entry.status in statuses
        for ref in entry.source_refs
    }
    visible_refs = {ref for entry in entries for ref in entry.source_refs}

    missing = sorted(expected - accepted_refs)
    if missing and not allow_empty:
        raise LaunchError(
            f"Strict build for {release.version} has git commits not covered "
            "by accepted entries: " + ", ".join(missing),
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
            remediation=[
                "Run `releaseledger git import VERSION "
                "--base BASE --head HEAD --output entries.yaml`.",
                "Rewrite the generated entry summaries from patch evidence.",
                "Run `releaseledger entry add-many VERSION "
                "--file entries.yaml --dry-run`.",
                "Run `releaseledger review VERSION --git --strict` before building.",
            ],
        )

    hidden_internal_refs = (
        sorted(expected - visible_refs) if not include_internal else []
    )
    warnings: list[str] = []
    if hidden_internal_refs:
        warnings.append(
            f"{release.version}: {len(hidden_internal_refs)} git commit(s) are covered "
            "only by entries excluded from this build."
        )
    return warnings, len(hidden_internal_refs)


def _run_strict_build_checks(
    workspace_root: Path,
    *,
    version: str,
    release: ReleaseRecord,
    statuses: tuple[str, ...],
    selected: list[ReleaseEntryRecord],
    all_entries: list[ReleaseEntryRecord],
    include_internal: bool,
    allow_empty: bool,
) -> tuple[list[str], int]:
    """Run strict-mode validation checks for build_changelog_file.

    Returns (warnings, hidden_internal_commit_count). Raises LaunchError on
    violations.
    """
    strict_warnings: list[str] = []
    lint = lint_release_entries(
        workspace_root,
        release_version=version,
        strict=False,
        include_statuses=statuses,
    )
    summary = lint["summary"]
    assert isinstance(summary, dict)
    if int(summary["errors"]) > 0:
        raise LaunchError(
            "Strict build blocked by entry lint errors.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    if not selected and not allow_empty:
        raise LaunchError(
            "Strict build requires at least one included entry; "
            "pass --allow-empty to override.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    release_refs = set(release.source_refs)
    if release.boundary_ref:
        release_refs.add(release.boundary_ref)
    entry_refs = {ref for entry in selected for ref in entry.source_refs}
    uncovered = sorted(release_refs - entry_refs)
    if uncovered and not allow_empty:
        raise LaunchError(
            "Strict build has release source refs not referenced by entries: "
            + ", ".join(uncovered),
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    if int(summary["warnings"]) > 0:
        strict_warnings.append(f"Entry lint reported {summary['warnings']} warning(s).")
    git_warnings, hidden_internal_commit_count = _strict_git_range_coverage(
        workspace_root,
        release=release,
        entries=selected,
        all_entries=all_entries,
        include_internal=include_internal,
        allow_empty=allow_empty,
        statuses=statuses,
    )
    strict_warnings.extend(git_warnings)
    return strict_warnings, hidden_internal_commit_count


def build_changelog_file(
    workspace_root: Path,
    *,
    version: str,
    target_file: Path | None = None,
    include_internal: bool = False,
    release_date: str | None = None,
    unreleased: bool = False,
    template_name: str = "default",
    dry_run: bool = False,
    replace_existing: bool = False,
    include_canceled: bool = False,
    include_statuses: tuple[str, ...] = ("accepted",),
    strict: bool = False,
    allow_empty: bool = False,
) -> dict[str, object]:
    """Render and optionally update the target changelog for ``version``.

    Dry runs return ``updated=False`` with the rendered ``section``. Non-dry
    runs refuse an existing section for ``version`` unless ``replace_existing``
    is set, then atomically write the merged changelog. Returns a deterministic
    ``changelog_build`` result payload.
    """
    workspace_root = workspace_root.expanduser().resolve()
    paths = resolve_project_paths(workspace_root)
    config = _load_config(paths)
    target = _resolve_target_file(
        workspace_root=workspace_root, config=config, target_file=target_file
    )
    release = load_release(workspace_root, version)
    if release.status == "canceled" and not include_canceled:
        raise LaunchError(
            f"Release {version} is canceled and cannot be rendered "
            "as an active release section.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
            remediation=[
                "Use `build --all` to rebuild active releases, or pass "
                "--include-canceled for archival/debug rendering."
            ],
        )
    statuses = tuple(normalize_entry_status(value) for value in include_statuses)
    all_entries = load_entries(workspace_root, version)
    selected = [
        entry
        for entry in all_entries
        if entry.status in statuses and (include_internal or not entry.internal)
    ]
    strict_warnings: list[str] = []
    hidden_internal_commit_count = 0
    if strict:
        strict_warnings, hidden_internal_commit_count = _run_strict_build_checks(
            workspace_root,
            version=version,
            release=release,
            statuses=statuses,
            selected=selected,
            all_entries=all_entries,
            include_internal=include_internal,
            allow_empty=allow_empty,
        )

    # In Keep a Changelog mode with strict, require a date for released sections
    is_kac = config.changelog_standard == "keepachangelog-1.1.0"
    if strict and is_kac and not unreleased:
        effective_date = release_date or release.released_at
        if not effective_date:
            raise LaunchError(
                "Strict build in Keep a Changelog mode requires a release date "
                "for released sections. Pass --release-date or --unreleased.",
                code=CODE_VALIDATION_ERROR,
                exit_code=2,
            )

    rendered = render_changelog_section(
        workspace_root,
        version=version,
        include_internal=include_internal,
        release_date=release_date,
        unreleased=unreleased,
        template_name=template_name,
        include_statuses=statuses,
    )
    section = str(rendered["section"])
    section_heading = rendered["section_heading"]

    existing = _read_target(target)
    span = find_release_section(existing, version)
    raw_warnings = rendered.get("warnings", [])
    warnings: list[str] = []
    if isinstance(raw_warnings, list):
        warnings = [str(item) for item in raw_warnings]
    warnings.extend(strict_warnings)
    replaced_existing = False

    excluded_internal_count = sum(
        1
        for entry in all_entries
        if entry.status in statuses and entry.internal and not include_internal
    )
    excluded_draft_count = sum(
        1
        for entry in all_entries
        if entry.status == "draft" and "draft" not in statuses
    )
    excluded_rejected_count = sum(
        1
        for entry in all_entries
        if entry.status == "rejected" and "rejected" not in statuses
    )

    if dry_run:
        return {
            "kind": "changelog_build",
            "version": version,
            "target_file": _relative_target(workspace_root, target),
            "updated": False,
            "dry_run": True,
            "replaced_existing": bool(span is not None),
            "section": section,
            "section_heading": section_heading,
            "entry_count": rendered["entry_count"],
            "included_internal": bool(include_internal),
            "included_statuses": list(statuses),
            "status_counts": rendered["status_counts"],
            "warnings": warnings,
            "excluded_internal_count": excluded_internal_count,
            "excluded_draft_count": excluded_draft_count,
            "excluded_rejected_count": excluded_rejected_count,
            "hidden_internal_git_commit_count": hidden_internal_commit_count,
        }

    if span is not None:
        if not replace_existing:
            raise LaunchError(
                f"Changelog already has a section for {version} in {target}.",
                code=CODE_CONFLICT,
                exit_code=2,
                remediation=[
                    "Re-run with --replace-existing to overwrite the section."
                ],
            )
        merged = replace_release_section(existing, version, section)
        replaced_existing = True
    else:
        merged = insert_release_section(
            existing, section, config=config, version=version
        )

    # Update link references if in Keep a Changelog mode
    is_kac = config.changelog_standard == "keepachangelog-1.1.0"
    if is_kac and config.changelog_link_references:
        new_refs: dict[str, str] = {}
        # Find the previous version for compare links
        previous_version = release.previous_version
        release_link = render_release_link(config, version, previous_version)
        if release_link:
            # Extract the ref name and URL
            match = _LINK_REF_RE.match(release_link)
            if match:
                new_refs[match.group(1)] = match.group(2)
        # Add unreleased link
        unreleased_link = render_unreleased_link(config, version)
        if unreleased_link:
            match = _LINK_REF_RE.match(unreleased_link)
            if match:
                new_refs[match.group(1)] = match.group(2)
        if new_refs:
            merged = update_changelog_link_refs(merged, new_refs)

    merged = _ensure_final_newline(merged)
    try:
        ledgercore.ensure_dir(target.parent)
        ledgercore.atomic_write_text(target, merged)
    except (ledgercore.AtomicWriteError, OSError) as exc:
        raise LaunchError(
            f"Failed to write changelog target {target}: {exc}",
            code=CODE_USAGE_ERROR,
            exit_code=2,
        ) from exc

    return {
        "kind": "changelog_build",
        "version": version,
        "target_file": _relative_target(workspace_root, target),
        "updated": True,
        "dry_run": False,
        "replaced_existing": replaced_existing,
        "section_heading": section_heading,
        "entry_count": rendered["entry_count"],
        "included_internal": bool(include_internal),
        "included_statuses": list(statuses),
        "status_counts": rendered["status_counts"],
        "warnings": warnings,
        "excluded_internal_count": excluded_internal_count,
        "excluded_draft_count": excluded_draft_count,
        "excluded_rejected_count": excluded_rejected_count,
        "hidden_internal_git_commit_count": hidden_internal_commit_count,
    }


# ---------------------------------------------------------------------------
# Full-document changelog rebuild (``releaseledger build`` / ``build --all``)
# ---------------------------------------------------------------------------


_UNRELEASED_HEADING_RE = re.compile(r"^##\s+\[?\s*Unreleased\s*\]?\s*$", re.IGNORECASE)


def extract_unreleased_section_body(text: str) -> str:
    """Return the body text under a ``## [Unreleased]`` heading.

    The body excludes the heading line itself and any following ``## `` section.
    Returns an empty string when the file has no Unreleased section. Trailing
    blank lines are stripped; the result has no leading or trailing newline.
    """
    lines = text.splitlines(keepends=True)
    start: int | None = None
    for index, line in enumerate(lines):
        if _UNRELEASED_HEADING_RE.match(line):
            start = index + 1
            break
    if start is None:
        return ""
    end = len(lines)
    for index in range(start, len(lines)):
        if _LEVEL2_RE.match(lines[index]) or _LINK_REF_RE.match(lines[index].strip()):
            end = index
            break
    body = "".join(lines[start:end])
    return body.strip("\n")


def _full_changelog_release_key(record: ReleaseRecord) -> tuple[object, ...]:
    """Sort selected releases newest-first for full-document rendering.

    Mirrors :func:`storage.store._release_sort_key` but reversed for newest-first
    document ordering. Uses released_at then semantic version then raw version.
    """
    released_at = record.released_at or ""
    semver = parse_release_version_tuple(record.version)
    if semver is not None:
        semver_component: tuple[object, ...] = (0, *semver)
    else:
        semver_component = (1, record.version)
    return (released_at, semver_component, record.version)


def _render_full_link_refs(
    config: ProjectConfig,
    releases: list[ReleaseRecord],
    *,
    include_unreleased: bool = False,
) -> dict[str, str]:
    """Build the deterministic link-reference map for the selected release chain.

    Includes ``[Unreleased]`` comparing from the newest selected release to HEAD
    when ``include_unreleased`` is true and a repository URL is configured.
    Returns an empty dict when link refs are disabled or no repository URL is set.
    """
    refs: dict[str, str] = {}
    if not (config.changelog_link_references and config.changelog_repository_url):
        return refs
    for release in releases:
        line = render_release_link(config, release.version, release.previous_version)
        if line:
            match = _LINK_REF_RE.match(line)
            if match:
                refs[match.group(1)] = match.group(2)
    if include_unreleased:
        newest = releases[0].version if releases else None
        unreleased_line = render_unreleased_link(config, newest)
        if unreleased_line:
            match = _LINK_REF_RE.match(unreleased_line)
            if match:
                refs[match.group(1)] = match.group(2)
    return refs


def render_full_changelog_document(
    *,
    config: ProjectConfig,
    sections: list[str],
    unreleased_body: str = "",
    link_refs: dict[str, str] | None = None,
) -> str:
    """Assemble the full changelog document from rendered release sections.

    Layout: ``# Changelog`` title, optional preamble (Keep a Changelog mode), an
    optional ``## [Unreleased]`` section preserving ``unreleased_body``, the
    release sections newest-first, and finally the generated-by marker plus the
    link-reference block. The result ends with exactly one newline.
    """
    parts: list[str] = ["# Changelog"]
    is_kac = config.changelog_standard == "keepachangelog-1.1.0"
    preamble = config.changelog_preamble
    if is_kac and not preamble.strip():
        preamble = KEEPACHANGELOG_PREAMBLE
    if preamble.strip():
        parts.append("")
        parts.append(preamble.strip())
    body = unreleased_body.strip("\n")
    if body:
        parts.append("")
        parts.append("## [Unreleased]")
        parts.append("")
        parts.append(body)
    for section in sections:
        section = section.strip("\n")
        if not section:
            continue
        parts.append("")
        parts.append(section)
    if config.changelog_footer.strip():
        parts.append("")
        parts.append(config.changelog_footer.strip())
    refs = link_refs or {}
    for name, url in refs.items():
        parts.append(f"[{name}]: {url}")
    document = "\n".join(parts)
    document = ledgercore.normalize_newlines(document)
    document = re.sub(r"\n{3,}", "\n\n", document)
    return _ensure_final_newline(document)


def render_release_groups_body(
    workspace_root: Path,
    *,
    version: str,
    include_internal: bool = False,
    template_name: str = "default",
    include_statuses: tuple[str, ...] = ("accepted",),
) -> dict[str, object]:
    """Render a release's entries as grouped body text without a version heading.

    Used to fold a ``planned``/``draft``/``candidate`` release into the canonical
    ``## [Unreleased]`` section. Renders the configured body template with
    ``unreleased=True`` and strips the leading ``## [VERSION] - Unreleased``
    heading line so only the groups remain. Returns the body text and the
    included entry count.
    """
    paths = resolve_project_paths(workspace_root)
    config = _load_config(paths)
    template_config = _resolve_template_profile(config, template_name)
    context = build_changelog_render_context(
        workspace_root,
        version=version,
        include_internal=include_internal,
        unreleased=True,
        include_statuses=include_statuses,
    )
    trim_blocks = bool(template_config.get("trim", config.changelog_trim))
    env = _make_environment(trim_blocks=trim_blocks, lstrip_blocks=trim_blocks)
    body_template = template_config.get("body", config.changelog_body)
    rendered = _render_template(env, body_template, dict(context))
    out: list[str] = []
    skipped = False
    for line in rendered.splitlines():
        if not skipped and _LEVEL2_RE.match(line):
            skipped = True
            continue
        out.append(line)
    body = "\n".join(out).strip("\n")
    postprocessors = template_config.get(
        "postprocessors", config.changelog_postprocessors
    )
    body = _apply_postprocessors(body, postprocessors)
    body = ledgercore.normalize_newlines(body).strip("\n")
    release_payload = context["release"]
    assert isinstance(release_payload, dict)
    entry_count = int(release_payload.get("entry_count", 0))
    return {"body": body, "entry_count": entry_count}


def _validate_folded_unreleased_strict(
    workspace_root: Path,
    *,
    folded: ReleaseRecord,
    include_internal: bool,
    statuses: tuple[str, ...],
    allow_empty: bool,
    warnings: list[str],
) -> None:
    """Apply strict lint/coverage/entries checks to a folded unreleased release.

    Skips the released_at requirement (the folded release is intentionally
    unreleased) but still requires entries, lint success, and source-ref coverage.
    """
    unreleased_version = folded.version
    folded_entries = [
        entry
        for entry in load_entries(workspace_root, unreleased_version)
        if entry.status in statuses and (include_internal or not entry.internal)
    ]
    folded_lint = lint_release_entries(
        workspace_root,
        release_version=unreleased_version,
        strict=False,
        include_statuses=statuses,
    )
    folded_summary = folded_lint["summary"]
    assert isinstance(folded_summary, dict)
    if int(folded_summary["errors"]) > 0:
        raise LaunchError(
            f"Strict full build blocked by entry lint errors for folded "
            f"unreleased {unreleased_version}.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    if not folded_entries and not allow_empty:
        raise LaunchError(
            f"Strict full build requires at least one included entry for "
            f"folded unreleased {unreleased_version}; pass --allow-empty.",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    release_refs = set(folded.source_refs)
    if folded.boundary_ref:
        release_refs.add(folded.boundary_ref)
    entry_refs = {ref for entry in folded_entries for ref in entry.source_refs}
    uncovered = sorted(release_refs - entry_refs)
    if uncovered and not allow_empty:
        raise LaunchError(
            f"Strict full build for folded unreleased {unreleased_version} has "
            "release source refs not referenced by entries: " + ", ".join(uncovered),
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    if int(folded_summary["warnings"]) > 0:
        warnings.append(
            f"{unreleased_version}: entry lint reported "
            f"{folded_summary['warnings']} warning(s)."
        )


def build_full_changelog_file(  # noqa: C901
    workspace_root: Path,
    *,
    target_file: Path | None = None,
    include_internal: bool = False,
    template_name: str = "default",
    dry_run: bool = False,
    include_statuses: tuple[str, ...] = ("accepted",),
    include_release_statuses: tuple[str, ...] = ("released",),
    strict: bool = False,
    allow_empty: bool = False,
    preserve_unreleased: bool = True,
    unreleased_version: str | None = None,
) -> dict[str, object]:
    """Rebuild the full changelog target from releaseledger state.

    Selects releases whose status is in ``include_release_statuses`` (default
    ``released``) and never includes ``canceled``. Renders each release section
    newest-first using the existing single-release renderer, preserves the
    existing ``## [Unreleased]`` body when ``preserve_unreleased`` is true, and
    regenerates the link-reference block deterministically. A whole-file rewrite:
    ``--replace-existing`` does not apply.

    Returns a deterministic ``changelog_full_build`` payload.
    """
    workspace_root = workspace_root.expanduser().resolve()
    paths = resolve_project_paths(workspace_root)
    config = _load_config(paths)
    target = _resolve_target_file(
        workspace_root=workspace_root, config=config, target_file=target_file
    )
    statuses = tuple(normalize_entry_status(value) for value in include_statuses)
    release_statuses = set(include_release_statuses)
    is_kac = config.changelog_standard == "keepachangelog-1.1.0"

    existing = _read_target(target)
    unreleased_body = (
        extract_unreleased_section_body(existing) if preserve_unreleased else ""
    )

    all_releases = list_releases(workspace_root)
    selected = [
        record
        for record in all_releases
        if record.status in release_statuses and record.status != "canceled"
    ]

    sections: list[str] = []
    results: list[dict[str, object]] = []
    warnings: list[str] = []

    selected, order_warnings = _order_releases_for_changelog(selected)
    warnings.extend(order_warnings)

    unreleased_version_rendered: str | None = None
    unreleased_entry_count = 0
    if unreleased_version:
        folded = load_release(workspace_root, unreleased_version)
        if folded.status not in {"planned", "draft", "candidate"}:
            raise LaunchError(
                f"--unreleased-version {unreleased_version} requires a release "
                f"with status planned, draft, or candidate (got {folded.status!r}).",
                code=CODE_VALIDATION_ERROR,
                exit_code=2,
            )
        if strict:
            _validate_folded_unreleased_strict(
                workspace_root,
                folded=folded,
                include_internal=include_internal,
                statuses=statuses,
                allow_empty=allow_empty,
                warnings=warnings,
            )
        rendered_unreleased = render_release_groups_body(
            workspace_root,
            version=unreleased_version,
            include_internal=include_internal,
            template_name=template_name,
            include_statuses=statuses,
        )
        folded_body = str(rendered_unreleased["body"])
        if folded_body.strip():
            unreleased_body = _wrap_generated_unreleased_body(
                unreleased_version, folded_body
            )
            unreleased_version_rendered = unreleased_version
            unreleased_entry_count = int(str(rendered_unreleased["entry_count"]))
        # Exclude the folded release from normal release sections.
        selected = [r for r in selected if r.version != unreleased_version]

    # Strip stale generated Unreleased blocks for finalized releases.
    selected_versions = {record.version for record in selected}
    if preserve_unreleased and unreleased_body:
        unreleased_body, removed_unreleased_version = (
            _strip_stale_generated_unreleased_body(
                unreleased_body,
                selected_versions=selected_versions,
            )
        )
        if removed_unreleased_version:
            warnings.append(
                f"Removed generated Unreleased body for finalized release "
                f"{removed_unreleased_version}."
            )

    for release in selected:
        version = release.version
        all_version_entries = load_entries(workspace_root, version)
        version_entries = [
            entry
            for entry in all_version_entries
            if entry.status in statuses and (include_internal or not entry.internal)
        ]
        if strict:
            lint = lint_release_entries(
                workspace_root,
                release_version=version,
                strict=False,
                include_statuses=statuses,
            )
            lint_summary = lint["summary"]
            assert isinstance(lint_summary, dict)
            if int(lint_summary["errors"]) > 0:
                raise LaunchError(
                    f"Strict full build blocked by entry lint errors for {version}.",
                    code=CODE_VALIDATION_ERROR,
                    exit_code=2,
                )
            if not version_entries and not allow_empty:
                raise LaunchError(
                    f"Strict full build requires at least one included entry "
                    f"for {version}; pass --allow-empty to override.",
                    code=CODE_VALIDATION_ERROR,
                    exit_code=2,
                )
            if is_kac and not release.released_at:
                raise LaunchError(
                    f"Strict full build requires a release date for {version} in "
                    "Keep a Changelog mode.",
                    code=CODE_VALIDATION_ERROR,
                    exit_code=2,
                )
            release_refs = set(release.source_refs)
            if release.boundary_ref:
                release_refs.add(release.boundary_ref)
            entry_refs = {ref for entry in version_entries for ref in entry.source_refs}
            uncovered = sorted(release_refs - entry_refs)
            if uncovered and not allow_empty:
                raise LaunchError(
                    f"Strict full build for {version} has release source refs not "
                    "referenced by entries: " + ", ".join(uncovered),
                    code=CODE_VALIDATION_ERROR,
                    exit_code=2,
                )
            if int(lint_summary["warnings"]) > 0:
                warnings.append(
                    f"{version}: entry lint reported "
                    f"{lint_summary['warnings']} warning(s)."
                )
            git_warnings, _ = _strict_git_range_coverage(
                workspace_root,
                release=release,
                entries=version_entries,
                all_entries=all_version_entries,
                include_internal=include_internal,
                allow_empty=allow_empty,
                statuses=statuses,
            )
            warnings.extend(git_warnings)
        rendered = render_changelog_section(
            workspace_root,
            version=version,
            include_internal=include_internal,
            template_name=template_name,
            include_statuses=statuses,
        )
        sections.append(str(rendered["section"]).strip())
        raw_render_warnings = rendered.get("warnings", [])
        if isinstance(raw_render_warnings, list):
            warnings.extend(f"{version}: {item}" for item in raw_render_warnings)
        entry_count_raw = rendered["entry_count"]
        results.append(
            {
                "version": version,
                "entry_count": (
                    entry_count_raw if isinstance(entry_count_raw, int) else 0
                ),
                "section_heading": rendered["section_heading"],
            }
        )

    duplicates = _duplicate_unreleased_release_bullets(unreleased_body, sections)
    if duplicates:
        msg = (
            "Unreleased contains entries that also appear in released sections: "
            + "; ".join(sorted(duplicates))
        )
        if strict:
            raise LaunchError(
                msg,
                code=CODE_VALIDATION_ERROR,
                exit_code=2,
                remediation=[
                    "Run with --no-preserve-unreleased if the "
                    "Unreleased body is stale.",
                    "Or edit the manual Unreleased notes before rebuilding.",
                ],
            )
        warnings.append(msg)

    link_refs = _render_full_link_refs(
        config, selected, include_unreleased=bool(unreleased_body.strip())
    )
    document = render_full_changelog_document(
        config=config,
        sections=sections,
        unreleased_body=unreleased_body,
        link_refs=link_refs,
    )
    document = _ensure_final_newline(document)

    total_excluded_internal = 0
    total_excluded_draft = 0
    total_excluded_rejected = 0
    total_hidden_internal_commits = 0
    for release in selected:
        all_rel_entries = load_entries(workspace_root, release.version)
        total_excluded_internal += sum(
            1
            for entry in all_rel_entries
            if entry.status in statuses and entry.internal and not include_internal
        )
        total_excluded_draft += sum(
            1
            for entry in all_rel_entries
            if entry.status == "draft" and "draft" not in statuses
        )
        total_excluded_rejected += sum(
            1
            for entry in all_rel_entries
            if entry.status == "rejected" and "rejected" not in statuses
        )
        if strict:
            base_ref = release.git_base_ref or release.git_base_sha
            head_ref = release.git_head_ref or release.git_head_sha
            if base_ref and head_ref:
                candidates = collect_git_candidates(
                    workspace_root,
                    base_ref=base_ref,
                    head_ref=head_ref,
                )
                expected = {
                    candidate.source_ref
                    for candidate in candidates
                    if candidate.include_by_default
                }
                visible_refs = {
                    ref
                    for entry in load_entries(workspace_root, release.version)
                    if entry.status in statuses
                    and (include_internal or not entry.internal)
                    for ref in entry.source_refs
                }
                hidden = sorted(expected - visible_refs) if not include_internal else []
                total_hidden_internal_commits += len(hidden)

    versions = [str(item["version"]) for item in results]
    payload: dict[str, object] = {
        "kind": "changelog_full_build",
        "target_file": _relative_target(workspace_root, target),
        "updated": False,
        "dry_run": bool(dry_run),
        "release_count": len(results),
        "versions": versions,
        "releases": results,
        "included_internal": bool(include_internal),
        "included_statuses": list(statuses),
        "included_release_statuses": sorted(release_statuses),
        "unreleased_preserved": bool(preserve_unreleased) and bool(unreleased_body),
        "document": document,
        "warnings": warnings,
        "unreleased_version": unreleased_version_rendered,
        "unreleased_entry_count": unreleased_entry_count,
        "unreleased_rendered": unreleased_version_rendered is not None,
        "excluded_internal_count": total_excluded_internal,
        "excluded_draft_count": total_excluded_draft,
        "excluded_rejected_count": total_excluded_rejected,
        "hidden_internal_git_commit_count": total_hidden_internal_commits,
    }

    if dry_run:
        return payload

    try:
        ledgercore.ensure_dir(target.parent)
        ledgercore.atomic_write_text(target, document)
    except (ledgercore.AtomicWriteError, OSError) as exc:
        raise LaunchError(
            f"Failed to write changelog target {target}: {exc}",
            code=CODE_USAGE_ERROR,
            exit_code=2,
        ) from exc
    payload["updated"] = True
    return payload


# Silence unused-import analyzers for re-exported Any used only in annotations.
_ = Any
