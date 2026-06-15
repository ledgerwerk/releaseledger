---
schema_version: 4
id: content-0004
kind: content
type: section
section: solution_strategy
title: Solution Strategy
order: 40
status: accepted
version: 2
body_format: markdown
---

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
