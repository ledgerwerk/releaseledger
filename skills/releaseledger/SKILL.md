---
name: releaseledger
description: Manage project-local release records, release entries, changelog source, and CHANGELOG.md builds
license: Apache-2.0
compatibility: opencode
metadata:
  audience: coding-agents
  workflow: release-management
---

## When to use this skill

Use releaseledger when a project needs durable, project-local release state: release records, release notes, changelog entries, generated changelog source, or updates to `CHANGELOG.md`.

Releaseledger is git-first. Git commit ranges are the canonical evidence of shipped changes. Taskledger, issue trackers, and PR descriptions are optional provenance.

Git evidence is not changelog prose. A commit subject or body must never be copied, lightly edited, title-cased, or otherwise used as a release entry summary. Commit refs may identify coverage; release entry summaries must be written from reviewed behavior, public API/docs impact, changed paths, tests, and diff evidence.

Releaseledger is separate from taskledger. Do not treat `.releaseledger/` as task state and do not require taskledger to be installed.

## Never do these things

- Do not edit `.releaseledger/` storage files directly. Use releaseledger commands or the public `releaseledger.api.*` surface.
- Release and entry records use validated per-record revisions. Events are
  append-only operation rows without wall-clock timestamps or before/after
  deltas; use git history for chronological review.
- Do not invent a release date. Use the date explicitly provided by the user, the persisted `released_at` value, or an unreleased heading.
- Do not include internal entries unless the user explicitly asks for internal release notes or passes an include-internal option.
- Do not silently overwrite an existing release section in `CHANGELOG.md`. Use the supported replace/update option only when explicitly requested.
- Do not duplicate an existing release heading.
- Do not remove existing historical changelog sections.
- Do not change release status just to build a changelog.
- Do not import or call `releaseledger.storage.*`, `releaseledger.services.*`, or `releaseledger.domain.*` from ad-hoc scripts during normal release work. Use the CLI or public `releaseledger.api.*`.
- Do not create or switch to external releaseledger state unless the project config already declares it or the user explicitly requests it.
- Prefer portable relative paths with `releaseledger_dir_policy = "external"` over machine-specific absolute paths.
- If releaseledger reports that releaseledger_dir escapes the workspace root, run `releaseledger storage where` or `releaseledger config show` before mutating anything.
- Do not treat generated changelog source as final prose unless the command requested a final build.
- Do not import taskledger, inspect `.taskledger/`, or dereference task refs.
  Accept taskledger evidence only as caller-supplied context and global refs.
- Do not use git commit messages as changelog entries. Do not paste, paraphrase, title-case, or mechanically convert commit subjects into `summary` values. A commit message is only provenance for locating evidence.
- Do not run multiple releaseledger mutating commands concurrently. Especially do not fan out `entry add` calls. Use `entry add-many ... --dry-run` followed by one `entry add-many`, or run single mutating commands sequentially and re-read state after any failure.
- During normal release work, do not inspect `releaseledger` package internals. If CLI output is insufficient, first try the JSON form of the public command. If the JSON form is still insufficient, write a change request and stop using source code as a workaround unless the user explicitly asks for releaseledger debugging.

## Core agent command path

Use this path first for routine release work:

