# Quickstart

## Install

```bash
python -m pip install releaseledger
```

For development:

```bash
python -m pip install -e ".[dev]"
```

## Initialize a project

```bash
releaseledger init
releaseledger status
releaseledger doctor
```

This creates a schema-3 `.ledger/ledger.toml`, the Releaseledger tool config,
and the default state layout:

```text
.ledger/
  ledger.toml
  releaseledger/
    config.toml
    data/
    indexes/
```

Inspect paths and validate the bindings before mutating state:

```bash
releaseledger storage where
releaseledger storage validate --strict
releaseledger config validate
```

The legacy layout may still be discovered and migrated; it is not the format
created by new projects:

```text
.releaseledger/
  ledgers/
    main/
      releases/
      events/
      indexes/
```

Releaseledger is git-first. The recommended workflow uses git commit ranges
as the canonical evidence of shipped changes.

## Prepare a release and pin the git snapshot

```bash
releaseledger release prepare 1.2.0 \
  --previous 1.1.0 \
  --git-base v1.1.0 \
  --git-head HEAD
work=.ledger/releaseledger/work/1.2.0
```

After the snapshot is attached, omit `--head` unless you intentionally want to
refresh the stored snapshot to a newer commit.

## Create audit evidence and scaffold entries

````bash
# release prepare already emitted evidence/, audit.yaml, audit-decisions.yaml, and entries.yaml.
# `release prepare` returns structured `next_actions` in JSON and renders the same ordered guidance in human output.
# Sequence: inspect audit, dry-run/apply audit, validate evidence, edit behavior-based entries, dry-run/apply entries with subject guards, validate complete audit, then run the lifecycle check.
ls "$work"
# For a planned release without a date, use `--unreleased` on direct strict changelog builds. Do not treat preparation as publication.
ls "$work/evidence"

Curate the audit annotations, then validate the evidence phase:

```bash
releaseledger audit apply 1.2.0 \
  --file "$work/audit-decisions.yaml" \
  --dry-run
releaseledger audit apply 1.2.0 \
  --file "$work/audit-decisions.yaml"
releaseledger audit validate 1.2.0 --phase evidence --strict
````

Edit the entry scaffold to write user-facing summaries from reviewed behavior,
then validate and write entries atomically:
One commit may map to zero, one, or multiple entries. Keep one `source_refs` coverage owner for each commit and use supporting `sources` on additional behavior-specific entries; write every summary from the reviewed diff, not the commit subject.

```bash
releaseledger entry apply 1.2.0 \
  --file "$work/entries.yaml" \
  --dry-run \
  --strict \
  --guard-commit-subjects
releaseledger entry apply 1.2.0 \
  --file "$work/entries.yaml" \
  --strict \
  --guard-commit-subjects \
  --sync-audit
releaseledger audit validate 1.2.0 --phase complete --strict --include-internal
```

## Run the final gate and build the changelog

```bash
releaseledger release check 1.2.0 --strict --target-file CHANGELOG.md
releaseledger release check 1.2.0 --phase finalize \
  --released-at 2026-06-14 --strict --target-file CHANGELOG.md
releaseledger release finalize 1.2.0 --released-at 2026-06-14
releaseledger changelog build 1.2.0 --strict --output CHANGELOG.md
```

## Local and external tag workflows

The default local policy keeps the existing explicit local release workflow. For a project where GitHub creates the tag, configure the policy before preparation:

```toml
[git]
tag_creation = "external"
```

An undated preparation remains planned and unreleased. A dated preparation reports the external publication handoff before the published check. The agent must not run tag creation or release publication commands. The human publishes the release externally, then the agent may fetch the tag and resume reconciliation and `release check --phase published --strict`.

## Correct a recorded version safely

Preview and apply a planned-version correction as one explicit workflow. The
dry-run verifies bundle, entry, audit, successor, and changelog actions; no
manual edit to generated `CHANGELOG.md` content is needed. Stored `vX.Y.Z`
and `X.Y.Z` records share one external identity, while exact storage names
remain unchanged.

```bash
releaseledger release rename 0.3.0 0.2.8 \
  --previous v0.2.7 \
  --target-file CHANGELOG.md \
  --rename-changelog-section \
  --dry-run
releaseledger release rename 0.3.0 0.2.8 \
  --previous v0.2.7 \
  --target-file CHANGELOG.md \
  --rename-changelog-section
releaseledger release prepare 0.2.8 \
  --previous v0.2.7 \
  --released-at 2026-08-01 \
  --git-base v0.2.7 --git-head HEAD \
  --output-dir .releaseledger/work/0.2.8
releaseledger audit decisions 0.2.8 \
  --output .releaseledger/work/0.2.8/audit-decisions.yaml
```

Use `entry update --add-source-ref REF` for additive provenance. It preserves
existing refs; `--source-ref` replaces the full list and `--clear-source-refs`
clears it explicitly. Internal or rejected commits with complete audit evidence
do not need unrelated public changelog entries.

For a single release section update only, `build VERSION` is the default and
explicit version intent. Rebuild the whole file only when you really mean all
history:

```bash
releaseledger changelog build --strict --output CHANGELOG.md
```

## Optional: taskledger provenance

Taskledger refs (`tl:task-0103`) and PR refs (`github:pr-42`) are optional
provenance. Add them to entries to enrich coverage, but git commits are the
primary source of truth:

```bash
releaseledger entry add 1.2.0 \
  --kind added \
  --summary "Added release bundle storage" \
  --status accepted \
  --source-ref git:abcdef0123456789abcdef0123456789abcdef01 \
  --source-ref tl:task-0103
```

## Create a release

```bash
releaseledger release create 1.2.0 \
  --title "Release 1.2.0" \
  --boundary-ref tl:task-0105 \
  --source-ref tl:task-0103
```

## Add entries

```bash
releaseledger entry add 1.2.0 \
  --kind added \
  --summary "Added release bundle storage" \
  --status accepted \
  --source-ref tl:task-0103
```

Validate entries:

```bash
releaseledger entry lint 1.2.0 --strict
```

## Render changelog output

Use `changelog` to produce review context:

```bash
releaseledger changelog preview 1.2.0 \
  --target-changelog CHANGELOG.md \
  --release-date 2026-06-13
```

Use `build VERSION` to render and insert a final section:

```bash
releaseledger changelog build 1.2.0 \
  --dry-run \
  --strict \
  --unreleased \
  --target-file CHANGELOG.md

releaseledger changelog build 1.2.0 \
  --release-date 2026-06-13 \
  --strict \
  --target-file CHANGELOG.md
```

## Recover a canceled release that shipped

If a canceled release has a real Git tag, reconcile first, then use the explicit restore and bundle replacement workflows:

```bash
releaseledger release reconcile --strict
releaseledger release rename v0.1.0 0.1.0 \
  --replace-canceled-target \
  --reason "Consolidate duplicate canceled bundles." \
  --dry-run
releaseledger release rename v0.1.0 0.1.0 \
  --replace-canceled-target \
  --reason "Consolidate duplicate canceled bundles."
releaseledger release restore 0.1.0 \
  --from-tag v0.1.0 \
  --git-base :root \
  --reason "The tagged release was actually shipped."
releaseledger release chain repair --apply
releaseledger changelog build --all --strict --no-preserve-unreleased --output CHANGELOG.md
releaseledger release reconcile --strict
releaseledger release chain check --strict
```

Never use generic `release update --status` to reopen a canceled or released record, and do not repair release ownership by renaming a changelog section manually.
