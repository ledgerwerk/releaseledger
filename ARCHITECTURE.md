---
title: "Architecture Documentation"
version: 1
generator: "archledger 0.3.1.dev6+g5c58990ed"
arc42_template_version: "9.0-EN"
---

# Architecture Documentation

Generated from archledger records. Do not edit this generated file directly.

# Introduction and Goals

## Introduction

**releaseledger** is a project-local release-state ledger for Python projects and
other source repositories. It records releases, release-note entries, operation
events, and JSON indexes in a deterministic file layout. It renders reviewable
changelog context and writes final `CHANGELOG.md` sections from ledger entries.

Releaseledger is **git-first**: the authoritative evidence of shipped changes is
the git commit range between the previous shipped release (base) and the new
release target (head). Git tags and commit ranges define the shipped change set.
Taskledger, issue trackers, and PR descriptions are optional provenance context.

## Requirements Overview

| ID  | Requirement                                                           |
| --- | --------------------------------------------------------------------- |
| R1  | Record releases with deterministic Markdown + YAML front matter files |
| R2  | Attach changelog entries to releases with structured metadata         |
| R3  | Track every mutation via an append-only event log                     |
| R4  | Render `CHANGELOG.md` from entries using Jinja2 templates             |
| R5  | Derive release entries from git commit ranges (git-first workflow)    |
| R6  | Validate source coverage between release boundaries                   |
| R7  | Support branch-scoped ledger state safe for VCS commit                |
| R8  | Provide both JSON (machine) and human-readable CLI output             |

## Quality Goals

| Priority | Quality Goal              | Scenario                                                                      |
| -------- | ------------------------- | ----------------------------------------------------------------------------- |
| 1        | **Deterministic storage** | Identical inputs produce byte-identical Markdown/JSON output                  |
| 2        | **Git-first provenance**  | Every entry traces to a `git:<sha>` or a global ref (`tl:task-0006`)          |
| 3        | **Schema stability**      | Records validate against versioned schemas; new fields are additive           |
| 4        | **Offline operation**     | All commands work without network access                                      |
| 5        | **Extensibility**         | Entry kinds, template profiles, and changelog postprocessors are configurable |

## Key Stakeholders

| Role               | Expectation                                               |
| ------------------ | --------------------------------------------------------- |
| **Maintainer**     | Generate accurate changelogs from structured release data |
| **CI/CD pipeline** | Machine-readable JSON output for automation               |
| **Contributor**    | Understand what changed and why via structured entries    |
| **Auditor**        | Immutable event log with full mutation history            |

## Requirements Overview

<!-- archledger: no accepted records for this section yet -->

## Quality Goals

<!-- archledger: no accepted records for this section yet -->

## Stakeholders

<!-- archledger: no accepted records for this section yet -->

# Architecture Constraints

## Technical Constraints

| Constraint                       | Rationale                                                             |
| -------------------------------- | --------------------------------------------------------------------- |
| **Python ≥ 3.10**                | Runtime requirement; `match` statements and `X \| Y` union types used |
| **File-based storage**           | No external database; records live in the project repository          |
| **Markdown + YAML front matter** | Records are human-readable and git-diffable                           |
| **Append-only event log**        | `events.jsonl` records all mutations; no deletes or updates           |
| **No network dependency**        | All operations are offline; no telemetry or remote API calls          |
| **Single-project scope**         | Each workspace has one `.releaseledger.toml` and one ledger directory |

## Organizational Constraints

| Constraint                  | Rationale                                                            |
| --------------------------- | -------------------------------------------------------------------- |
| **Apache-2.0 license**      | Open-source license for broad adoption                               |
| **PyPI distribution**       | Installable via `pip install releaseledger`                          |
| **No import of taskledger** | Cross-ledger refs are string-only; no runtime coupling               |
| **ledgercore dependency**   | Shared primitives (front-matter I/O, path validation, ID generation) |

## Conventions

