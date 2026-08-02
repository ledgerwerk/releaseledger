---
schema_version: 4
id: content-0007
kind: content
type: section
section: deployment_view
title: Deployment View
order: 70
status: accepted
version: 2
body_format: markdown
---

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
