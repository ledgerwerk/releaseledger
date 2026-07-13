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
```

This creates `.releaseledger.toml` and the default state layout:

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

## Create a release and pin the git snapshot

```bash
releaseledger release create 1.2.0 \
  --previous 1.1.0 \
  --released-at 2026-06-14

releaseledger release update 1.2.0 \
  --git-base v1.1.0 \
  --git-head HEAD
```

After the snapshot is attached, omit `--head` unless you intentionally want to
refresh the stored snapshot to a newer commit.

## Create audit evidence and scaffold entries

```bash
releaseledger git evidence 1.2.0 --output-dir /tmp/1.2.0-evidence
releaseledger audit init 1.2.0
releaseledger audit show 1.2.0 --format yaml --output /tmp/1.2.0-audit.yaml
releaseledger git scaffold 1.2.0 \
  --output /tmp/1.2.0-entries.yaml
```

Curate the audit annotations, then validate the evidence phase:

```bash
releaseledger audit apply 1.2.0 \
  --file /tmp/1.2.0-audit-decisions.yaml \
  --dry-run
releaseledger audit apply 1.2.0 \
  --file /tmp/1.2.0-audit-decisions.yaml
releaseledger audit validate 1.2.0 --phase evidence --strict
```

Edit the entry scaffold to write user-facing summaries from reviewed behavior,
then validate and write entries atomically:

```bash
releaseledger entry add-many 1.2.0 \
  --file /tmp/1.2.0-entries.yaml \
  --dry-run \
  --strict \
  --guard-commit-subjects
releaseledger entry add-many 1.2.0 \
  --file /tmp/1.2.0-entries.yaml \
  --strict \
  --guard-commit-subjects \
  --sync-audit
releaseledger audit validate 1.2.0 --phase complete --strict --include-internal
```

## Run the final gate and build the changelog

```bash
releaseledger release check 1.2.0 --strict --target-file CHANGELOG.md
releaseledger release finalize 1.2.0 --released-at 2026-06-14
releaseledger build 1.2.0 --strict --target-file CHANGELOG.md
```

For a single release section update only, `build VERSION` is the default and
explicit version intent. Rebuild the whole file only when you really mean all
history:

```bash
releaseledger build --strict --target-file CHANGELOG.md
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
releaseledger changelog 1.2.0 \
  --target-changelog CHANGELOG.md \
  --release-date 2026-06-13
```

Use `build VERSION` to render and insert a final section:

```bash
releaseledger build 1.2.0 \
  --dry-run \
  --strict \
  --target-file CHANGELOG.md

releaseledger build 1.2.0 \
  --release-date 2026-06-13 \
  --strict \
  --target-file CHANGELOG.md
```
