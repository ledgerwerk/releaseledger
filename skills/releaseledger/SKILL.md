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

## Core agent command path

Use this path first for routine release work:

```text
releaseledger --version
releaseledger init
releaseledger release list
releaseledger release show VERSION
releaseledger release create VERSION
releaseledger release update VERSION
releaseledger release tag VERSION
releaseledger release finalize VERSION
releaseledger entry add VERSION --kind KIND --summary TEXT
releaseledger entry add-many VERSION --file FILE --dry-run
releaseledger entry show VERSION ENTRY_ID
releaseledger entry update VERSION ENTRY_ID
releaseledger entry import VERSION --file FILE
releaseledger entry list VERSION
releaseledger entry lint VERSION --strict
releaseledger entry prompt VERSION --source-ref REF --context-file FILE
releaseledger changelog VERSION --format markdown|json
releaseledger build VERSION --dry-run
releaseledger review VERSION [--strict] [--git] [--git-base REF] [--git-head REF] [--require-audit-sheet]
releaseledger git range VERSION [--base REF] [--head REF]
releaseledger git import VERSION --base REF [--head REF] --output PATH
releaseledger audit init VERSION [--base REF] [--head REF] [--overwrite]
releaseledger audit show VERSION [--format markdown|json] [--output PATH]
releaseledger audit update VERSION --file PATH
releaseledger audit validate VERSION [--strict] [--include-internal]
releaseledger audit sync VERSION
releaseledger branch status
releaseledger build [VERSION] [--all] [--target-file CHANGELOG.md]

releaseledger storage where
releaseledger config show
releaseledger config set releaseledger_dir PATH [--external-dir]
```

Root options belong before the subcommand:

```text
releaseledger --cwd PATH --json release show VERSION
```

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

1. Check shipped evidence first: git tags, existing changelog headings, or an
   explicit user statement.
2. If a stored release version was never shipped and the number was wrong, use
   `release rename`. Pass `--force-released-unshipped` if it is currently marked
   `released`, `--previous` to set the real predecessor, and
   `--rename-changelog-section --target-file CHANGELOG.md` to fix the heading.
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
releaseledger entry add-many VERSION --file /tmp/VERSION-entries.yaml --dry-run
releaseledger entry add-many VERSION --file /tmp/VERSION-entries.yaml
releaseledger entry lint VERSION --strict
releaseledger entry list VERSION
```

Batch creation validates every entry before writing any entry. If any item is
invalid, correct the YAML and rerun the dry run; do not add entries one at a
time to bypass atomic validation.

## Changelog source protocol

Use this when the user wants release-note source material for review or drafting.

```bash
releaseledger changelog VERSION --target-changelog CHANGELOG.md --release-date YYYY-MM-DD
releaseledger changelog VERSION --format json
releaseledger changelog VERSION --include-internal
releaseledger changelog VERSION --include-status accepted --include-status draft
releaseledger changelog VERSION --lint
```

Rules:

1. Treat `releaseledger changelog VERSION` as source/context unless the command name or option explicitly says build/update.
2. Check whether internal entries were filtered.
3. Preserve warnings, release metadata, and entry grouping when handing source to a human or another tool.
4. If no date is provided and the release has no persisted `released_at`, keep the output unreleased or explicitly say no date was available.

## Commit audit sheet protocol

Use this for any git-backed changelog or release-note backfill.

1. Attach or resolve the git range.
2. Run `releaseledger audit init VERSION --base BASE --head HEAD`.
3. Inspect every commit patch. Fill `inspected`, `inspected_paths`,
   `observed_behavior`, `decision`, and `target_entry_key`.
4. Never copy, paraphrase, title-case, or mechanically convert
   `evidence_subject` into `summary`.
5. Group commits only when the grouped entry still covers every `git:<sha>`
   source ref.
6. Mark housekeeping as `internal` and cover it with an internal accepted
   entry; do not invent public changelog prose.
7. Run `releaseledger audit validate VERSION --strict --include-internal`
   before `entry add-many` and again after `audit sync`.
8. Run
   `releaseledger review VERSION --git --strict --include-internal --require-audit-sheet`
   before building.

   `audit init` writes one `needs_review` row per git candidate commit.
   Decisions are `needs_review`, `accepted`, `grouped`, `internal`, and
   `rejected` (prefer `internal` for housekeeping). The sheet is evidence
   state, not changelog prose; the YAML file under
   `.releaseledger/.../audit/commit-audit.yaml` is canonical.

## CHANGELOG.md build protocol

Use this when the user asks to build, generate, or update `CHANGELOG.md`.

0. For a git-backed release, run a strict git coverage review immediately before any build:
   `releaseledger review VERSION --git --git-base PREV_TAG --git-head HEAD --strict`.
   If review reports missing `git:<sha>` coverage, uncovered expected refs, lint errors, or build blockers, stop and report the missing audit work. Do not build.
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
8. To rebuild the **whole** target file from ledger state, use the
   conventional full-build command:
   - `releaseledger build --dry-run --target-file CHANGELOG.md`
   - `releaseledger build --target-file CHANGELOG.md`
     `build` with no VERSION (or `build --all`) regenerates every selected
     release section newest-first, preserves the `## [Unreleased]` body by
     default, excludes internal entries and non-released releases by default,
     and is a whole-file rewrite (no `--replace-existing`). `build VERSION`
     keeps the single-section insert/replace behavior.