| Convention                | Description                                                                           |
| ------------------------- | ------------------------------------------------------------------------------------- |
| **Frozen dataclasses**    | All domain models are immutable (`frozen=True`, `slots=True`)                         |
| **Services return dicts** | Services raise `LaunchError`; never print or call `typer.Exit`                        |
| **Stable error codes**    | `USAGE_ERROR`, `NOT_FOUND`, `CONFIG_ERROR`, `VALIDATION_ERROR`, `CONFLICT`            |
| **Schema versioning**     | `RELEASELEDGER_SCHEMA_VERSION = 2`; supported versions in `SUPPORTED_SCHEMA_VERSIONS` |
| **Revision tracking**     | Every record mutation increments `versioning.revision`                                |

<!-- archledger: no accepted records for this section yet -->

# Context and Scope

## System Context

```
┌─────────────────────────────────────────────────────┐
│                  releaseledger                       │
│                                                     │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────┐  │
│  │  CLI     │  │ Services │  │ Storage (disk)   │  │
│  │ (typer)  │──│ (domain) │──│ .releaseledger/  │  │
│  └──────────┘  └──────────┘  └──────────────────┘  │
└─────────────────────────────────────────────────────┘
        │              │                │
        ▼              ▼                ▼
   ┌─────────┐   ┌──────────┐   ┌───────────────┐
   │ User /  │   │ Git      │   │ ledgercore    │
   │ CI/CD   │   │ worktree │   │ (shared lib)  │
   └─────────┘   └──────────┘   └───────────────┘
```

## External Interfaces

| Interface              | Direction                | Description                                                        |
| ---------------------- | ------------------------ | ------------------------------------------------------------------ |
| **CLI (stdin/stdout)** | User → releaseledger     | Typer-based commands with `--json` output                          |
| **Git worktree**       | releaseledger → git      | `git rev-list`, `git log`, `git rev-parse` via subprocess          |
| **Filesystem**         | releaseledger ↔ disk     | Read/write `.releaseledger/` state directory                       |
| **ledgercore**         | releaseledger → lib      | Front-matter I/O, path validation, ID generation, config discovery |
| **PyPI**               | releaseledger → registry | Package distribution                                               |

## User Stories

| ID  | Story                                                                                                        |
| --- | ------------------------------------------------------------------------------------------------------------ |
| U1  | As a maintainer, I want to create a release and attach git ranges so that the changelog is evidence-based    |
| U2  | As a maintainer, I want to add structured entries (added/changed/fixed/etc.) so the changelog is categorized |
| U3  | As a CI pipeline, I want `--json` output so I can parse release state programmatically                       |
| U4  | As a reviewer, I want `releaseledger review` to show missing git coverage                                    |
| U5  | As a contributor, I want `releaseledger git import` to auto-generate entry candidates from commits           |

## Business Context

<!-- archledger: no accepted records for this section yet -->

## Technical Context

<!-- archledger: no accepted records for this section yet -->

# Solution Strategy

## Architectural Approach

releaseledger follows a **layered architecture** with four distinct tiers:

| Layer        | Responsibility                                 | Key Modules               |
| ------------ | ---------------------------------------------- | ------------------------- |
| **CLI**      | Command parsing, output formatting             | `cli.py`, `cli_common.py` |
| **API**      | Thin service wrappers for CLI                  | `api/`                    |
| **Services** | Business logic, validation, orchestration      | `services/`               |
| **Domain**   | Immutable data models, controlled vocabularies | `domain/`                 |
| **Storage**  | Persistence, path resolution, config           | `storage/`                |

## Key Design Decisions

### 1. File-based Markdown storage with YAML front matter

Records are stored as Markdown files with YAML front matter. This makes them:

- **Human-readable**: developers can inspect records directly
- **Git-diffable**: changes are visible in standard diff tools
- **Self-contained**: each file carries its complete schema metadata

### 2. Append-only event log

Every mutation appends a `ReleaseEvent` to `events.jsonl`. This provides:

- Complete audit trail without database infrastructure
- Reconstruction capability from events
- No wall-clock timestamps (git history provides chronology)

### 3. Git-first provenance

`git:<sha>` commit refs are first-class coverable source identities, on equal
footing with ledgercore global refs. The coverage model is:

```
git rev-list --reverse --topo-order <base>..<head>
```

### 4. Immutable domain models

