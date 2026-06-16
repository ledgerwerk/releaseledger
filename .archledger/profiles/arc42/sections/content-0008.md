---
schema_version: 4
id: content-0008
kind: content
type: section
section: cross_cutting_concepts
title: Cross-cutting Concepts
order: 80
status: accepted
version: 2
body_format: markdown
---

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
