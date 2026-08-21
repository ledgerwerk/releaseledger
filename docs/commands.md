# Commands

## Unified CLI contract

```text
releaseledger --root PATH ...
releaseledger --root PATH ...
releaseledger --json ...
releaseledger --version
```

`--root` selects the project without changing the process working directory.
`--cwd` remains accepted with a structured deprecation warning. `--json`
emits the deterministic `ledgerwerk.cli.v1` success/error envelope; warnings
are included in `warnings` rather than mixed into stdout.

Every command has a space-separated canonical path and exits with `0` for
success, `1` for a failed check, `2` for usage/input errors, `3` for an
unavailable dependency, `4` for conflicts or stale plans, and `5` for
external-process failures.

## Common and migration commands

```text
releaseledger commands
releaseledger help release review
releaseledger status [--check]
releaseledger info
releaseledger doctor [--check]
releaseledger next-action
releaseledger storage where
releaseledger storage validate [--strict]
releaseledger storage set data --storage project|external|user-data
                             [--storage-root PATH] [--scope project|local]
                             [--dry-run]
releaseledger storage clear-override data [--dry-run]
releaseledger migrate status
releaseledger migrate plan storage-layout [--storage ...] [--output PLAN.json]
releaseledger migrate apply storage-layout [--plan-file PLAN.json]
                             --reason TEXT [--dry-run]
releaseledger migrate recover --journal PATH [--policy auto|resume|rollback] [--dry-run]
releaseledger migrate cleanup storage-layout [--dry-run]
                             [--yes --reason TEXT]
releaseledger config show
releaseledger config validate [--strict]
```

Migration plans use `releaseledger.storage-migration-plan.v2`, carry one shared
migration ID, exact source/before/target fingerprints, and are rejected with
exit `4` if source or destination state changes before apply. Physical state
belongs to the Ledgercore schema-3 journal; Releaseledger writes a completion
receipt only after commit. Recovery policies are explicit and dry-run is
read-only. Cleanup is separate, confirmation-gated, and requires a reason.
`storage migrate ...` remains a deprecated compatibility entry point.

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
releaseledger release restore VERSION --reason TEXT [--to STATUS]
                                      [--from-tag TAG] [--git-base REF]
                                      [--previous VERSION] [--clear-previous]
                                      [--released-at YYYY-MM-DD] [--dry-run]
releaseledger release prepare VERSION [--previous VERSION]
                                      [--released-at YYYY-MM-DD]
                                      [--git-base REF] [--git-head REF]
                                      [--output-dir PATH]
releaseledger release check VERSION [--target-file PATH] [--strict]
                                    [--include-internal]
                                    [--phase current|finalize|published]
                                    [--released-at YYYY-MM-DD]
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
                                                      [--replace-canceled-target]
                                                      [--reason TEXT] [--dry-run]
releaseledger release chain check
releaseledger release chain repair [--dry-run] [--apply]
releaseledger release chain check [--strict]
releaseledger release reconcile [--strict] [--target-file PATH]
releaseledger release list
releaseledger release show VERSION
```

`release tag` creates a release with status `released`. `release finalize`
transitions an existing release to `released` and is a compatible no-op when
that release is already finalized. `release cancel` marks a release as
`canceled` (never shipped; excluded from previous-version inference).
`release restore` is the only supported way to reopen a canceled release or
restore one from a matching shipped Git tag. Generic `release update --status`
cannot bypass terminal lifecycle states. `release rename` moves a release
bundle; `--replace-canceled-target` atomically displaces an obsolete canceled
bundle. `release chain check`/`repair` validate and rebuild predecessor links.

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
                                    [--source-ref REF]...
                                    [--add-source-ref REF]...
                                    [--remove-source-ref REF]...
                                    [--clear-source-refs]
releaseledger entry delete VERSION ENTRY_ID --reason TEXT [--dry-run]
                                    [--force-accepted] [--detach-audit]
releaseledger entry move SOURCE_VERSION ENTRY_ID TARGET_VERSION --reason TEXT
                                  [--renumber] [--move-audit-targets] [--dry-run]
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

### Two-step model

`releaseledger changelog` renders review context. Use it when a human or
coding agent needs release metadata, entries, target-file guidance, and optional
lint findings. Pass `--include-sources` to include provenance refs in Markdown
output.

`releaseledger build` renders the final changelog section and inserts it into
the target file. Use `--dry-run` before writing and `--replace-existing` when
re-rendering an existing release section. Pass `--template NAME` to select a
named changelog template profile.

`build` never invents entries from git commits. Git commit ranges require
`git scaffold` / audit / entry curation before a strict build can pass.

### Full changelog rebuild

`releaseledger build` with no `VERSION` (or `releaseledger build --all`)
rebuilds the **complete** target file from ledger state:

- `releaseledger build VERSION` updates one release section.
- `releaseledger build` or `releaseledger build --all` rebuilds the whole
  target file from ledger state.
- Full rebuild excludes internal entries and non-released releases by default.
- Use `--include-internal` only for internal release notes.
- Use `--include-release-status` to include candidate/planned sections
  explicitly.
- Full rebuild preserves the `## [Unreleased]` body by default
  (`--no-preserve-unreleased` clears it) and is a whole-file rewrite (no
  `--replace-existing`).
- An **empty** `## [Unreleased]` section is omitted: the heading (and its
  link reference) is rendered only when an unreleased body exists.