All domain objects (`ReleaseRecord`, `ReleaseEntryRecord`, `ReleaseEvent`) are
frozen dataclasses. Mutation always creates new instances with bumped revisions.

### 5. Services never print or exit

Services return plain dict payloads and raise `LaunchError`. The CLI boundary
handles rendering (JSON or human-readable) and exit codes.

## Technology Stack

| Technology       | Role                         |
| ---------------- | ---------------------------- |
| **Python 3.10+** | Runtime                      |
| **typer/click**  | CLI framework                |
| **PyYAML**       | YAML front matter parsing    |
| **Jinja2**       | Changelog template rendering |
| **ledgercore**   | Shared storage primitives    |
| **setuptools**   | Build system                 |

## Strategy Items

<!-- archledger: no accepted records for this section yet -->

# Building Block View

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

<!-- archledger: no accepted records for this section yet -->

# Runtime View

## Scenario 1: Create a Release

```
User                    CLI                   Services              Storage
  │                       │                       │                     │
  │ releaseledger         │                       │                     │
  │ release create 1.2.0  │                       │                     │
  ├──────────────────────►│                       │                     │
  │                       │ create_release()      │                     │
  │                       ├──────────────────────►│                     │
  │                       │                       │ validate_version()  │
  │                       │                       ├────────────────────►│
  │                       │                       │◄────────────────────┤
  │                       │                       │                     │
  │                       │                       │ save_release()      │
  │                       │                       ├────────────────────►│
  │                       │                       │  → release.md       │
  │                       │                       │◄────────────────────┤
  │                       │                       │                     │
  │                       │                       │ append_event()      │
  │                       │                       ├────────────────────►│
  │                       │                       │  → events.jsonl     │
  │                       │                       │◄────────────────────┤
  │                       │                       │                     │
  │                       │                       │ rebuild_indexes()   │
  │                       │                       ├────────────────────►│
  │                       │                       │  → releases.json    │
  │                       │                       │◄────────────────────┤
  │                       │◄──────────────────────┤                     │
  │◄──────────────────────┤                       │                     │
  │ { "version": "1.2.0" }│                       │                     │
```

## Scenario 2: Git Import (Generate Entries from Commits)

```
User                    CLI                   Git Sources           Services
  │                       │                       │                     │
  │ releaseledger git     │                       │                     │
  │ import 1.2.0          │                       │                     │
  ├──────────────────────►│                       │                     │
  │                       │ collect_git_candidates│                     │
  │                       ├──────────────────────►│                     │
  │                       │                       │ git rev-list        │
  │                       │                       │ <base>..<head>      │
  │                       │                       ├─────────┐           │
  │                       │                       │◄────────┘           │
  │                       │                       │                     │
  │                       │  For each commit:     │                     │
  │                       │  - parse diff         │                     │
  │                       │  - infer kind from    │                     │
  │                       │    conventional prefix│                     │
  │                       │  - create candidate   │                     │
  │                       │◄──────────────────────┤                     │
  │                       │                       │                     │
  │                       │ write YAML output     │                     │
  │◄──────────────────────┤                       │                     │
  │ entries.yaml          │                       │                     │
```

## Scenario 3: Changelog Build

```
User                    CLI                   Changelog Build       Storage
  │                       │                       │                     │
  │ releaseledger         │                       │                     │
  │ changelog build       │                       │                     │
  ├──────────────────────►│                       │                     │
  │                       │ build_changelog()     │                     │
  │                       ├──────────────────────►│                     │
  │                       │                       │ list_releases()     │
  │                       │                       ├────────────────────►│
  │                       │                       │◄────────────────────┤
  │                       │                       │                     │
  │                       │                       │ load_entries()      │
  │                       │                       ├────────────────────►│
  │                       │                       │◄────────────────────┤
  │                       │                       │                     │
  │                       │                       │ group by kind       │
  │                       │                       │ render Jinja2       │
  │                       │                       │ apply postprocessors│
  │                       │                       │                     │
  │                       │                       │ write CHANGELOG.md  │
  │                       │                       ├────────────────────►│
  │                       │◄──────────────────────┤                     │
  │◄──────────────────────┤                       │                     │
  │ CHANGELOG.md written  │                       │                     │
```

