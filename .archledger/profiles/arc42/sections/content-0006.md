---
schema_version: 4
id: content-0006
kind: content
type: section
section: runtime_view
title: Runtime View
order: 60
status: accepted
version: 2
body_format: markdown
---

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
