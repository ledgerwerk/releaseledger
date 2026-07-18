# Commands

## Root options

```text
releaseledger --cwd PATH ...
releaseledger --json ...
releaseledger --version
```

`--cwd` runs as if started from another directory. `--json` emits
deterministic JSON envelopes.

## Project commands

```text
releaseledger init [--releaseledger-dir PATH] [--project-name NAME]
                  [--external-dir] [--force]
releaseledger storage where
releaseledger config show
releaseledger config set releaseledger_dir PATH [--external-dir]
```

## Release commands

```text
releaseledger release create VERSION [--title TEXT] [--status STATUS]
                                     [--previous VERSION] [--note TEXT]
                                     [--changelog-file PATH]
                                     [--released-at YYYY-MM-DD]
                                     [--boundary-ref REF]
                                     [--source-ref REF]...
                                     [--source-count N]
releaseledger release update VERSION [release metadata options]
                                    [--clear-previous]
                                    [--clear-changelog-file]
                                    [--clear-boundary-ref]
                                    [--clear-source-refs]
                                    [--clear-source-count]
                                    [--clear-released-at] [--force]
releaseledger release tag VERSION [release metadata options]
releaseledger release finalize VERSION [--released-at YYYY-MM-DD]
                                       [--changelog-file PATH]
releaseledger release prepare VERSION [--previous VERSION]
                                      [--released-at YYYY-MM-DD]
                                      [--git-base REF] [--git-head REF]
                                      [--output-dir PATH]
releaseledger release check VERSION [--target-file PATH] [--strict]
                                    [--include-internal]
releaseledger release cancel VERSION [--reason TEXT]
                                    [--superseded-by VERSION]
                                    [--force-released-unshipped]
                                    [--target-file PATH]
                                    [--remove-changelog-section]
                                    [--ignore-missing]
                                    [--rewrite-successors]
                                    [--successor-previous VERSION] [--dry-run]
releaseledger release rename OLD_VERSION NEW_VERSION [--previous VERSION]
                                                      [--title TEXT]
                                                      [--released-at YYYY-MM-DD]
                                                      [--force-released-unshipped]
                                                      [--rewrite-successors]
                                                      [--target-file PATH]
                                                      [--rename-changelog-section]
                                                      [--replace-existing-section]
releaseledger release chain check
releaseledger release chain repair [--dry-run] [--apply]
releaseledger release chain check [--strict]
releaseledger release reconcile [--strict] [--target-file PATH]
releaseledger release list
releaseledger release show VERSION
```

`release tag` creates a release with status `released`. `release finalize`
transitions an existing release to `released`. `release cancel` marks a
release as `canceled` (never shipped; excluded from previous-version
inference). `release rename` moves a release bundle to a new version and
rewrites its front matter, entries, and optionally its changelog section.
`release chain check`/`repair` validate and rebuild predecessor links.

## Entry commands

```text
releaseledger entry add VERSION --kind KIND --summary TEXT [--body TEXT]
                               [--status STATUS] [--audience TEXT]
                               [--scope SCOPE]... [--source-ref REF]...
                               [--path PATH]... [--issue REF]... [--pr REF]...
                               [--breaking] [--internal] [--dry-run]
releaseledger entry add-many VERSION --file FILE [--dry-run] [--strict]
                                    [--guard-commit-subjects]
                                    [--sync-audit]
releaseledger entry update VERSION ENTRY_ID [entry metadata options]
releaseledger entry delete VERSION ENTRY_ID --reason TEXT [--dry-run]
                                    [--force-accepted] [--detach-audit]
releaseledger entry show VERSION ENTRY_ID
releaseledger entry import VERSION --file FILE [--replace]
                                   [--source-ledger LEDGER]
releaseledger entry list VERSION
releaseledger entry lint VERSION [--strict] [--include-status STATUS]...
releaseledger entry prompt VERSION [--source-ref REF]...
                                   [--context-file FILE]
                                   [--format markdown|json]
                                   [--output PATH]
```

`entry lint` checks summary style and record validity. With `--json` it
returns the full per-entry `issues` and `entries` payload, **including on
failure**; the command still exits non-zero. `--strict` fails on warnings.
`entry add-many --dry-run` and `entry add-many` now share the same pre-write
`entry add-many --dry-run --json` preserves the complete `result` payload on
validation failure, including proposed entries, lint findings, coverage projection,
and stable issue codes. Human mode prints one actionable row per issue.
validation path, so strict dry-run results match write-mode gating.

## Batch file format

`entry add-many` expects YAML with a top-level `entries` list:

```yaml
entries:
  - kind: added
    summary: Added release bundle storage
    body: >-
      The storage layer now writes release records, entries, events, and indexes.
    status: accepted
    audience: developer
    scopes: [storage]
    source_refs: [tl:task-0103]
    paths:
      - releaseledger/storage/store.py
    issues: []
    prs: []
    breaking: false
    internal: false
```

## Changelog commands

