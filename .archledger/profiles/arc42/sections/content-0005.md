---
schema_version: 4
id: content-0005
kind: content
type: section
section: building_block_view
title: Building Block View
order: 50
status: accepted
version: 2
body_format: markdown
---

## Level 1: System Overview

```
releaseledger/
├── cli.py              # CLI entry point (typer commands)
├── cli_common.py       # Shared CLI helpers (JSON rendering, error handling)
├── launcher.py         # Console script entry point
├── errors.py           # Typed exceptions (LaunchError, ReleaseledgerError)
├── api/                # Thin service wrappers for CLI
│   ├── changelog.py
│   ├── config.py
│   ├── entries.py
│   ├── releases.py
│   └── review.py
├── domain/             # Immutable data models
│   ├── entry.py        # ReleaseEntryRecord
│   ├── event.py        # ReleaseEvent
│   ├── release.py      # ReleaseRecord
│   ├── source_ref.py   # Source ref normalization (git-first)
│   ├── states.py       # Controlled vocabularies, schema versions
│   └── versioning.py   # RecordVersioning (revision tracking)
├── services/           # Business logic
│   ├── branch.py       # Branch-scoped state management
│   ├── changelog.py    # Changelog rendering service
│   ├── changelog_build.py  # CHANGELOG.md build logic
│   ├── config.py       # Config-related services
│   ├── entries.py      # Entry lifecycle (add, update, import)
│   ├── entry_lint.py   # Entry validation/linting
│   ├── entry_prompt.py # Entry prompting helpers
│   ├── events.py       # Event log appending
│   ├── git_sources.py  # Git-first evidence source
│   ├── releases.py     # Release lifecycle (create, tag, finalize)
│   └── review.py       # Release review/coverage analysis
└── storage/            # Persistence layer
    ├── config.py       # ProjectConfig, TOML parsing
    ├── paths.py        # Config discovery, path resolution
    └── store.py        # Read/write releases, entries, events, indexes
```

## Level 2: Domain Layer

The domain layer contains only immutable data models and validation logic. No
I/O, no side effects.

### ReleaseRecord (`domain/release.py`)

A frozen dataclass representing one release. Persisted as `release.md` with YAML
front matter. The `note` field becomes the Markdown body (not front matter).

Key fields: `version`, `status`, `title`, `versioning`, `released_at`,
`previous_version`, `source_refs`, `git_base_ref`, `git_head_ref`, `entry_count`.

### ReleaseEntryRecord (`domain/entry.py`)

A frozen dataclass representing one changelog entry. Persisted as `entry-NNNN.md`
inside a release bundle.

Key fields: `entry_id`, `release_version`, `kind`, `summary`, `status`,
`audience`, `scopes`, `source_refs`, `paths`, `issues`, `prs`, `breaking`,
`internal`, `order`.

### ReleaseEvent (`domain/event.py`)

A frozen dataclass for the append-only event log. Written to `events.jsonl`.

Event types: `release.created`, `release.tagged`, `release.finalized`,
`release.updated`, `release.canceled`, `release.renamed`, `entry.added`,
`entry.updated`, `entry.imported`, `entry.batch_added`.

### Source Ref Normalization (`domain/source_ref.py`)

Single routing point for source-ref validation. Accepts:

- `git:<7-40 hex>` commit refs (first-class, coverable)
- Ledgercore global refs: `tl:task-0006`, `github:pr-0042`

Rejects: `git:HEAD`, `git:main`, `git-range:*`, `git-tag:*`, `git-branch:*`
(these are range markers, not coverable identities).

### Controlled Vocabularies (`domain/states.py`)

- Schema version: `RELEASELEDGER_SCHEMA_VERSION = 2`
- Release statuses: `planned`, `draft`, `candidate`, `released`, `yanked`, `canceled`
- Entry kinds: `added`, `changed`, `fixed`, `removed`, `deprecated`, `security`, `docs`, `quality`, `internal`
- Entry statuses: `draft`, `accepted`, `rejected`

## Level 2: Storage Layer

### Store (`storage/store.py`)

Owns all disk I/O. Reads and writes:

- `release.md` (YAML front matter + Markdown body)
- `entry-NNNN.md` (YAML front matter + Markdown body)
- `events.jsonl` (append-only JSONL)
- `releases.json` / `entries.json` (rebuilt indexes)

Revision validation: `_validate_revision_transition` ensures that on update,
the revision increments by exactly 1 when content changes, or stays the same
when content is unchanged.

### Config (`storage/config.py`)

Parses and validates `.releaseledger.toml`. The `ProjectConfig` frozen dataclass
holds all configuration: ledger identity, release defaults, changelog template,
git-first settings.

### Paths (`storage/paths.py`)

Config discovery (upward search for `.releaseledger.toml`), path resolution
(`.releaseledger/ledgers/<ref>/`), and layout initialization.

## Level 2: Services Layer

### Release Service (`services/releases.py`)

Orchestrates the full release lifecycle:

- `create_release` → validate, save record, append event, rebuild indexes
- `tag_release` → add git range metadata, transition status to `candidate`
- `finalize_release` → transition to `released`, set `released_at`
- `rename_release` → atomic bundle move with entry rewriting
- `cancel_release` → transition to `canceled` with reason
- `update_release` → update mutable fields, bump revision

### Entry Service (`services/entries.py`)

Orchestrates entry lifecycle:

- `add_release_entry` → validate, save, append event, update release entry count
- `update_release_entry` → validate, save, bump revision, append event
- `import_release_entry_file` → import from external YAML
- `add_many_release_entries` → batch add from YAML file

### Git Sources (`services/git_sources.py`)

Git-first evidence source. Uses `git rev-list --reverse --topo-order <base>..<head>`
to collect commits. Each commit becomes a `GitSourceCandidate` with `git:<sha>`
as the source ref. Merge policies: `never`, `always`, `nontrivial`.

### Changelog Build (`services/changelog_build.py`)

Renders `CHANGELOG.md` from entries using Jinja2 templates. Supports:

- Keep a Changelog 1.1.0 standard
- Extended entry kinds (docs, quality, internal)
- Configurable group ordering and postprocessors
- Link references and URL templates
