[![PyPI - Version](https://img.shields.io/pypi/v/releaseledger)](https://pypi.org/project/releaseledger/)
![PyPI - Python Version](https://img.shields.io/pypi/pyversions/releaseledger)
![PyPI - Downloads](https://img.shields.io/pypi/dm/releaseledger)
[![codecov](https://codecov.io/gh/holgern/releaseledger/graph/badge.svg?token=XH3tdO0CqJ)](https://codecov.io/gh/holgern/releaseledger)

# releaseledger

Project-local release management for coding workflows.

`releaseledger` is a standalone release-state ledger for Python projects and
other source repositories. It records releases, release-note entries, operation
events, and JSON indexes in a deterministic file layout. It can also render
reviewable changelog context and write final `CHANGELOG.md` sections from
releaseledger entries.

typed file-storage primitives. It does not import `taskledger`, inspect
`.taskledger/`, or validate task state. Cross-ledger provenance is represented
only as explicit refs such as `tl:task-0103`.

**Git-first.** Releaseledger uses git commit ranges as the canonical evidence
of shipped changes. Git tags and commit ranges define the shipped change set.
Taskledger, issue trackers, and PR descriptions are optional provenance context.
Release notes are generated from the commits reachable from the release target
and absent from the previous release target:

```bash
releaseledger git range 1.2.0 --base v1.1.0 --head HEAD
releaseledger git import 1.2.0 --base v1.1.0 --head HEAD --output entries.yaml
releaseledger review 1.2.0 --git --strict
```

## What releaseledger stores

After `releaseledger init`, a project has a `.releaseledger.toml` config file and
a state directory, usually `.releaseledger/`:

```text
.releaseledger/
  ledgers/
    main/
      releases/
        1.2.0/
          release.md
          entries/
            entry-0001.md
      events/
        events.jsonl
      indexes/
        releases.json
        entries.json
```

Release records and entries are Markdown files with YAML front matter. Every
mutation appends an operation event to `events.jsonl` and rebuilds the JSON
indexes. Events omit wall-clock timestamps and before/after deltas; git history
provides chronology and record revisions validate file changes.

## Install

```bash
python -m pip install releaseledger
```

For local development:

```bash
python -m pip install -e ".[dev]"
```

The package exposes the console command `releaseledger` and supports
`python -m releaseledger`.

## Quickstart

```bash
# 1. Initialize.
releaseledger init

# 2. Create the release and attach the git range.
releaseledger release create 1.2.0 \
  --previous 1.1.0 \
  --released-at 2026-06-14

releaseledger release update 1.2.0 \
  --git-base v1.1.0 \
  --git-head HEAD

# 3. Generate git candidate entries from the commit range.
releaseledger git import 1.2.0 \
  --base v1.1.0 \
  --head HEAD \
  --status draft \
  --output /tmp/1.2.0-git-entries.yaml

# Edit the YAML summaries, then:
releaseledger entry add-many 1.2.0 --file /tmp/1.2.0-git-entries.yaml --dry-run
releaseledger entry add-many 1.2.0 --file /tmp/1.2.0-git-entries.yaml

# 4. Generate a durable per-commit review worksheet, inspect every patch,
#    and validate it covers the git range.
releaseledger audit init 1.2.0
releaseledger audit show 1.2.0 --output /tmp/1.2.0-commit-audit.md
releaseledger audit sync 1.2.0
releaseledger audit validate 1.2.0 --strict --include-internal

# 5. Review git coverage and the audit sheet.
releaseledger review 1.2.0 --git --strict --include-internal --require-audit-sheet


# 6. Build the changelog section for this release.
releaseledger build 1.2.0 \
  --release-date 2026-06-14 \
  --strict \
  --target-file CHANGELOG.md

# Or rebuild the COMPLETE changelog from ledger state:
releaseledger build --dry-run --strict --target-file CHANGELOG.md
releaseledger build --target-file CHANGELOG.md
```

Taskledger refs (`tl:task-0103`) and PR refs (`github:pr-42`) are optional
provenance — use them to enrich entries, but git commits are the coverage source.

`changelog` produces agent-facing context for review or drafting. `build`
renders and inserts the final changelog section.

## Core concepts

| Concept           | Meaning                                                                                                   |
| ----------------- | --------------------------------------------------------------------------------------------------------- |
| Release           | A versioned release record with status, optional previous version, source boundary, and changelog target. |
| Entry             | One release-note item attached to a release. Entries are grouped by kind for changelog output.            |
| Event             | Append-only operation marker with affected record revisions.                                              |
| Versioning        | Per-record metadata whose revision increases exactly once when a release or entry file changes.           |
| Index             | Deterministic JSON summary rebuilt after mutations for fast inspection.                                   |
| Ledger ref        | Branch-scoped namespace, defaulting to `main`.                                                            |
| Global source ref | External provenance token such as `tl:task-0103`; releaseledger records it but does not resolve it.       |

Release statuses are `planned`, `draft`, `candidate`, `released`, `yanked`, and
`canceled` (never shipped; excluded from previous-version inference and not
built into public changelogs by default).
Entry statuses are `draft`, `accepted`, and `rejected`. Builds include accepted
entries by default.

Entry kinds are `added`, `changed`, `fixed`, `removed`, `deprecated`,
`security`, `docs`, `quality`, and `internal`. `documentation` and `doc` are
accepted aliases for `docs`.

## Commands

```text
releaseledger init [--releaseledger-dir PATH] [--project-name NAME]
                  [--external-dir] [--force]

releaseledger release create VERSION [--title TEXT] [--status STATUS]
                                     [--previous VERSION] [--note TEXT]
                                     [--changelog-file PATH]
                                     [--released-at YYYY-MM-DD]
                                     [--boundary-ref REF]
                                     [--source-ref REF]...
                                     [--source-count N]
releaseledger release update VERSION [same metadata options]
                                     [--clear-previous]
                                     [--clear-changelog-file]
                                     [--clear-boundary-ref]
                                     [--clear-source-refs]
                                     [--clear-source-count]
                                     [--clear-released-at] [--force]
releaseledger release tag VERSION [release metadata options]
releaseledger release finalize VERSION [--released-at YYYY-MM-DD]
                                       [--changelog-file PATH]
releaseledger release cancel VERSION [--reason TEXT]
                                    [--superseded-by VERSION]
                                    [--force-released-unshipped]
                                    [--target-file PATH]
                                    [--remove-changelog-section]
                                    [--ignore-missing]
releaseledger release rename OLD_VERSION NEW_VERSION [--previous VERSION]
                                                      [--title TEXT]
                                                      [--released-at YYYY-MM-DD]
                                                      [--force-released-unshipped]
                                                      [--rewrite-successors]
                                                      [--target-file PATH]
                                                      [--rename-changelog-section]
                                                      [--replace-existing-section]
releaseledger release chain check
releaseledger release chain repair [--dry-run] [--apply]
releaseledger release list
releaseledger release show VERSION

releaseledger entry add VERSION --kind KIND --summary TEXT [--body TEXT]
                               [--status STATUS] [--audience TEXT]
                               [--scope SCOPE]... [--source-ref REF]...
                               [--path PATH]... [--issue REF]... [--pr REF]...
                               [--breaking] [--internal] [--dry-run]
releaseledger entry add-many VERSION --file FILE [--dry-run]
releaseledger entry update VERSION ENTRY_ID [entry metadata options]
releaseledger entry show VERSION ENTRY_ID
releaseledger entry import VERSION --file FILE [--replace]
                                   [--source-ledger LEDGER]
releaseledger entry list VERSION
releaseledger entry lint VERSION [--strict] [--include-status STATUS]...
releaseledger entry prompt VERSION [--source-ref REF]...
                                   [--context-file FILE]
                                   [--format markdown|json]
                                   [--output PATH]

releaseledger changelog VERSION [--format markdown|json] [--output PATH]
                                [--include-internal]
                                [--target-changelog PATH]
                                [--release-date YYYY-MM-DD]
                                [--include-sources]
                                [--include-status STATUS]... [--lint]

releaseledger build VERSION [--target-file PATH]
                            [--release-date YYYY-MM-DD]
                            [--unreleased]
                            [--include-internal]
                            [--template NAME]
                            [--dry-run]
                            [--replace-existing]
                            [--format markdown|json]
                            [--include-status STATUS]...
                            [--strict]
                            [--allow-empty]
releaseledger build [VERSION] [--all] [--target-file PATH]
                            [--include-release-status STATUS]...
                            [--preserve-unreleased|--no-preserve-unreleased]
                            [--include-internal]
                            [--include-status STATUS]... [--strict]
                            [--dry-run] [--allow-empty]
releaseledger review VERSION [--include-internal]
                       [--include-status STATUS]...
                       [--target-file PATH] [--strict]
                       [--git] [--git-base REF] [--git-head REF]
                       [--require-audit-sheet]
releaseledger audit init VERSION [--base REF] [--head REF] [--overwrite]
releaseledger audit show VERSION [--format markdown|json] [--output PATH]
releaseledger audit update VERSION --file PATH
releaseledger audit validate VERSION [--strict] [--include-internal]
releaseledger audit sync VERSION

releaseledger git range VERSION [--base REF] [--head REF]
                      [--include-merges never|always|nontrivial]
releaseledger git range next --base REF [--head REF]
releaseledger git import VERSION --base REF [--head REF]
                      [--status draft] --output PATH
releaseledger git import next --base REF [--head REF] --output PATH

releaseledger branch status
releaseledger branch start BRANCH --parent PARENT
releaseledger branch merge BRANCH --into TARGET --release VERSION

releaseledger changelog-section remove-section VERSION --target-file PATH
                                              [--ignore-missing] [--dry-run]
releaseledger changelog-section rename-section OLD_VERSION NEW_VERSION
                                              --target-file PATH
                                              [--ignore-missing]
                                              [--replace-existing] [--dry-run]

releaseledger storage where
releaseledger config show
releaseledger config set releaseledger_dir PATH [--external-dir]
```

Root options:

```text
releaseledger --cwd PATH ...
releaseledger --json ...
releaseledger --version
```

## Batch entries

`entry add-many` reads YAML with a top-level `entries` list:

```yaml
entries:
  - kind: added
    summary: Added release bundle storage
    body: >-
      The storage layer now writes release records, entries, events, and indexes.
    status: accepted
    audience: developer
    scopes: [storage]
    source_refs: [tl:task-0103]
    paths:
      - releaseledger/storage/store.py
    issues: []
    prs: []
    breaking: false
    internal: false
```

Run a dry run before writing:

```bash
releaseledger entry add-many 1.2.0 --file /tmp/1.2.0-entries.yaml --dry-run
releaseledger entry add-many 1.2.0 --file /tmp/1.2.0-entries.yaml
```

## Changelog generation

There are two changelog commands:

`releaseledger changelog` builds review context. It is useful for coding agents
or humans who need to inspect release metadata, included entries, target file
guidance, and lint findings before writing final prose. Add
`--include-sources` when the Markdown output should show provenance refs.

`releaseledger build` renders the final section from `[changelog]` config and
inserts it into the target file. It can run in `--dry-run` mode, replace an
existing release section with `--replace-existing`, or render an unreleased date
with `--unreleased`. Use `--template NAME` to select a named changelog template
profile.

Use the conventional **full rebuild** to regenerate the whole target file from
ledger state: `releaseledger build` with no `VERSION` (or `releaseledger build --all`) rewrites the entire document newest-first, preserves the
`## [Unreleased]` body by default, excludes internal entries and non-released
releases by default, and regenerates the link-reference block. `releaseledger build VERSION` keeps the single-section insert/replace behavior. Use
`--include-release-status` to include candidate/planned sections explicitly,
and `--include-internal` only for internal release notes.

Default `.releaseledger.toml` changelog template:

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

Templates run in a sandboxed Jinja2 environment and may access `project`,
`release`, `entries`, `groups`, and `releases`. Postprocessors are literal
string replacements:

```toml
postprocessors = [
  { pattern = "releaseledger", replace = "Releaseledger" },
]
```

## Release review

`releaseledger review VERSION` is a read-only coverage report that combines
release state, entry coverage, orphan detection, entry lint, and a strict
changelog dry-run into one deterministic report. Use it to answer "what did I
already add for this release?" without stitching together `release show`,
`entry list`, `entry lint`, `changelog`, and `build --dry-run`.

```bash
releaseledger review 0.5.0
releaseledger --json review 0.5.0
releaseledger review 0.5.0 --include-status accepted --include-status draft
releaseledger review 0.5.0 --strict --target-file CHANGELOG.md
```

Each expected source ref (`release.source_refs` plus `boundary_ref`) is
classified as `covered`, `draft_only`, `rejected_only`, `internal_only`, or
`missing`. Accepted entries with no provenance (empty `source_refs`, `issues`,
`prs`, and `sources`) are reported as orphans. Git hashes remain optional
evidence in entry `sources`; `source_refs` plus entry `status` are the
canonical change identity.

> Before adding a new entry, run `releaseledger review VERSION`. If the same
> `source_ref` is already covered by an accepted entry, update the existing
> entry instead of adding a duplicate.

## Correcting canceled or misnumbered releases

When a recorded release was never actually shipped (no git tag, no package
publish) or was recorded under the wrong version number, fix it with the
release-correction commands instead of editing `.releaseledger/` storage.

```bash
# 1. Verify the real shipped baseline first (git tags / package index / user).
git tag --list | sort -V | tail

# 2. Inspect the stored chain and repair a broken backfill.
releaseledger release chain check
releaseledger release chain repair --dry-run
releaseledger release chain repair --apply

# 3. Clear an optional field on a root release (e.g. v0.1.0).
releaseledger release update v0.1.0 --clear-previous

# 4. Rename an unshipped, misnumbered release and its changelog section.
releaseledger release rename v0.4.3 v0.5.0 \
  --previous v0.4.2 \
  --force-released-unshipped \
  --target-file CHANGELOG.md \
  --rename-changelog-section

# 5. Or keep the wrong version as a visible audit tombstone.
releaseledger release cancel v0.4.3 \
  --reason "Never shipped; superseded by v0.5.0" \
  --superseded-by v0.5.0 \
  --force-released-unshipped
```

Decision tree:

- Check shipped evidence first (git tags, changelog headings, explicit user
  statement).
- If a version was never shipped and the number was wrong, use
  `release rename`.
- If the wrong version should remain as an audit tombstone, use
  `release cancel` (sets status `canceled`; never use `yanked` for never-shipped
  releases).
- When backfilling old releases, always pass `--previous` explicitly and run
  `release chain check` afterwards.
- Build the changelog from the net shipped baseline, then bump the package
  version.

## Cross-ledger provenance

Releaseledger is intentionally standalone. To link work from another tool,
export that tool's evidence and pass it as opaque context:

```bash
taskledger task show task-0103 --json > /tmp/task-0103.json

releaseledger entry prompt 1.2.0 \
  --source-ref tl:task-0103 \
  --context-file /tmp/task-0103.json \
  --output /tmp/entry-prompt.md

releaseledger entry add-many 1.2.0 --file /tmp/1.2.0-entries.yaml --dry-run
releaseledger entry add-many 1.2.0 --file /tmp/1.2.0-entries.yaml
releaseledger entry lint 1.2.0 --strict
releaseledger build 1.2.0 --dry-run --strict --target-file CHANGELOG.md
```

The prompt command tells the drafting agent to use only releaseledger metadata,
explicit source refs, and caller-supplied context.

## JSON envelopes

Use `--json` for deterministic machine-readable output.

Success envelope:

```json
{
  "command": "release.tag",
  "events": ["event-0001"],
  "ok": true,
  "result": {
    "events": ["event-0001"],
    "kind": "release",
    "ledger_ref": "main",
    "release": {
      "status": "released",
      "version": "1.2.0"
    }
  },
  "result_type": "release"
}
```

Error envelope:

```json
{
  "command": "release.tag",
  "error": {
    "code": "USAGE_ERROR",
    "exit_code": 2,
    "message": "Release version already exists: 1.2.0",
    "remediation": ["Run `releaseledger release show 1.2.0`."]
  },
  "ok": false
}
```

Common error codes are `USAGE_ERROR`, `NOT_FOUND`, `CONFIG_ERROR`,
`VALIDATION_ERROR`, and `CONFLICT`.

## Configuration

Default local state:

```toml
# .releaseledger.toml
config_version = 1
releaseledger_dir = ".releaseledger"

ledger_ref = "main"
ledger_parent_ref = ""
ledger_next_entry_number = 1
ledger_branch_guard = "off"

[ledger]
code = "rl"
name = "releaseledger"

[release]
default_changelog = "CHANGELOG.md"
default_status = "planned"
allow_dirty_worktree = true
```

Projects that keep generated state in a sibling repository can opt in to an
external relative path:

```toml
releaseledger_dir = "../ledger/release/releaseledger"
releaseledger_dir_policy = "external"
```

The CLI equivalent is:

```bash
releaseledger init \
  --releaseledger-dir ../ledger/release/releaseledger \
  --external-dir

releaseledger config set releaseledger_dir \
  ../ledger/release/releaseledger \
  --external-dir
```

Relative paths that escape the workspace are rejected unless the external policy
is explicit. Absolute paths are accepted for compatibility but are not portable.

## Storage diagnostics

Inspect effective paths and layout health without mutating state:

```bash
releaseledger storage where
releaseledger --json storage where
releaseledger config show
releaseledger --json config show
```

Human output from `storage where` includes workspace, config path, storage path,
ledger ref, workspace containment, config source, layout status, and index
status.

## Python API

The public API is intentionally narrow and re-exported from `releaseledger.api`:

```python
from releaseledger.api.releases import (
    create_release,
    finalize_release,
    list_release_records,
    show_release,
    tag_release,
    update_release,
)
from releaseledger.api.entries import (
    add_many_release_entries,
    add_release_entry,
    build_entry_prompt,
    import_release_entry_file,
    lint_release_entries,
    list_release_entries,
    show_release_entry,
    update_release_entry,
)
from releaseledger.api.changelog import (
    build_changelog_context,
    build_changelog_file,
    render_changelog_section,
)
from releaseledger.api.config import (
    config_set_releaseledger_dir,
    config_show,
    discover_workspace_root,
    load_project_config,
    load_project_locator,
    render_default_releaseledger_toml,
    require_project,
    storage_where,
)
```

Service functions return plain dictionaries or strings and raise
`releaseledger.errors.LaunchError` for user-facing failures. They do not print
or call `typer.Exit`.

## Development

```bash
python -m pip install -e ".[dev]"
pytest -q
ruff check .
mypy releaseledger
python -m build
```

Build documentation:

```bash
python -m pip install -e ".[docs]"
sphinx-build -b html docs docs/_build/html
```

The project ships `py.typed` and targets Python 3.10+.

## License

Apache-2.0