## Scenario 4: Release Review (Coverage Check)

```
User                    CLI                   Review Service        Git Sources
  │                       │                       │                     │
  │ releaseledger review  │                       │                     │
  │ 1.2.0 --git --strict  │                       │                     │
  ├──────────────────────►│                       │                     │
  │                       │ review_release()      │                     │
  │                       ├──────────────────────►│                     │
  │                       │                       │ load entries        │
  │                       │                       │ collect git commits │
  │                       │                       ├────────────────────►│
  │                       │                       │◄────────────────────┤
  │                       │                       │                     │
  │                       │                       │ match source_refs   │
  │                       │                       │ to commits          │
  │                       │                       │ find uncovered      │
  │                       │                       │                     │
  │◄──────────────────────┤                       │                     │
  │ coverage report       │                       │                     │
```

<!-- archledger: no accepted records for this section yet -->

# Deployment View

## Deployment Model

releaseledger is a **standalone Python CLI tool** installed into a project's
virtual environment. No server process, no database, no external services.

### Installation

```
┌─────────────────────────────────────────────────┐
│ Developer Workstation / CI Runner                │
│                                                  │
│  ┌──────────────────┐                           │
│  │ Python venv       │                           │
│  │ ┌──────────────┐ │                           │
│  │ │ releaseledger │ │  ← pip install            │
│  │ │ (CLI binary)  │ │                           │
│  │ └──────┬───────┘ │                           │
│  │        │          │                           │
│  │ ┌──────▼───────┐ │                           │
│  │ │ ledgercore   │ │  ← shared dependency      │
│  │ └──────────────┘ │                           │
│  └──────────────────┘                           │
│                                                  │
│  ┌──────────────────┐                           │
│  │ Project Root      │                           │
│  │ ┌──────────────┐ │                           │
│  │ │ .releaseledger│ │  ← state directory       │
│  │ │ .toml         │ │  ← config file           │
│  │ └──────────────┘ │                           │
│  └──────────────────┘                           │
└─────────────────────────────────────────────────┘
```

### Storage Layout on Disk

```
<project_root>/
├── .releaseledger.toml              # Project configuration
├── .releaseledger/                  # State directory
│   └── ledgers/
│       └── main/                    # ledger_ref (configurable)
│           ├── releases/
│           │   ├── 0.1.0/
│           │   │   ├── release.md
│           │   │   └── entries/
│           │   │       ├── entry-0001.md
│           │   │       └── entry-0002.md
│           │   └── 1.0.0/
│           │       ├── release.md
│           │       └── entries/
│           │           └── entry-0001.md
│           ├── events/
│           │   └── events.jsonl     # Append-only event log
│           └── indexes/
│               ├── releases.json    # Rebuilt on every mutation
│               └── entries.json     # Rebuilt on every mutation
├── CHANGELOG.md                     # Generated output
└── src/...
```

### CI/CD Integration

```yaml
# GitHub Actions example
- name: Generate changelog
  run: |
    releaseledger changelog build --version 1.2.0
    git add CHANGELOG.md
    git commit -m "chore: update changelog for 1.2.0"
```

