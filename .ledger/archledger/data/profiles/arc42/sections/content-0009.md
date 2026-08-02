---
schema_version: 4
id: content-0009
kind: content
type: section
section: architecture_decisions
title: Architecture Decisions
order: 90
status: accepted
version: 2
body_format: markdown
---

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
