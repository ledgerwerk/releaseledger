---
schema_version: 4
id: content-0012
kind: content
type: section
section: glossary
title: Glossary
order: 120
status: accepted
version: 2
body_format: markdown
---

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