- `--unreleased-version VERSION` folds a `planned`/`draft`/`candidate`
  release's accepted entries into the canonical `## [Unreleased]` section
  without a version heading, and excludes that release from the normal release
  sections. It is rejected for a missing, `canceled`, `yanked`, or already
  `released` version, and is valid only for full builds.
- Generated folded Unreleased content is automatically removed once the folded
  release is finalized.
- Manual Unreleased content (without `<!-- releaseledger:unreleased-start`
  markers) is always preserved by default.

### Provenance and canceled releases

`source_refs` identifies the one coverage owner for a commit or other
coverable change identity. `sources` records supporting provenance for
additional entries describing the same commit. Supporting entries render as
normal bullets and are not orphans, while duplicate `source_refs` ownership
is rejected with remediation to move the reference to `sources`.

A full build excludes canceled releases. A single-release build rejects a
canceled version unless `--include-canceled` is explicitly used for
archival/debug output.

### Group modes

Entry kinds are grouped for rendering. The mode is set by
`changelog_group_mode` in `[changelog]`.

- `keepachangelog` (Keep a Changelog 1.1.0) renders exactly six groups in
  order: `Added`, `Changed`, `Deprecated`, `Removed`, `Fixed`,
  `Security`. Extended kinds map onto these groups:

  - `docs -> changed`
  - `quality -> changed`
  - `internal -> changed` (hidden unless `--include-internal`)

- `extended` (the default) renders `Documentation`, `Quality`, and
  `Internal` as their own groups alongside the Keep a Changelog groups.

### Template configuration

The default `.releaseledger.toml` contains:

```toml
[changelog]
output = "CHANGELOG.md"
trim = true
render_always = false
header = """
body = """
## {% if release.date %}[{{ release.version }}] - {{ release.date }}{% else %}[{{ release.version }}] - Unreleased{% endif %}

{% for group in groups %}
### {{ group.title }}
{% for entry in group.entries %}
- {% if entry.breaking %}**BREAKING:** {% endif %}{{ entry.summary }}
{% endfor %}

{% endfor %}
"""
footer = "<!-- generated by releaseledger -->"
postprocessors = []
```

### Template context

Templates run in a sandboxed Jinja2 environment and may access:

`project`
: Project or ledger name.

`release`
: The target release payload, including `version` and effective `date`.

`entries`
: Included entry payloads.

`groups`
: Entries grouped by changelog title.

`releases`
: Known release payloads.

### Postprocessors

Postprocessors apply literal string replacements after template rendering:

```toml
postprocessors = [
  { pattern = "releaseledger", replace = "Releaseledger" },
]
```

### Strict builds

Run `releaseledger release check VERSION --strict --target-file CHANGELOG.md`
before a final public build.

`releaseledger build --strict` blocks on entry lint errors, empty included
entries unless `--allow-empty` is supplied, release source refs that are
not covered by included entries, and (for releases with stored git range
metadata) git commits that have no accepted entry coverage. Internal-only
entries satisfy audit coverage but are reported as excluded from the
public changelog. A dated `planned` release is also a strict-state failure for
the final public workflow.

### Section correction

When a release section in the target changelog is stale (wrong version number
or a canceled release), correct it with the section helpers rather than editing
the file by hand:

```text
releaseledger changelog-section rename-section OLD NEW --target-file CHANGELOG.md
releaseledger changelog-section remove-section VERSION --target-file CHANGELOG.md
```

`release rename --rename-changelog-section` and
`release cancel --remove-changelog-section` apply the same corrections as part
of a release correction. Both fail when the source section is missing unless
`--ignore-missing` is passed, and `rename-section` fails when the
destination section already exists unless `--replace-existing` is passed.

`release rename --dry-run` previews the bundle move, entry and audit ownership,
successor rewrites, and changelog action without writing. Planned releases need
no force flag; title and status are preserved unless explicitly changed. If a
target changelog contains the old heading and the rename flag is omitted, the
command reports the stale heading and prints the exact section-rename command.

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
                               [--phase current|finalize|published]
                               [--released-at YYYY-MM-DD]
```

Read-only coverage report. It combines release state, entry coverage, orphan
detection, entry lint, and a strict changelog dry-run into one deterministic
report so agents and humans do not need to run `release show`,
`entry list`, `entry lint`, `changelog`, and `build --dry-run`
separately. `--strict` exits non-zero when the release is not OK (uncovered
source refs, lint errors, a dated `planned` release, or a changelog build that
would fail). `release check` is the consolidated final gate built on the same
review machinery.

The `current` phase preserves the existing persisted-state check. `finalize`
accepts a proposed date, checks readiness to transition to `released`, and does
not require a tag. `published` requires released status/date, a matching tag
when Git integration is active, a consistent changelog, and clean
reconciliation. Every failed gate appears in human output and in JSON
`failed_checks`, with structured `next_actions` where a safe command exists.

For audit-backed Git coverage, accepted/grouped decisions require accepted
public-entry coverage. Complete internal and rejected decisions are accounted
for by audit evidence in public mode; `--include-internal` requires an accepted
internal entry for internal decisions. Unresolved or incomplete rows block.

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
releaseledger audit decisions VERSION --output PATH
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

`audit decisions` generates one mutable YAML row per commit, pre-filling changed
paths without marking a row inspected. `audit apply --dry-run` reports all
missing evidence fields for completed decisions before any write.

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