```text
releaseledger --version
releaseledger init
releaseledger status
releaseledger info
releaseledger doctor
releaseledger next-action
releaseledger release list
releaseledger release show VERSION
releaseledger release create VERSION
releaseledger release update VERSION
releaseledger release prepare VERSION
releaseledger release tag VERSION
releaseledger release finalize VERSION
releaseledger release check VERSION [--phase current|finalize|published] [--strict] [--target-file PATH]
releaseledger entry add VERSION --kind KIND --summary TEXT
releaseledger entry add-many VERSION --file FILE --dry-run [--strict] [--guard-commit-subjects]
releaseledger entry add-many VERSION --file FILE [--strict] [--guard-commit-subjects] [--sync-audit]
releaseledger entry show VERSION ENTRY_ID
releaseledger entry update VERSION ENTRY_ID
releaseledger entry update VERSION ENTRY_ID --add-source-ref REF
releaseledger entry import VERSION --file FILE
releaseledger entry list VERSION
releaseledger entry lint VERSION --strict
releaseledger entry prompt VERSION --source-ref REF --context-file FILE
releaseledger changelog preview VERSION --format markdown|json
releaseledger changelog build VERSION
releaseledger build VERSION --dry-run
releaseledger release review VERSION [--strict] [--git] [--git-base REF] [--git-head REF] [--require-audit-sheet]
releaseledger git range VERSION [--base REF] [--head REF]
releaseledger git scaffold VERSION [--base REF] [--head REF] --output PATH
releaseledger git import VERSION [--base REF] [--head REF] --output PATH
releaseledger git evidence VERSION [--base REF] [--head REF] --output-dir DIR
releaseledger audit init VERSION [--base REF] [--head REF] [--overwrite]
releaseledger audit show VERSION [--format markdown|json|yaml] [--output PATH]
releaseledger audit decisions VERSION --output PATH
releaseledger audit apply VERSION --file PATH [--dry-run]
releaseledger audit refresh VERSION [--base REF] [--head REF] [--allow-remove]
releaseledger audit update VERSION --file PATH
releaseledger audit validate VERSION [--phase evidence|complete] [--strict] [--include-internal]
releaseledger audit sync VERSION
releaseledger commands
releaseledger help release review
releaseledger branch status
releaseledger build --strict --target-file CHANGELOG.md
releaseledger build VERSION --strict --target-file CHANGELOG.md
releaseledger build VERSION --strict --target-file CHANGELOG.md --replace-existing

releaseledger storage where
releaseledger storage validate --strict
releaseledger migrate status
releaseledger migrate plan storage-layout --output migration-plan.json
releaseledger migrate apply storage-layout --plan-file migration-plan.json --reason TEXT
releaseledger migrate recover --journal PATH --policy auto|resume|rollback [--dry-run]
releaseledger config show
releaseledger config validate
```

Root options belong before the subcommand:

```text
releaseledger --root PATH --json release show VERSION
```

`--root` is the canonical project selector. `--cwd` remains a deprecated compatibility alias. JSON output uses the
`ledgerwerk.cli.v1` envelope and structured warnings.

## Fresh context entry protocol

1. Run `releaseledger --version`.
2. Run `releaseledger storage where` or `releaseledger --json storage where`.
3. Run `releaseledger config show` to verify the resolved configuration.
4. Run `releaseledger release list`.
5. For a known release, run `releaseledger release show VERSION`.
6. Run `releaseledger entry list VERSION`.
7. Generate machine context when needed:
   `releaseledger changelog VERSION --format json`.
8. Do not inspect `.releaseledger/` internals unless the CLI cannot start and the user explicitly requested forensic inspection.

## Release creation protocol

1. Create a planned or candidate release:
   `releaseledger release create VERSION --title "Release VERSION"`.
2. Set `--previous VERSION` when the previous version is known and should appear in generated context.
3. Set `--released-at YYYY-MM-DD` only when the date is known.
4. Use `releaseledger release tag VERSION` for an immediately released/tagged release.
5. Use `releaseledger release finalize VERSION --released-at YYYY-MM-DD` to transition an existing planned/draft/candidate release to released.
6. Verify with:
   `releaseledger release show VERSION`.

## Correcting canceled or misnumbered releases

Use this when a recorded release was never actually shipped (no git tag, no
package publish) or was recorded under the wrong version number. Never edit
`.releaseledger/` storage directly; never use `yanked` for a never-shipped
release.

Decision tree:

1. Preview the correction first; planned releases need no force flag:
   `releaseledger release rename OLD NEW --previous PREV --target-file CHANGELOG.md`
   `--rename-changelog-section --dry-run`.
2. Apply the rename with `--rename-changelog-section` when the old generated
   heading should move. Without that flag, inspect the reported stale heading
   and run the exact suggested `changelog-section rename-section` command.
   The rename preserves title and status unless explicitly overridden.
3. Prepare the corrected release in one step:
   `releaseledger release prepare NEW --previous PREV --released-at DATE`
   with `--git-base` and `--git-head` when the range is known.
3. If the wrong version should remain as a visible audit tombstone, use
   `release cancel --reason "..." --superseded-by VERSION` (sets status
   `canceled`).