## Release review protocol

Use this to answer "what did I already add for this release?" before adding
new entries or building the changelog. `releaseledger review VERSION` is
read-only: it never writes `CHANGELOG.md` and never mutates release state.

```bash
releaseledger review VERSION
releaseledger --json review VERSION
releaseledger review VERSION --include-status accepted --include-status draft
releaseledger review VERSION --strict --target-file CHANGELOG.md
releaseledger review VERSION --git --strict
releaseledger review VERSION --git --git-base v1.1.0 --git-head HEAD --strict
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
5. `--strict` exits non-zero when the release is not OK. It mirrors `build --strict`, so it can fail on uncovered refs, lint errors, a missing release
   date in Keep a Changelog mode, or other build blockers. Review alone never
   writes the changelog.
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

# 2. Capture the range. JSON mode is preferred because it gives a machine list
#    of source refs that can be audited.
releaseledger --json git range VERSION --base PREV_TAG --head HEAD > /tmp/releaseledger-range.json

# 3. Create a per-commit evidence directory and inspect every candidate commit.
python - <<'PY'
import json
from pathlib import Path
payload = json.loads(Path("/tmp/releaseledger-range.json").read_text())
candidates = payload["result"]["candidates"]
Path("/tmp/releaseledger-shas.txt").write_text(
    "\n".join(c["sha"] for c in candidates) + "\n"
)
PY
while read -r sha; do
  git show --stat --patch --find-renames --find-copies --format=fuller "$sha"     > "/tmp/releaseledger-commit-${sha}.patch"
done < /tmp/releaseledger-shas.txt
```

Create an audit worksheet before writing entries:

```markdown
| sha   | paths inspected | public/API/docs behavior            | decision                     | target entry          |
| ----- | --------------- | ----------------------------------- | ---------------------------- | --------------------- |
| <sha> | <paths>         | <real effect from patch/tests/docs> | accept/internal/reject/group | <summary or entry id> |
```

Rules for the worksheet:

1. There must be one row per candidate commit from the range.
2. `paths inspected` must come from the patch/path evidence, not only from a
   commit subject.
3. `public/API/docs behavior` must describe the shipped behavior, compatibility
   impact, CLI/config/docs effect, or state that the commit is internal-only.
4. `decision` must be one of `accept`, `group`, `internal`, or `reject`.
5. Version-only commits may be grouped or rejected only after checking the
   actual patch and confirming no independent user-facing behavior changed.

Then create and curate the entry batch:

```bash
# 4. Create a coverage scaffold. For a non-empty range this is mandatory.
releaseledger git import VERSION --base PREV_TAG --head HEAD --status draft --output entries.yaml

# 5. Rewrite entries.yaml manually from the worksheet:
#    - fill every summary from reviewed behavior, API/docs impact, tests, paths,
#      and patch evidence
#    - preserve every git:<sha> source_ref
#    - combine related commits only by moving all relevant git:<sha> source_refs
#      onto the combined entry
#    - mark implementation-only work with internal: true or status: rejected
#    - never use commit subjects as summaries

# 6. Validate atomically. Do not replace this with many parallel entry add calls.
releaseledger entry add-many VERSION --file entries.yaml --dry-run
releaseledger entry add-many VERSION --file entries.yaml

# 7. Review git coverage. This is the gate before changelog build.
releaseledger review VERSION --git --git-base PREV_TAG --git-head HEAD --strict

# 8. Build only after strict review passes.
releaseledger build VERSION --release-date YYYY-MM-DD --strict --target-file CHANGELOG.md
```

Commit-message guard before `entry add-many`:

```bash
# Inspect commit subjects only to detect accidental leakage. Do not use them as prose.
git log --format=%s PREV_TAG..HEAD > /tmp/releaseledger-commit-subjects.txt
python - <<'PY'
from pathlib import Path
import yaml
subjects = {line.strip().lower() for line in Path("/tmp/releaseledger-commit-subjects.txt").read_text().splitlines() if line.strip()}
batch = yaml.safe_load(Path("entries.yaml").read_text())
for i, entry in enumerate(batch.get("entries", []), 1):
    summary = str(entry.get("summary", "")).strip().lower()
    if not summary:
        raise SystemExit(f"entry {i}: summary is blank")
    if summary in subjects:
        raise SystemExit(f"entry {i}: summary matches a commit subject: {summary!r}")
PY
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
