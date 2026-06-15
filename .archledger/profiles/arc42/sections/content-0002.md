---
schema_version: 4
id: content-0002
kind: content
type: section
section: architecture_constraints
title: Architecture Constraints
order: 20
status: accepted
version: 2
body_format: markdown
---

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