```text
releaseledger changelog VERSION [--format markdown|json] [--output PATH]
                                [--include-internal]
                                [--target-changelog PATH]
                                [--release-date YYYY-MM-DD]
                                [--include-sources]
                                [--include-status STATUS]... [--lint]

releaseledger build VERSION [--target-file PATH]
                            [--release-date YYYY-MM-DD]
                            [--unreleased]
                            [--include-internal]
                            [--template NAME]
                            [--dry-run]
                            [--replace-existing]
                            [--format markdown|json]
                            [--include-status STATUS]...
                            [--strict]
                            [--allow-empty]
releaseledger build [VERSION] [--all] [--target-file PATH]
                            [--include-release-status STATUS]...
                            [--preserve-unreleased|--no-preserve-unreleased]
                            [--unreleased-version VERSION]
                            [--include-internal]
                            [--include-status STATUS]... [--strict]
                            [--dry-run] [--allow-empty]
Single-release builds reject canceled releases unless `--include-canceled` is
passed for archival/debug rendering. Full builds always exclude canceled releases.
```

`build` with no `VERSION` (or `--all`) is a full rebuild. `build VERSION`
updates one section. Full build omits an empty `## [Unreleased]` section: the
heading and its link reference are rendered only when an unreleased body exists.
`--unreleased-version VERSION` folds a `planned`/`draft`/`candidate` release
into the canonical `## [Unreleased]` section without a version heading, and
excludes that release from the normal release sections.

`build` never invents entries from git commits; entries must be created first
(via `git scaffold`/`git import`, audit, and `entry add-many`). Run
`releaseledger release check VERSION --strict --target-file CHANGELOG.md`
before the final build. Manual Unreleased content is preserved by default;
generated folded Unreleased content is automatically removed once the folded
release is finalized.

`release reconcile --strict` is read-only and compares release records, Git tags,
and changelog headings before finalization.

## Review commands

```text
releaseledger review VERSION [--include-internal]
                        [--include-status STATUS]...
                        [--target-file PATH] [--strict]
                        [--git] [--git-base REF] [--git-head REF]
                        [--require-audit-sheet]
releaseledger release check VERSION [--target-file PATH] [--strict]
                               [--include-internal]
```

Read-only coverage report. It combines release state, entry coverage, orphan
detection, entry lint, and a strict changelog dry-run into one deterministic
report so agents and humans do not need to run `release show`,
`entry list`, `entry lint`, `changelog`, and `build --dry-run`
separately. `--strict` exits non-zero when the release is not OK (uncovered
source refs, lint errors, a dated `planned` release, or a changelog build that
would fail). `release check` is the consolidated final gate built on the same
review machinery.

With `--git`, review also computes coverage from the git commit range
(`--git-base`/`--git-head` or the release's stored git metadata). Strict
mode fails when any include_by_default git commit has no accepted entry
coverage.

## Git-first commands

Releaseledger is git-first: git commit ranges are the canonical evidence of
shipped changes.

```text
releaseledger git range VERSION [--base REF] [--head REF]
                       [--include-merges never|always|nontrivial]
releaseledger git range next --base REF [--head REF]
releaseledger git scaffold VERSION [--base REF] [--head REF]
                         [--status draft] --output PATH
releaseledger git import VERSION [--base REF] [--head REF]
                       [--status draft] --output PATH
releaseledger git import next --base REF [--head REF] --output PATH
releaseledger git evidence VERSION [--base REF] [--head REF]
                         [--output-dir PATH]
```

`git range` inspects the commit range and prints candidate entries.
`git scaffold` generates a metadata-rich `entry add-many` YAML batch from the
range for review and curation; `git import` remains a compatibility alias.
`git evidence` exports deterministic per-commit patches plus a manifest. The
`next` forms are non-persisting previews that do not require a release record.

For a real version, git-backed commands use the release's stored **pinned
snapshot SHAs** unless `--base` or `--head` is supplied explicitly. Resolve
`HEAD` once when attaching the range, then omit `--head` until an intentional
refresh.

## Commit audit sheet commands

```text
releaseledger audit init VERSION [--base REF] [--head REF] [--overwrite]
releaseledger audit show VERSION [--format markdown|json|yaml] [--output PATH]
releaseledger audit apply VERSION --file PATH [--dry-run]
releaseledger audit refresh VERSION [--base REF] [--head REF] [--allow-remove]
releaseledger audit update VERSION --file PATH
releaseledger audit validate VERSION [--phase evidence|complete]
                                  [--strict] [--include-internal]
                                  [--record-event]
releaseledger audit sync VERSION
```

The commit audit sheet is a per-release review artifact that maps every commit
in the git range to a reviewer decision (`needs_review`, `accepted`,
`grouped`, `internal`, `rejected`) and to a release entry. Commit
subjects are evidence-only and must never become changelog prose. Use the
`evidence` phase before entries exist and the `complete` phase after entries
exist. When a sheet exists, `review` emits an `audit` block; pass
`--require-audit-sheet` to gate on a complete sheet.

## Branch commands

```text
releaseledger branch status
releaseledger branch start BRANCH --parent PARENT
releaseledger branch merge BRANCH --into TARGET --release VERSION
```

Optional branch-scoped ledgers. `branch status` compares the current git
branch to `ledger_ref`. `branch start` forks a new ledger. `branch merge` merges entries by `source_refs` (`git:<sha>` dedup).

## Changelog section correction commands

```text
releaseledger changelog-section remove-section VERSION --target-file PATH
                                                  [--ignore-missing] [--dry-run]
releaseledger changelog-section rename-section OLD_VERSION NEW_VERSION
                                                  --target-file PATH
                                                  [--ignore-missing]
                                                  [--replace-existing] [--dry-run]
```

These rewrite release section headings in an existing changelog file without
touching release records. `release rename --rename-changelog-section` and
`release cancel --remove-changelog-section` apply the same corrections inline.
