---
schema_version: 4
id: content-0003
kind: content
type: section
section: context_and_scope
title: Context and Scope
order: 30
status: accepted
version: 2
body_format: markdown
---

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