4. When backfilling old releases, always pass `--previous` explicitly, then run
   `release chain check`. Repair with `release chain repair --dry-run` then
   `--apply`.
5. Clear an optional field (e.g. a root release's `previous_version`) with
   `release update VERSION --clear-previous`.
6. Build the changelog from the net shipped baseline, then bump the package
   version.

For a complete release-day correction, use this sequence:

```bash
releaseledger release rename OLD NEW --previous PREV \
  --target-file CHANGELOG.md --rename-changelog-section --dry-run
releaseledger release rename OLD NEW --previous PREV \
  --target-file CHANGELOG.md --rename-changelog-section
releaseledger release prepare NEW --previous PREV --released-at DATE \
  --git-base PREV_TAG --git-head HEAD --output-dir .releaseledger/work/NEW
releaseledger audit decisions NEW --output .releaseledger/work/NEW/audit-decisions.yaml
releaseledger audit apply NEW --file .releaseledger/work/NEW/audit-decisions.yaml --dry-run
releaseledger audit apply NEW --file .releaseledger/work/NEW/audit-decisions.yaml
releaseledger release check NEW --phase finalize --released-at DATE --strict \
  --target-file CHANGELOG.md
releaseledger changelog build NEW --output CHANGELOG.md --strict --replace-existing
releaseledger release finalize NEW --released-at DATE
releaseledger release check NEW --phase published --strict --target-file CHANGELOG.md
```

Internal or rejected commits with complete audit evidence are accounted for by
the audit sheet and do not need unrelated public entry refs. Use
`--add-source-ref` to add genuine provenance while preserving existing refs;
`--source-ref` replaces the complete list, and `--clear-source-refs` is the
explicit clear operation.

Example (canceled v0.4.3, intended v0.5.0 from v0.4.2):

```bash
releaseledger release chain check
releaseledger release chain repair --apply
releaseledger release rename v0.4.3 v0.5.0 \
  --previous v0.4.2 \
  --force-released-unshipped \
  --target-file CHANGELOG.md \
  --rename-changelog-section
```

Or keep the tombstone:

```bash
releaseledger release cancel v0.4.3 \
  --reason "Never shipped; superseded by v0.5.0" \
  --superseded-by v0.5.0 \
  --force-released-unshipped
```

## Changelog entry protocol

Use this when the user asks to add release-note material.

1. Resolve the target version:
   `releaseledger release show VERSION`.
2. Add entries with one of the controlled kinds:
   `added`, `changed`, `fixed`, `removed`, `deprecated`, `security`, `docs`, `quality`, `internal`.
   `documentation` and `doc` normalize to `docs`.
3. Keep summaries one line, user-facing, and free of trailing periods unless the project style requires punctuation.
4. Write each summary from reviewed product behavior, API/docs impact, changed paths, tests, and diffs. Never derive it from a git commit subject/body.
5. A valid summary should still make sense if all commit hashes and commit messages are hidden.
6. Use `--body` for longer explanation and `--path`, `--issue`, and `--pr` for traceability.
7. Use `--breaking` for breaking changes.
8. Use `--internal` for implementation-only notes that should be hidden from public changelogs by default. `kind: internal` alone is not enough in Keep a Changelog mode because extended kinds can render under `Changed`; set `internal: true` or reject the entry.
9. Verify with:
   `releaseledger entry list VERSION`.
10. Use `--status accepted` for final notes, `draft` for incomplete notes, and
    `rejected` for retained-but-excluded proposals.
11. Link external evidence with `--source-ref tl:task-0103`; never make
    releaseledger inspect the external ledger.

Example:

```bash
releaseledger entry add 1.2.0 --kind added \
  --summary "Added release bundle storage" \
  --status accepted \
  --source-ref tl:task-0103 \
  --path releaseledger/storage/store.py
```

## Batch entry protocol

When release notes need taskledger context, first use taskledger to inspect
tasks and validation evidence. Then pass that evidence into releaseledger as
opaque context and global refs:

```bash
releaseledger entry prompt VERSION --source-ref tl:task-0103 \
  --context-file /tmp/task-0103.json --output /tmp/prompt.md
releaseledger entry add-many VERSION --file /tmp/VERSION-entries.yaml \
  --dry-run --strict --guard-commit-subjects
releaseledger entry add-many VERSION --file /tmp/VERSION-entries.yaml \
  --strict --guard-commit-subjects --sync-audit
releaseledger entry lint VERSION --strict
releaseledger entry list VERSION
```

Batch creation validates every entry before writing any entry. If any item is
invalid, correct the YAML and rerun the dry run; do not add entries one at a
time to bypass atomic validation.

For batch imports, verify fields with `releaseledger --json entry add-many VERSION --file FILE --dry-run` and inspect `result.entries[*].internal`, `breaking`, and other fields before assuming the batch parser dropped a field.

## Changelog source protocol

Use this when the user wants release-note source material for review or drafting.

```bash
releaseledger changelog preview VERSION --target-changelog CHANGELOG.md --release-date YYYY-MM-DD
releaseledger changelog preview VERSION --format json
releaseledger changelog preview VERSION --include-internal
releaseledger changelog preview VERSION --include-status accepted --include-status draft
releaseledger changelog preview VERSION --lint
```

Rules:

1. Treat `releaseledger changelog VERSION` as source/context unless the command name or option explicitly says build/update.
2. Check whether internal entries were filtered.
3. Preserve warnings, release metadata, and entry grouping when handing source to a human or another tool.
4. If no date is provided and the release has no persisted `released_at`, keep the output unreleased or explicitly say no date was available.

## Commit audit sheet protocol

Use this for any git-backed changelog or release-note backfill.

1. Attach or resolve the git range once with
   `releaseledger release update VERSION --git-base PREV_TAG --git-head HEAD`
   or `releaseledger release prepare ...`.
2. After the snapshot is pinned, omit `--head` unless intentionally refreshing
   the stored snapshot.
3. Create the sheet with `releaseledger audit init VERSION`.
4. Generate the mutable decisions worksheet with
   `releaseledger audit decisions VERSION --output audit-decisions.yaml`.
   It pre-fills changed paths but never marks a commit inspected.
5. Curate row annotations and run `audit apply --dry-run`; completed decisions
   must include `inspected: true`, `inspected_paths`, and `observed_behavior`.
   Apply only after the dry-run reports no evidence deficiencies.
6. Never copy, paraphrase, title-case, or mechanically convert
   `evidence_subject` into `summary`.
7. Validate the evidence phase before creating entries:
   `releaseledger audit validate VERSION --phase evidence --strict`.
8. Generate the batch scaffold with `releaseledger git scaffold VERSION --output entries.yaml`
   (or `git import`, which remains a compatibility alias).
9. Add entries atomically with
   `releaseledger entry add-many VERSION --file entries.yaml --dry-run --strict --guard-commit-subjects`
   followed by
   `releaseledger entry add-many VERSION --file entries.yaml --strict --guard-commit-subjects --sync-audit`.
10. Validate the complete phase after entries exist:
    `releaseledger audit validate VERSION --phase complete --strict --include-internal`.
11. Run `releaseledger release check VERSION --phase finalize --released-at DATE --strict
    --target-file CHANGELOG.md` before any final build. Use the
    `published` phase after finalization and tagging.

`audit init` writes one `needs_review` row per git candidate commit. Decisions
are `needs_review`, `accepted`, `grouped`, `internal`, and `rejected`.
`public_impact` values are `public`, `docs`, `internal`, `none`, and
`unknown`. The sheet is evidence state, not changelog prose.

## CHANGELOG.md build protocol

Use this when the user asks to build, generate, or update `CHANGELOG.md`.

0. For a git-backed release, run the consolidated read-only gate first:
   `releaseledger release check VERSION --strict --target-file CHANGELOG.md`.
   If it reports missing `git:<sha>` coverage, audit failures, lint errors, or
   release-state blockers, stop and resolve them before building.
1. Generate a strict dry run first:
   `releaseledger build VERSION --dry-run --strict --target-file CHANGELOG.md`.
2. Inspect the rendered section:
   - heading version is correct
   - release date is exact, omitted, or marked unreleased according to user intent
   - internal entries are absent unless requested
   - groups appear in deterministic order
   - breaking changes are visible
3. Apply the build:
   `releaseledger build VERSION --target-file CHANGELOG.md`.
4. Read `CHANGELOG.md` back and verify:
   - no duplicate release heading exists
   - new section is below `## Unreleased` when that heading exists
   - prior release history is preserved
   - the file has one final newline
5. If the target already has the version section, do not replace it unless the user explicitly requested replacement. Use the supported replace flag and state that replacement was used.
6. Accepted entries are included by default. Include draft entries only for
   explicitly draft output and preserve the draft-quality warning.
7. Do not use `--allow-empty` unless an empty release section is intentional.
8. If the user explicitly said the release shipped, finalize it before the
   final public build:
   `releaseledger release finalize VERSION --released-at YYYY-MM-DD`.
9. To rebuild the **whole** target file from ledger state, use the
   conventional full-build command:
   - `releaseledger build --dry-run --target-file CHANGELOG.md`
   - `releaseledger build --target-file CHANGELOG.md`
     `build` with no VERSION (or `build --all`) regenerates every selected
     release section newest-first, preserves the `## [Unreleased]` body by
     default, excludes internal entries and non-released releases by default,
     and is a whole-file rewrite (no `--replace-existing`). `build VERSION`
     keeps the single-section insert/replace behavior.

## Changelog build intent protocol

An explicit release version means single-section intent:

```bash
releaseledger build VERSION --strict --target-file CHANGELOG.md
```

Use a full document rebuild only when the user explicitly asks for all history,
for example "rebuild the whole changelog" or "regenerate all release
sections":

```bash
releaseledger build --strict --target-file CHANGELOG.md
```

If a git range exists, strict build must pass. If it fails because commits are
not covered by included entries, stop and create/update entries from a git audit
worksheet. Do not write a partial changelog.

If review only passes with `--include-internal`, tell the user that public
`CHANGELOG.md` will omit internal-only entries. Use `--include-internal` only for
internal release notes.

## Complete historical reconstruction protocol

When the user requests "all versions," "all changes," "full history," or equivalent:

```text
1. releaseledger release reconcile --strict
2. releaseledger release import-tags --dry-run
3. releaseledger release import-tags --apply
4. For every release in ascending semantic order:
   a. verify tag/date/predecessor/range
   b. initialize or refresh audit sheet
   c. inspect every commit and diff in the exact range
   d. curate entries without copying commit subjects
   e. preserve PR/contributor metadata
   f. validate audit completeness
5. releaseledger build --all --strict --require-complete-history
6. releaseledger release reconcile --strict
```

Explicit prohibitions:

- do not interpret "all" as all existing releaseledger records;
- do not collapse multiple tagged releases into `Previous Unreleased Changes`;
- do not skip patch releases;
- do not call the history complete while `tag_without_release` exists;
- do not label a contributor "new" before complete-history validation;
- do not copy commit subjects as summaries.

### GitHub attribution

For repositories with a GitHub `repository_url`, generated release sections include
GitHub-style attribution when metadata exists:

```markdown
### Changed

- Updated pygoruut dependency to version 0.8.0 by @neurlang in [#3]

### New Contributors

- @neurlang made their first contribution in [#3]

**Full Changelog**: [v0.6.7...v0.7.0]
```

Configuration:

```toml
[changelog]
github_attribution = true
github_whats_changed = true
github_new_contributors = true
github_full_compare = true
github_duplicate_categorized_entries = false
generated_notice = true
generated_notice_text = "> **Generated by releaseledger.** Manual edits may be overwritten."
generated_sentinel = "<!-- releaseledger:generated-file -->"
```

### Tag import

Use `release import-tags` to discover and backfill git tags:

```bash
releaseledger release import-tags --dry-run
releaseledger release import-tags --apply
```

This creates released records for every semver tag that does not already have one.
The command is idempotent and never overwrites existing records.

## Release review protocol

Use this to answer "what did I already add for this release?" before adding
new entries or building the changelog. `releaseledger review VERSION` is
read-only: it never writes `CHANGELOG.md` and never mutates release state.

```bash
releaseledger review VERSION
releaseledger --json review VERSION
releaseledger review VERSION --include-status accepted --include-status draft
releaseledger review VERSION --strict --target-file CHANGELOG.md
releaseledger release check VERSION --strict --target-file CHANGELOG.md
```

Rules:

1. Run review before adding an entry. If the same `source_ref` is already
   covered by an accepted entry, update the existing entry instead of adding a
   duplicate. Search by `source_refs`, then `prs`/`issues`, then `sources`,
   then a kind+summary+paths fingerprint as a last resort.
2. Each expected ref (`release.source_refs` plus coverable `boundary_ref`) is
   classified as `covered`, `draft_only`, `rejected_only`, `internal_only`, or
   `missing`. Non-coverable boundary refs (`git-range:*`, `git-tag:*`, etc.)
   produce no coverage row.
3. With `--git`, expected refs also include `git:<sha>` for every
   include_by_default commit in the range. Strict mode fails when any such
   commit has no accepted entry coverage.
   Treat `draft_only` as pending review and `rejected_only` as possibly
   intentional; confirm before re-adding.
4. Orphan accepted entries (no `source_refs`, `issues`, `prs`, or `sources`)
   should get provenance or be removed.
5. `--strict` exits non-zero when the release is not OK. It mirrors
   `build --strict`, so it can fail on uncovered refs, lint errors, a missing
   release date in Keep a Changelog mode, a dated `planned` release, or other
   build blockers. Review alone never writes the changelog.
6. `git:<sha>` source refs are first-class coverage identities (not just evidence). A commit in the release range should have an accepted entry covering its `git:<sha>` in `source_refs`.

## Git-first workflow

The recommended workflow uses git commit ranges as the canonical evidence. For any non-empty git range, the commit audit is mandatory, not optional.
Every `include_by_default` commit must be inspected and accounted for before
entries are accepted or `CHANGELOG.md` is built.

Mandatory audit invariant:

- Let `C` be the candidate commits returned by
  `releaseledger --json git range VERSION --base PREV_TAG --head HEAD`.
- Every `git:<sha>` in `C` must appear in exactly one curated entry's
  `source_refs`, unless it is intentionally represented by a rejected/internal
  entry with an explicit rationale.
- One entry may cover multiple small commits, but it must preserve all covered
  `git:<sha>` refs.
- Aggregate `git log`, aggregate `git diff --stat`, tag dates, version bumps, or
  package metadata changes are not sufficient review evidence.
- `releaseledger git range` commit subjects are identity only. They prove which
  commits exist; they do not prove the commit was reviewed and must not become
  release prose.

Workflow:

```bash
# 1. Create or update the release and attach the exact git range.
releaseledger release create VERSION --previous PREV_VERSION --released-at YYYY-MM-DD
releaseledger release update VERSION --git-base PREV_TAG --git-head HEAD

# 2. From this point, omit --head unless intentionally refreshing the snapshot.

# 3. Export deterministic evidence and the canonical audit sheet.
releaseledger git evidence VERSION --output-dir evidence/
releaseledger audit init VERSION
releaseledger audit show VERSION --format yaml --output audit.yaml

# 4. Curate row annotations and validate only the evidence phase.
releaseledger audit apply VERSION --file audit-decisions.yaml --dry-run
releaseledger audit apply VERSION --file audit-decisions.yaml
releaseledger audit validate VERSION --phase evidence --strict

# 5. Create a coverage scaffold and validate entries atomically.
releaseledger git scaffold VERSION --output entries.yaml
releaseledger entry add-many VERSION --file entries.yaml \
  --dry-run --strict --guard-commit-subjects
releaseledger entry add-many VERSION --file entries.yaml \
  --strict --guard-commit-subjects --sync-audit

# 6. Run one complete read-only gate.
releaseledger release check VERSION --strict --target-file CHANGELOG.md

# 7. Finalize only when shipped intent is explicit, then build the requested scope.
releaseledger release finalize VERSION --released-at YYYY-MM-DD
releaseledger build VERSION --strict --target-file CHANGELOG.md
```

No coverage, no build:

- If `releaseledger review VERSION --git --strict` fails, do not run
  `releaseledger build`.
- If the user asks for a fast changelog and the range has not been audited,
  produce the audit worksheet and stop before mutation.
- If a commit cannot be understood from the patch, mark it draft/internal and
  ask for project context instead of inventing a user-facing summary.

## Templating protocol

Releaseledger changelog templates are configured in `.releaseledger.toml` under `[changelog]`.

Expected keys:

```toml
[changelog]
output = "CHANGELOG.md"
trim = true
render_always = false
header = ""
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

Template context should include at least:

```text
project.name
release.version
release.title
release.status
release.date
release.previous_version
release.changelog_file
entries
groups
releases
```

Use templates only for rendering. Do not let templates mutate releaseledger state or read files.

## JSON mode protocol

When machine output is needed, `--json` is root-level:

```bash
releaseledger --json release show 1.2.0
releaseledger --json build 1.2.0 --dry-run
```

Do not append `--json` after the subcommand unless releaseledger explicitly adds that local option later.

## Exact enum vocabulary

Audit decisions:

```text
needs_review
accepted
grouped
internal
rejected
```

Audit public impact:

```text
public
docs
internal
none
unknown
```

Entry kinds:

```text
added
changed
fixed
removed
deprecated
security
docs
quality
internal
```

## Serialization rule

Never run a command and its verification concurrently. Wait for a successful
mutation before issuing `show`, `list`, `review`, `validate`, or file checks
that depend on it.

## Snapshot rule

Resolve `HEAD` once when attaching or explicitly refreshing a release range.
After that, omit `--head` and use the stored SHA snapshot. A new commit on the
branch belongs to the release only after an explicit refresh.

## Build-scope rule

An explicit version in "update the changelog for VERSION" means single-section
intent. Full-file rebuild requires explicit all-history wording such as
"rebuild the whole changelog" or "regenerate all release sections."

## CLI failure protocol

If a `releaseledger ...` command fails with a Python traceback:

1. Stop mutating release state.
2. Run exactly one read-only probe:
   `releaseledger --version`.
3. If startup still fails, report that the releaseledger CLI is broken and no mutation was recorded.
4. If startup succeeds, rerun the failed command once with the same arguments.
5. For repeated failure, inspect command help and use explicit options rather than guessing.

If `releaseledger_dir escapes the workspace root`, do not edit `.releaseledger.toml` manually.
Use `releaseledger config set releaseledger_dir PATH --external-dir` when the sibling state directory is intentional.
Or use `releaseledger config set releaseledger_dir .releaseledger` to reset to workspace-local.

## Public API protocol

Prefer CLI for agent work. If Python integration is required, import only public modules:

```python
from releaseledger.api.releases import create_release, update_release, show_release
from releaseledger.api.entries import (
    add_release_entry,
    add_many_release_entries,
    update_release_entry,
    lint_release_entries,
    build_entry_prompt,
)
from releaseledger.api.review import build_release_review
from releaseledger.api.config import load_project_locator, render_default_releaseledger_toml
```

Do not couple external code to internal storage paths or private service functions unless the user explicitly requests package development work.

## Release-boundary recovery protocol

Never use the newest local tag as a release boundary until shipment is confirmed. If a tagged release was never shipped, audit from the last actually shipped tag and keep the unshipped tag as a canceled tombstone.

Before finalization, run the read-only gate sequence:

```text
releaseledger release reconcile --strict
releaseledger release chain check --strict
releaseledger release show VERSION
releaseledger git range VERSION
releaseledger audit validate VERSION --phase complete --strict
releaseledger release check VERSION --strict
releaseledger build --all --dry-run --strict
```

`release reconcile` compares release records, Git tags, and changelog headings. It never writes historical state and reports `tag_without_release`, `changelog_without_release`, `planned_with_tag`, and related mismatches.

`source_refs` is the single coverage-owner surface for a commit or other coverable identity. Use `sources` for supporting provenance when several changelog bullets describe one commit. `entry add-many --dry-run --json` exposes `result.issues` without writing. Do not create a real entry to probe validation. Remove accidental draft or rejected probes with `entry delete VERSION ENTRY_ID --reason TEXT`; accepted entries require an explicit force flag.

Canceling a release with direct successors requires `--rewrite-successors` and either a valid prior predecessor or `--successor-previous VERSION`. Use `--dry-run` before mutation. A single-release build rejects canceled releases unless `--include-canceled` is explicitly requested for archival/debug output.
