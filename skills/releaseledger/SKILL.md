---
name: releaseledger
description: Manage project-local release records, release entries, changelog source, and CHANGELOG.md builds
license: Apache-2.0
compatibility: opencode
metadata:
  audience: coding-agents
  workflow: release-management
---

## When to use this skill

Use releaseledger when a project needs durable, project-local release state: release records, release notes, changelog entries, generated changelog source, or updates to `CHANGELOG.md`.

Releaseledger is separate from taskledger. Do not treat `.releaseledger/` as task state and do not require taskledger to be installed.

## Never do these things

- Do not edit `.releaseledger/` storage files directly. Use releaseledger commands or the public `releaseledger.api.*` surface.
- Do not invent a release date. Use the date explicitly provided by the user, the persisted `released_at` value, or an unreleased heading.
- Do not include internal entries unless the user explicitly asks for internal release notes or passes an include-internal option.
- Do not silently overwrite an existing release section in `CHANGELOG.md`. Use the supported replace/update option only when explicitly requested.
- Do not duplicate an existing release heading.
- Do not remove existing historical changelog sections.
- Do not change release status just to build a changelog.
- Do not import or call `releaseledger.storage.*`, `releaseledger.services.*`, or `releaseledger.domain.*` from ad-hoc scripts during normal release work. Use the CLI or public `releaseledger.api.*`.
- Do not use path traversal, absolute paths, or state directories outside the configured workspace.
- Do not treat generated changelog source as final prose unless the command requested a final build.

## Core agent command path

Use this path first for routine release work:

```text
releaseledger --version
releaseledger init
releaseledger release list
releaseledger release show VERSION
releaseledger release create VERSION
releaseledger release tag VERSION
releaseledger release finalize VERSION
releaseledger entry add VERSION --kind KIND --summary TEXT
releaseledger entry list VERSION
releaseledger changelog VERSION --format markdown|json
releaseledger build VERSION --dry-run
releaseledger build VERSION --target-file CHANGELOG.md
```

Root options belong before the subcommand:

```text
releaseledger --cwd PATH --json release show VERSION
```

## Fresh context entry protocol

1. Run `releaseledger --version`.
2. Run `releaseledger release list`.
3. For a known release, run `releaseledger release show VERSION`.
4. Run `releaseledger entry list VERSION`.
5. Generate machine context when needed:
   `releaseledger changelog VERSION --format json`.
6. Do not inspect `.releaseledger/` internals unless the CLI cannot start and the user explicitly requested forensic inspection.

## Release creation protocol

1. Create a planned or candidate release:
   `releaseledger release create VERSION --title "Release VERSION"`.
2. Set `--previous VERSION` when the previous version is known and should appear in generated context.
3. Set `--released-at YYYY-MM-DD` only when the date is known.
4. Use `releaseledger release tag VERSION` for an immediately released/tagged release.
5. Use `releaseledger release finalize VERSION --released-at YYYY-MM-DD` to transition an existing planned/draft/candidate release to released.
6. Verify with:
   `releaseledger release show VERSION`.

## Changelog entry protocol

Use this when the user asks to add release-note material.

1. Resolve the target version:
   `releaseledger release show VERSION`.
2. Add entries with one of the controlled kinds:
   `added`, `changed`, `fixed`, `removed`, `deprecated`, `security`, `docs`, `internal`.
3. Keep summaries one line, user-facing, and free of trailing periods unless the project style requires punctuation.
4. Use `--body` for longer explanation and `--path`, `--issue`, and `--pr` for traceability.
5. Use `--breaking` for breaking changes.
6. Use `--internal` only for implementation-only notes that should be hidden from public changelogs by default.
7. Verify with:
   `releaseledger entry list VERSION`.

Example:

```bash
releaseledger entry add 1.2.0 --kind added \
  --summary "Add release bundle storage" \
  --path releaseledger/storage/store.py
```

## Changelog source protocol

Use this when the user wants release-note source material for review or drafting.

```bash
releaseledger changelog VERSION --target-changelog CHANGELOG.md --release-date YYYY-MM-DD
releaseledger changelog VERSION --format json
releaseledger changelog VERSION --include-internal
```

Rules:

1. Treat `releaseledger changelog VERSION` as source/context unless the command name or option explicitly says build/update.
2. Check whether internal entries were filtered.
3. Preserve warnings, release metadata, and entry grouping when handing source to a human or another tool.
4. If no date is provided and the release has no persisted `released_at`, keep the output unreleased or explicitly say no date was available.

## CHANGELOG.md build protocol

Use this when the user asks to build, generate, or update `CHANGELOG.md`.

1. Generate a dry run first:
   `releaseledger build VERSION --dry-run --target-file CHANGELOG.md`.
2. Inspect the rendered section:
   - heading version is correct
   - release date is exact, omitted, or marked unreleased according to user intent
   - internal entries are absent unless requested
   - groups appear in deterministic order
   - breaking changes are visible
3. Apply the build:
   `releaseledger build VERSION --target-file CHANGELOG.md`.
4. Read `CHANGELOG.md` back and verify:
   - no duplicate release heading exists
   - new section is below `## Unreleased` when that heading exists
   - prior release history is preserved
   - the file has one final newline
5. If the target already has the version section, do not replace it unless the user explicitly requested replacement. Use the supported replace flag and state that replacement was used.

## Templating protocol

Releaseledger changelog templates are configured in `.releaseledger.toml` under `[changelog]`.

Expected keys:

```toml
[changelog]
output = "CHANGELOG.md"
trim = true
render_always = false
header = ""
body = """
## {% if release.date %}[{{ release.version }}] - {{ release.date }}{% else %}[{{ release.version }}] - Unreleased{% endif %}

{% for group in groups %}
### {{ group.title }}
{% for entry in group.entries %}
- {% if entry.breaking %}**BREAKING:** {% endif %}{{ entry.summary }}
{% endfor %}

{% endfor %}
"""
footer = "<!-- generated by releaseledger -->"
postprocessors = []
```

Template context should include at least:

```text
project.name
release.version
release.title
release.status
release.date
release.previous_version
release.changelog_file
entries
groups
releases
```

Use templates only for rendering. Do not let templates mutate releaseledger state or read files.

## JSON mode protocol

When machine output is needed, `--json` is root-level:

```bash
releaseledger --json release show 1.2.0
releaseledger --json build 1.2.0 --dry-run
```

Do not append `--json` after the subcommand unless releaseledger explicitly adds that local option later.

## CLI failure protocol

If a `releaseledger ...` command fails with a Python traceback:

1. Stop mutating release state.
2. Run exactly one read-only probe:
   `releaseledger --version`.
3. If startup still fails, report that the releaseledger CLI is broken and no mutation was recorded.
4. If startup succeeds, rerun the failed command once with the same arguments.
5. For repeated failure, inspect command help and use explicit options rather than guessing.

## Public API protocol

Prefer CLI for agent work. If Python integration is required, import only public modules:

```python
from releaseledger.api.releases import create_release, tag_release, show_release
from releaseledger.api.entries import add_release_entry
from releaseledger.api.config import load_project_locator, render_default_releaseledger_toml
```

Do not couple external code to internal storage paths or private service functions unless the user explicitly requests package development work.