The tool works entirely offline. The only external dependency is the git
worktree for git-first features. If git is unavailable, releaseledger falls
back gracefully (git-specific commands warn but don't block).

<!-- archledger: no accepted records for this section yet -->

# Cross-cutting Concepts

## 1. Deterministic Storage

Every record write follows the same pattern:

1. Validate the domain object against its schema
2. Check revision transitions (`_validate_revision_transition`)
3. Write via `ledgercore.write_front_matter_document` with canonical key order
4. Append an event to `events.jsonl`
5. Rebuild JSON indexes

Identical inputs produce byte-identical output. No timestamps are written to
records or indexes.

## 2. Revision Tracking

Every record carries `versioning.revision` (starting at 1). On update:

- If content changes → revision must increment by exactly 1
- If content is unchanged → revision must stay the same

This prevents lost-update conflicts in concurrent-edit scenarios.

## 3. Source Ref Taxonomy

Source references are classified into two families:

| Family               | Examples                         | Coverable | Use as source_refs |
| -------------------- | -------------------------------- | --------- | ------------------ |
| **Git commit**       | `git:a1b2c3d` (7-40 hex)         | ✅ Yes    | ✅ Yes             |
| **Global ref**       | `tl:task-0006`, `github:pr-0042` | ✅ Yes    | ✅ Yes             |
| **Git range marker** | `git-range:v0.1.0..HEAD`         | ❌ No     | ❌ No              |
| **Git symbolic**     | `git:HEAD`, `git:main`           | ❌ No     | ❌ No              |

Range markers are valid only as `boundary_ref` metadata on releases.

## 4. Entry Kind Vocabulary

releaseledger extends the standard Keep a Changelog kinds:

| Kind         | Keep a Changelog Group | Description                       |
| ------------ | ---------------------- | --------------------------------- |
| `added`      | Added                  | New features                      |
| `changed`    | Changed                | Changes to existing functionality |
| `deprecated` | Deprecated             | Soon-to-be-removed features       |
| `removed`    | Removed                | Removed features                  |
| `fixed`      | Fixed                  | Bug fixes                         |
| `security`   | Security               | Vulnerability fixes               |
| `docs`       | Changed (default)      | Documentation changes             |
| `quality`    | Changed (default)      | Test/lint/CI improvements         |
| `internal`   | Changed (default)      | Internal refactoring              |

## 5. Error Handling

All errors flow through `LaunchError` (subclass of `ReleaseledgerError`):

- `message`: Human-readable description
- `code`: Machine-readable constant (`USAGE_ERROR`, `NOT_FOUND`, etc.)
- `exit_code`: Process exit code (2 for input errors, 1 for runtime)
- `data`: Optional structured detail
- `remediation`: Optional ordered fix suggestions

Services raise `LaunchError`; the CLI boundary renders either a human line
or a JSON envelope.

## 6. Event Sourcing (Lightweight)

The `events.jsonl` file is an append-only log of all mutations. Events carry:

- `event_id`: Unique identifier
- `event`: Event type (e.g., `release.created`, `entry.added`)
- `release_version`: Target release (optional)
- `entry_id`: Target entry (optional)
- `record_revisions`: Map of record → revision at event time
- `data`: Event-specific payload

Events do NOT carry timestamps or before/after deltas. Git history provides
chronology; record revisions validate file changes.

## 7. Changelog Rendering Pipeline

```
entries → group by kind → sort by order → render Jinja2 → apply postprocessors → write
```

The pipeline supports:

- Custom Jinja2 templates per profile
- Postprocessors (regex pattern/replace pairs)
- Keep a Changelog 1.1.0 standard mode
- Link references and URL templates
- Preamble text for file headers
- Preamble text for file headers

## 8. Commit Audit Sheet

A commit audit sheet is a per-release review artifact that maps every commit
in the selected git range to a reviewer decision and, when applicable, to a
release entry. It is evidence state, not changelog prose.

The sheet exists to prevent release notes from being generated from commit
subjects. Each row records the commit SHA, inspected paths,
reviewer-observed behavior, public/internal impact, decision, and target
release entry. Public changelog entries are written from reviewed behavior,
API/docs impact, changed paths, tests, and diff evidence. Commit subjects are
evidence-only and must not be copied or mechanically transformed into
release summaries.

Decisions are `needs_review`, `accepted`, `grouped`, `internal`, and
`rejected`. Strict release review fails when rows remain uninspected, when
public rows lack accepted entry coverage, or when an entry summary matches a
commit subject.

This concept keeps Git as the canonical source of shipped changes while making
the human or agent review work durable and auditable. The canonical storage is
the YAML sheet under `releases/<version>/audit/commit-audit.yaml`; a markdown
view is rendered on demand rather than persisted.

<!-- archledger: no accepted records for this section yet -->

# Architecture Decisions

## ADR-1: File-based Markdown Storage (not a database)

**Status:** Accepted

**Context:** releaseledger needs to store releases, entries, and events. Options
included SQLite, JSON-only, or Markdown + YAML front matter.

**Decision:** Use Markdown files with YAML front matter for records, JSONL for
events, and JSON for indexes.

**Consequences:**

- ✅ Records are human-readable and git-diffable
- ✅ No external database dependency
- ✅ Works offline on any platform
- ❌ No ACID transactions (mitigated by revision tracking)
- ❌ Slower for large datasets (acceptable for project-local scope)

## ADR-2: Git-first Provenance Model

**Status:** Accepted

**Context:** Release notes need evidence of shipped changes. Options included
manual entry, issue-tracker-first, or git-first.

**Decision:** Git commit SHAs are first-class coverable source refs. The
`git:<sha>` format is on equal footing with ledgercore global refs.

**Consequences:**

- ✅ Evidence is always available (git is universal)
- ✅ No dependency on external issue trackers
- ✅ Coverage analysis is deterministic
- ❌ Commit messages need to be meaningful for good auto-generated entries
- ❌ Conventional commit format is recommended but not enforced

## ADR-3: Immutable Domain Models (frozen dataclasses)

**Status:** Accepted

**Context:** Domain objects need to be passed around safely without accidental
mutation. Options included mutable models with defensive copying, or frozen
dataclasses.

**Decision:** All domain models use `@dataclass(slots=True, frozen=True)`.

**Consequences:**

- ✅ No accidental mutation bugs
- ✅ Safe to pass across service boundaries
- ✅ `dataclasses.replace()` for controlled updates
- ❌ Slightly more verbose update patterns (acceptable trade-off)

## ADR-4: Append-only Event Log (no timestamps)

**Status:** Accepted

**Context:** Events need to track mutations. Options included timestamped events,
before/after deltas, or a minimal append-only log.

**Decision:** Events carry only event type, target identifiers, and record
revisions. No wall-clock timestamps, no before/after deltas.

**Consequences:**

- ✅ Deterministic output (no timestamp variance)
- ✅ Git history provides chronology
- ✅ Record revisions validate file changes
- ❌ Cannot reconstruct exact mutation order without git
- ❌ Events alone are not sufficient for full audit (intentional)

## ADR-5: Services Never Print or Exit

**Status:** Accepted

**Context:** The CLI needs both JSON and human-readable output. Options included
services printing directly, or services returning data.

**Decision:** Services return plain dict payloads and raise `LaunchError`. The
CLI boundary handles rendering and exit codes.

**Consequences:**

- ✅ Clean separation of concerns
- ✅ Services are testable without CLI mocking
- ✅ JSON output is always available
- ✅ Consistent error envelope format
- ❌ Slightly more boilerplate in CLI layer (acceptable)

## ADR-6: ledgercore as Shared Dependency

**Status:** Accepted

**Context:** Several operations (front-matter I/O, path validation, ID generation)
are shared across ledgers. Options included inlining or a shared library.

**Decision:** Use `ledgercore` as a shared dependency for common primitives.

**Consequences:**

- ✅ Consistent behavior across taskledger/releaseledger
- ✅ Single place to fix shared bugs
- ❌ Version coupling between packages
- ❌ Extra dependency (acceptable for consistency)

## ADR-7: Persist Commit Audit Sheets per Release

**Status:** Accepted

| Decision                                | Status   | Consequence                                                                                                         |
| --------------------------------------- | -------- | ------------------------------------------------------------------------------------------------------------------- |
| Persist commit audit sheets per release | Accepted | Release review can prove every git commit was inspected without exposing internal housekeeping in public changelogs |

**Context:** Reviewers (humans or agents) backfill release notes from git
ranges. Without a durable artifact, the per-commit reasoning disappears after
the entries are imported, and a reviewer can satisfy coarse coverage by writing
low-quality entries copied from commit subjects.

**Decision:** Store one canonical commit audit sheet per release under
`releases/<version>/audit/commit-audit.yaml`. Each row records the commit SHA,
inspected paths, reviewer-observed behavior, public/internal impact, decision,
and target release entry. Commit subjects are evidence-only and must never
become changelog prose.

**Consequences:**

- ✅ Review work is durable and auditable per release
- ✅ Strict review can prove every commit was inspected
- ✅ Internal housekeeping stays out of public changelogs by default
- ❌ One more artifact to maintain (opt-in strict enforcement keeps it optional)

<!-- archledger: no accepted records for this section yet -->

# Quality Requirements

## Quality Tree

```
Quality
├── Determinism
│   ├── Byte-identical output for identical inputs
│   ├── No timestamps in records or indexes
│   └── Canonical key ordering in YAML front matter
├── Correctness
│   ├── Schema validation on every record load
│   ├── Revision tracking prevents lost updates
│   └── Source ref validation rejects invalid refs
├── Usability
│   ├── Both JSON and human-readable CLI output
│   ├── Actionable error messages with remediation hints
│   └── Interactive prompts for missing fields
├── Reliability
│   ├── Offline operation (no network dependency)
│   ├── Graceful fallback when git is unavailable
│   └── Atomic writes via ledgercore
└── Extensibility
    ├── Configurable entry kinds and aliases
    ├── Custom Jinja2 changelog templates
    └── Postprocessors for changelog rendering
```

## Quality Scenarios

### Q1: Deterministic Storage

| Attribute | Determinism                                                                                                  |
| --------- | ------------------------------------------------------------------------------------------------------------ |
| Scenario  | Two identical `releaseledger init` + `release create` sequences produce identical `.releaseledger/` contents |
| Metric    | `diff -r` returns empty                                                                                      |
| Target    | 100% identical output                                                                                        |

### Q2: Schema Validation

| Attribute | Correctness                                                                          |
| --------- | ------------------------------------------------------------------------------------ |
| Scenario  | Loading a release.md with invalid YAML fields raises `LaunchError(VALIDATION_ERROR)` |
| Metric    | Every invalid field is caught at load time                                           |
| Target    | No silent data corruption                                                            |

### Q3: Revision Conflict Detection

| Attribute | Correctness                                                                          |
| --------- | ------------------------------------------------------------------------------------ |
| Scenario  | Two concurrent updates to the same record; second save detects the revision mismatch |
| Metric    | `_validate_revision_transition` raises `LaunchError`                                 |
| Target    | 100% conflict detection                                                              |

### Q4: CLI Error Envelope

| Attribute | Usability                                                                                              |
| --------- | ------------------------------------------------------------------------------------------------------ |
| Scenario  | Any CLI error produces a JSON envelope with `code`, `message`, `exit_code`, and optional `remediation` |
| Metric    | `--json` flag always produces parseable output                                                         |
| Target    | 100% consistent error format                                                                           |

### Q5: Git Coverage Analysis

| Attribute | Correctness                                                                        |
| --------- | ---------------------------------------------------------------------------------- |
| Scenario  | `releaseledger review 1.2.0 --git --strict` correctly identifies uncovered commits |
| Metric    | All commits in `base..head` range are checked against entry source_refs            |
| Target    | Zero false negatives (no uncovered commit reported as covered)                     |

### Q6: Commit Audit Sheet Completeness (Q-rel-01)

| Attribute | Correctness/Auditability                                                                                                  |
| --------- | ------------------------------------------------------------------------------------------------------------------------- |
| ID        | Q-rel-01                                                                                                                  |
| Scenario  | A release is built from a git range containing public and internal commits                                                |
| Response  | The audit sheet contains one inspected row per commit, public rows map to accepted entries, internal rows map to internal |
|           | entries, and public changelog output excludes internal entries by default                                                 |

## Quality Requirements Overview

<!-- archledger: no accepted records for this section yet -->

## Quality Scenarios

<!-- archledger: no accepted records for this section yet -->

# Risks and Technical Debt

## Risks

### R1: Large Repository Performance

| Risk       | Repositories with thousands of commits may slow git operations                   |
| ---------- | -------------------------------------------------------------------------------- |
| Impact     | Medium — `git rev-list` and diff parsing scale linearly                          |
| Mitigation | `max_commits` config limit (default 500); `max_diff_chars_per_commit` truncation |
| Status     | Mitigated by configuration                                                       |

### R2: ledgercore Version Coupling

| Risk       | Breaking changes in ledgercore could require coordinated updates |
| ---------- | ---------------------------------------------------------------- |
| Impact     | High — core storage primitives depend on it                      |
| Mitigation | Pin minimum version (`>=0.2.0`); ledgercore follows semver       |
| Status     | Managed via dependency pinning                                   |

### R3: Concurrent Access

| Risk       | Multiple processes writing to the same ledger simultaneously          |
| ---------- | --------------------------------------------------------------------- |
| Impact     | Medium — revision tracking catches conflicts but doesn't prevent them |
| Mitigation | Revision validation; atomic writes via `ledgercore.atomic_write_text` |
| Status     | Partially mitigated                                                   |

### R4: Git Worktree Assumptions

| Risk       | Git-first features assume a standard git worktree         |
| ---------- | --------------------------------------------------------- |
| Impact     | Low — non-git projects still work, only git commands warn |
| Mitigation | Graceful fallback; `git.enabled` config option            |
| Status     | Mitigated                                                 |

## Technical Debt

### TD1: Large CLI Module

| Debt   | `cli.py` is ~74KB — all commands in one file |
| ------ | -------------------------------------------- |
| Impact | Low — functional but hard to navigate        |
| Plan   | Could split into command groups (not urgent) |

### TD2: Manual Changelog Template Management

| Debt   | Default templates are string literals in config.py       |
| ------ | -------------------------------------------------------- |
| Impact | Low — works but template evolution requires code changes |
| Plan   | External template files with versioned defaults          |

### TD3: No Schema Migration Tooling

| Debt   | Schema version bumps require manual migration guidance |
| ------ | ------------------------------------------------------ |
| Impact | Low — current schema is stable (v2)                    |
| Plan   | Add migration tooling when schema v3 is needed         |

### TD4: Entry Kind Extension Requires Code Changes

| Debt   | Adding new entry kinds requires updating `states.py` and `domain/entry.py` |
| ------ | -------------------------------------------------------------------------- |
| Impact | Low — kinds are rare to add                                                |
| Plan   | Could make kinds configurable (not urgent)                                 |

## Risk Overview

<!-- archledger: no accepted records for this section yet -->

# Glossary

## Terms

| Term               | Definition                                                                                    |
| ------------------ | --------------------------------------------------------------------------------------------- |
| **Release**        | A versioned collection of entries representing a software release. Persisted as `release.md`. |
| **Entry**          | A single changelog item attached to a release. Persisted as `entry-NNNN.md`.                  |
| **Event**          | An immutable mutation record in the append-only `events.jsonl` log.                           |
| **Bundle**         | The directory containing a release and its entries: `releases/<version>/`.                    |
| **Source ref**     | A coverable change identity: `git:<sha>` or a global ref (`tl:task-0006`).                    |
| **Boundary ref**   | A range marker on a release (e.g., `git-range:v0.1.0..HEAD`). Non-coverable.                  |
| **Coverage**       | The set of source refs mapped to entries; uncovered commits indicate missing release notes.   |
| **Revision**       | Per-record version counter; increments on every content change.                               |
| **Schema version** | Global record schema identifier (currently `RELEASELEDGER_SCHEMA_VERSION = 2`).               |
| **Ledger ref**     | The branch-scoped ledger directory name (default: `main`).                                    |
| **ledgercore**     | Shared Python library providing front-matter I/O, path validation, and ID generation.         |

## Abbreviations

| Abbreviation | Full Form                                                 |
| ------------ | --------------------------------------------------------- |
| **FM**       | Front Matter (YAML metadata at the top of Markdown files) |
| **JSONL**    | JSON Lines (one JSON object per line)                     |
| **ADR**      | Architecture Decision Record                              |
| **semver**   | Semantic Versioning (e.g., `v1.2.3`)                      |
| **CLI**      | Command-Line Interface                                    |

## Entry Kind Aliases

| Alias           | Canonical Kind |
| --------------- | -------------- |
| `documentation` | `docs`         |
| `doc`           | `docs`         |

## Release Status Lifecycle

```
planned → draft → candidate → released
                          ↘ yanked
                          ↘ canceled
```

## Entry Status Lifecycle

```
draft → accepted
     ↘ rejected
```

<!-- archledger: no accepted records for this section yet -->
