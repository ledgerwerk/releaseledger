# Concepts

## Git-first

Releaseledger is git-first. Git tags and commit ranges define the shipped change
set. The canonical evidence of what shipped is `git rev-list --reverse --topo-order <base>..<head>`
— every commit reachable from the release target and absent from the previous release.

Taskledger, issue trackers, and PR descriptions are optional provenance that enrich
curated entries, but releaseledger works correctly with only git.

## Source refs

A source ref is a coverage identity. Two kinds are accepted:

- **Global refs** (taskledger, GitHub, etc.): `tl:task-0006`, `github:pr-42`.
  Canonicalized by ledgercore.
- **Git commit refs**: `git:<7-to-40 hex sha>`. The primary evidence type for
  git-first workflows.

A `git-range:*`, `git-tag:*`, or `git-branch:*` ref is a non-coverable
range marker — useful as release metadata but never creating a missing-coverage row.

## Pinned release snapshot

Git-backed releases store both symbolic refs and the resolved commit SHAs. The
resolved SHAs are the immutable snapshot used by default for later git-backed
commands (`git range`, `git scaffold`, `audit init`, `review`, `release check`,
strict build coverage). This prevents a moving branch head from silently adding
new commits to an in-progress release.

Resolve `HEAD` once when attaching the range. A new commit belongs to the
release only after an explicit refresh.

## Release

A release is a versioned record stored as `release.md` with YAML front matter
and an optional Markdown body. It tracks status, previous version, release date,
source boundary, source refs, and changelog file metadata.

Release statuses are:

- `planned`
- `draft`
- `candidate`
- `released`
- `yanked`
- `canceled`

`canceled` means the release was never shipped: it is excluded from
previous-version inference and not built into public changelogs by default.
Canceled releases may carry `cancel_reason` and `superseded_by` metadata
and remain visible in `release list` as an audit tombstone.

## Entry

An entry is one release-note item stored under
`releases/<version>/entries/entry-NNNN.md`. Entries are grouped by kind for
changelog rendering.

Entry kinds are:

- `added`
- `changed`
- `fixed`
- `removed`
- `deprecated`
- `security`
- `docs`
- `quality`
- `internal`

`documentation` and `doc` are accepted aliases for `docs`.

Entry statuses are `draft`, `accepted`, and `rejected`. Changelog builds
include accepted entries by default.

## Event

Events are append-only operation rows. They do not store wall-clock timestamps
or before/after deltas. Releaseledger relies on git history for chronological
review and on per-record revisions for validation.

## Commit audit sheet

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
`rejected`. `public_impact` values are `public`, `docs`, `internal`, `none`,
and `unknown`.

Audit validation is phase-aware:

- `evidence` validates inspection completeness and row evidence quality without
  requiring entries.
- `complete` additionally validates accepted entry coverage, internal coverage
  when requested, and the commit-subject summary guard.

This concept keeps Git as the canonical source of shipped changes while making
the human or agent review work durable and auditable.

## Versioning

Release and entry files contain `versioning.schema_version` and a positive
`versioning.revision`. New records start at revision 1, and the revision
increases by exactly one whenever that record file meaningfully changes.

## Planned versus released

A dated `planned` / `draft` / `candidate` release is intentionally treated as a
consistency warning or strict failure in review/check flows, because default
public full builds include only `released` releases. Finalize a shipped release
explicitly before the final public build.

## Index

Releaseledger rebuilds `indexes/releases.json` and `indexes/entries.json`
after mutations. Indexes are derived state and should remain deterministic.

## Global refs

External provenance is recorded as caller-supplied global refs, for example
`tl:task-0103`. Releaseledger stores these refs but does not resolve or
validate external ledger state.
