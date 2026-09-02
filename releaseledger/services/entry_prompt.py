"""Releaseledger-native prompt generation for drafting release entries."""

from __future__ import annotations

from pathlib import Path

from releaseledger.domain.entry import validate_source_refs
from releaseledger.domain.states import ENTRY_KINDS, ENTRY_STATUSES
from releaseledger.errors import CODE_VALIDATION_ERROR, LaunchError
from releaseledger.storage.store import load_entries, load_release

__all__ = ["build_entry_prompt"]


def build_entry_prompt(
    workspace_root: Path,
    *,
    release_version: str,
    source_refs: tuple[str, ...] = (),
    context_file: Path | None = None,
    format_name: str = "markdown",
) -> str | dict[str, object]:
    """Build an entry-writing prompt from releaseledger and opaque evidence."""
    release = load_release(workspace_root, release_version)
    entries = load_entries(workspace_root, release_version)
    refs = validate_source_refs(source_refs)
    context: str | None = None
    if context_file is not None:
        try:
            context = context_file.read_text(encoding="utf-8")
        except OSError as exc:
            raise LaunchError(
                f"Cannot read --context-file {context_file}: {exc}",
                code=CODE_VALIDATION_ERROR,
                exit_code=2,
            ) from exc
    payload: dict[str, object] = {
        "kind": "entry_prompt",
        "release_version": release_version,
        "release": release.to_dict(),
        "existing_entries": [entry.to_dict() for entry in entries],
        "source_refs": list(refs),
        "context": context,
        "allowed_kinds": sorted(ENTRY_KINDS),
        "allowed_statuses": sorted(ENTRY_STATUSES),
    }
    if format_name == "json":
        return payload
    if format_name != "markdown":
        raise LaunchError(
            f"Unsupported prompt format: {format_name!r}",
            code=CODE_VALIDATION_ERROR,
            exit_code=2,
        )
    refs_text = ", ".join(refs) if refs else "(none supplied)"
    evidence = context if context is not None else "(no external context supplied)"
    batch_path = f"/tmp/{release_version}-entries.yaml"
    return f"""# Release entry drafting prompt for {release_version}

Use only the release metadata, existing entries, source refs, and caller-supplied
evidence below. Do not inspect taskledger storage or invent changes.

## Rules

- Allowed kinds: {", ".join(sorted(ENTRY_KINDS))}
- Allowed statuses: {", ".join(sorted(ENTRY_STATUSES))}
- Write one-line summaries of at most 180 characters.
- Prefer summaries at most 120 characters.
- Start summaries with Added, Changed, Fixed, Documented, or Improved.
- Do not use Markdown headings, TODO markers, unchecked boxes, or trailing periods.
- Use canonical global refs such as `tl:task-0103` in `source_refs`.

## Release

- Version: {release.version}
- Status: {release.status}
- Previous version: {release.previous_version or "none"}
- Boundary ref: {release.boundary_ref or "none"}
- Requested source refs: {refs_text}

## Existing entries

{chr(10).join(f"- {entry.kind}: {entry.summary}" for entry in entries) or "(none)"}

## Caller-supplied evidence

```text
{evidence}
```

## YAML output skeleton

```yaml
entries:
  - kind: changed
    summary: Changed ...
    body: >-
      ...
    status: accepted
    audience: developer
    scopes: [cli]
    source_refs: [{", ".join(refs) if refs else "tl:task-0103"}]
```

## Write workflow

```bash
releaseledger entry apply {release_version} --file {batch_path} --dry-run
releaseledger entry apply {release_version} --file {batch_path}
releaseledger entry lint {release_version} --strict
releaseledger entry list {release_version}
```
"""
