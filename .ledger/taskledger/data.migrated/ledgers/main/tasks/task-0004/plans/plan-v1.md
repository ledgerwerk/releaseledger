---
schema_version: 1
object_type: plan
file_version: v2
task_id: task-0004
plan_id: plan-v1
version: 1
plan_version: 1
status: accepted
created_at: '2026-08-21T11:20:56Z'
created_by:
  actor_type: agent
  actor_name: nahrstaedt
  tool: null
  session_id: null
  host: wsl
  pid: 4006361
  actor_id: null
  role: null
  harness_id: null
  command_pid: null
  pid_scope: null
supersedes: null
question_refs: []
criteria:
- id: ac-0001
  text: Release identity treats a leading v on SemVer-like versions as equivalent
    without changing exact stored paths, and unique selectors resolve while ambiguous
    aliases fail explicitly.
  mandatory: true
- id: ac-0002
  text: Git tag import and reconciliation use identity groups, detect canceled releases
    with matching tags, detect multiple active aliases, and assign external evidence
    to an unambiguous active owner rather than a canceled alias tombstone.
  mandatory: true
- id: ac-0003
  text: Finalize is a compatible idempotent no-op for an already released record,
    while conflicting retry metadata fails without mutation, and generic status updates
    cannot bypass terminal lifecycle operations.
  mandatory: true
- id: ac-0004
  text: The supported restore lifecycle can reopen a canceled release or restore it
    from a matching Git tag with recorded release.restored evidence, cleared cancellation
    metadata, and correct tag date and SHA metadata.
  mandatory: true
- id: ac-0005
  text: Release rename can replace only a canceled target through an explicit reasoned
    dry-run and apply path, preserving the source entries and audit bundle, rewriting
    entry ownership, updating successors, rebuilding indexes, and rolling back source
    and target bundles on injected failure.
  mandatory: true
- id: ac-0006
  text: The Lexhint-shaped end-to-end fixture is repairable through supported CLI
    commands and ends with one released 0.1.0 bundle containing the 11 desired entries,
    released 0.1.1 pointing to it, no stale Unreleased content after rebuild, and
    strict reconciliation and chain checks passing.
  mandatory: true
- id: ac-0007
  text: CLI inventory, public API exports, changelog identity comparisons, generated
    command documentation, concepts, quickstart guidance, and the releaseledger skill
    document the new restore, alias, replacement, and retry-safe lifecycle behavior.
  mandatory: true
- id: ac-0008
  text: The optional entry move operation is available as an explicit, audited, dry-runnable
    and rollback-safe command that preserves or deterministically renumbers entry
    IDs and handles audit and source-reference ownership safely.
  mandatory: true
todos:
- id: plan-todo-0001
  text: Add release_identity_key and shared identity grouping and selector resolution
    primitives in @releaseledger/domain/release.py and @releaseledger/services/releases.py,
    with SemVer prerelease/build and custom-version coverage.
  done: false
  created_at: '2026-08-21T11:20:56Z'
  updated_at: '2026-08-21T11:20:56Z'
  source: plan
  mandatory: true
  status: open
  active_at: null
  blocked_reason: null
  done_at: null
  skipped_at: null
  completed_by: null
  completed_in_harness: null
  skipped_by: null
  evidence: []
  artifact_refs: []
  change_refs: []
  command_refs: []
  source_plan_id: null
  source_question_ids: []
  validation_hint: Run the focused identity and selector tests in tests/test_release_reconcile.py
    and tests/test_release_corrections.py.
- id: plan-todo-0002
  text: Refactor @releaseledger/services/releases.py tag loading, import-tags, reconciliation,
    predecessor comparisons, and relevant changelog matching to use identity groups
    and emit canceled_with_tag, ambiguous_active_release_identity, and related diagnostics
    without misassigning canceled aliases.
  done: false
  created_at: '2026-08-21T11:20:56Z'
  updated_at: '2026-08-21T11:20:56Z'
  source: plan
  mandatory: true
  status: open
  active_at: null
  blocked_reason: null
  done_at: null
  skipped_at: null
  completed_by: null
  completed_in_harness: null
  skipped_by: null
  evidence: []
  artifact_refs: []
  change_refs: []
  command_refs: []
  source_plan_id: null
  source_question_ids: []
  validation_hint: Run pytest -q tests/test_release_reconcile.py tests/test_version_correction_workflow.py.
- id: plan-todo-0003
  text: Implement retry-safe finalize and terminal-state enforcement in the public
    update path, add release.restored in @releaseledger/domain/event.py, and implement
    restore service and API behavior for reopen and matching Git-tag correction modes.
  done: false
  created_at: '2026-08-21T11:20:56Z'
  updated_at: '2026-08-21T11:20:56Z'
  source: plan
  mandatory: true
  status: open
  active_at: null
  blocked_reason: null
  done_at: null
  skipped_at: null
  completed_by: null
  completed_in_harness: null
  skipped_by: null
  evidence: []
  artifact_refs: []
  change_refs: []
  command_refs: []
  source_plan_id: null
  source_question_ids: []
  validation_hint: Run lifecycle, finalize idempotence, restore, and terminal update
    tests in tests/test_release_corrections.py, tests/test_cli.py, and tests/test_version_correction_workflow.py.
- id: plan-todo-0004
  text: Add rollback-safe canceled-target bundle replacement to @releaseledger/storage/store.py
    and expose --replace-canceled-target, --reason, and dry-run behavior through rename,
    preserving entries and audits while updating successors and indexes.
  done: false
  created_at: '2026-08-21T11:20:56Z'
  updated_at: '2026-08-21T11:20:56Z'
  source: plan
  mandatory: true
  status: open
  active_at: null
  blocked_reason: null
  done_at: null
  skipped_at: null
  completed_by: null
  completed_in_harness: null
  skipped_by: null
  evidence: []
  artifact_refs: []
  change_refs: []
  command_refs: []
  source_plan_id: null
  source_question_ids: []
  validation_hint: Run replacement dry-run, apply, non-canceled target, injected rollback,
    and Lexhint-shaped regression tests.
- id: plan-todo-0005
  text: Implement the explicit entry move command and service path with preflight
    collision checks, optional deterministic renumbering, audit target handling, source-reference
    ownership checks, revision updates, events, and rollback guarantees.
  done: false
  created_at: '2026-08-21T11:20:56Z'
  updated_at: '2026-08-21T11:20:56Z'
  source: plan
  mandatory: true
  status: open
  active_at: null
  blocked_reason: null
  done_at: null
  skipped_at: null
  completed_by: null
  completed_in_harness: null
  skipped_by: null
  evidence: []
  artifact_refs: []
  change_refs: []
  command_refs: []
  source_plan_id: null
  source_question_ids: []
  validation_hint: Run the entry move focused tests, including collision, audit, source-reference,
    dry-run, and rollback cases.
- id: plan-todo-0006
  text: Wire restore and extended rename options through @releaseledger/cli.py, @releaseledger/api/releases.py,
    and @releaseledger/command_registry.py, including correct mutating lock and human/JSON
    output behavior.
  done: false
  created_at: '2026-08-21T11:20:56Z'
  updated_at: '2026-08-21T11:20:56Z'
  source: plan
  mandatory: true
  status: open
  active_at: null
  blocked_reason: null
  done_at: null
  skipped_at: null
  completed_by: null
  completed_in_harness: null
  skipped_by: null
  evidence: []
  artifact_refs: []
  change_refs: []
  command_refs: []
  source_plan_id: null
  source_question_ids: []
  validation_hint: Run pytest -q tests/test_cli.py tests/test_cli_inventory.py and
    inspect the generated command inventory.
- id: plan-todo-0007
  text: Audit @releaseledger/services/changelog_build.py for identity comparisons,
    update tests and documentation files, regenerate command references, and add the
    Lexhint end-to-end recovery regression.
  done: false
  created_at: '2026-08-21T11:20:56Z'
  updated_at: '2026-08-21T11:20:56Z'
  source: plan
  mandatory: true
  status: open
  active_at: null
  blocked_reason: null
  done_at: null
  skipped_at: null
  completed_by: null
  completed_in_harness: null
  skipped_by: null
  evidence: []
  artifact_refs: []
  change_refs: []
  command_refs: []
  source_plan_id: null
  source_question_ids: []
  validation_hint: Run both planned pytest commands and inspect generated documentation
    and the strict Lexhint-shaped recovery assertions.
- id: plan-todo-0008
  text: Run the complete focused validation suite, reconcile implementation changes
    against the approved plan, and record all implementation change and test evidence
    before finishing the implementation run.
  done: false
  created_at: '2026-08-21T11:20:56Z'
  updated_at: '2026-08-21T11:20:56Z'
  source: plan
  mandatory: true
  status: open
  active_at: null
  blocked_reason: null
  done_at: null
  skipped_at: null
  completed_by: null
  completed_in_harness: null
  skipped_by: null
  evidence: []
  artifact_refs: []
  change_refs: []
  command_refs: []
  source_plan_id: null
  source_question_ids: []
  validation_hint: Run both plan test commands successfully and inspect git diff and
    taskledger todo status.
generation_reason: initial
based_on_question_ids: []
based_on_answer_hash: null
supersedes_plan_id: null
approved_at: '2026-08-21T11:27:38Z'
approved_by:
  actor_type: user
  actor_name: nahrstaedt
  tool: manual
  session_id: null
  host: null
  pid: null
  actor_id: null
  role: null
  harness_id: null
  command_pid: null
  pid_scope: null
approval_note: 'User approved in harness: approve.'
approval_source: explicit_chat
approved_plan_hash: 74ca671855221414e5a3724b50e8919c179495f1c1e2cc0f52ec54ce4e581830
goal: Make Releaseledger safely repair canceled and aliased releases, preserve release
  identity across Git and changelog surfaces, and support the Lexhint recovery workflow.
files:
- '@releaseledger/domain/release.py'
- '@releaseledger/domain/event.py'
- '@releaseledger/services/releases.py'
- '@releaseledger/storage/store.py'
- '@releaseledger/cli.py'
- '@releaseledger/api/releases.py'
- '@releaseledger/command_registry.py'
- '@releaseledger/services/changelog_build.py'
- '@tests/test_release_reconcile.py'
- '@tests/test_release_corrections.py'
- '@tests/test_version_correction_workflow.py'
- '@skills/releaseledger/SKILL.md'
- '@docs/concepts.md'
- '@docs/commands.md'
- '@docs/quickstart.md'
- '@docs/commands.generated.md'
test_commands:
- pytest -q tests/test_release_reconcile.py tests/test_release_corrections.py tests/test_version_correction_workflow.py
- pytest -q tests/test_cli.py tests/test_releaseledger_skill_protocol.py
expected_outputs:
- All focused release correction, reconciliation, workflow, CLI, and skill protocol
  tests pass.
todos_waived_reason: null
---
# Canceled release recovery and identity correction
 
## Summary

This plan repairs the coupled Releaseledger defects exposed by the Lexhint changelog recovery. It introduces one semantic identity model for stored releases, Git tags, changelog headings, selectors, and tag import, then adds explicit lifecycle operations for retry-safe finalization and canceled-release restoration. It also adds an atomic canceled-target bundle replacement path and the requested entry move operation so corrections preserve release content, audit evidence, indexes, and rollback guarantees.
 
## Implementation Changes

- Add identity normalization and alias-aware resolution while preserving raw storage versions and concrete predecessor references.
- Make tag import and reconciliation identity-aware, including canceled tag contradictions, active alias conflicts, and canceled tombstone ownership rules.
- Enforce lifecycle transitions in the service API, make compatible finalize retries no-ops, and add restore modes for reopening canceled releases and restoring from matching Git tags.
- Add an explicit release.restored event and a rollback-safe storage primitive for replacing an obsolete canceled target with a source release bundle.
- Extend rename and add entry move with dry-run previews, reason requirements, collision and audit checks, revision updates, successor handling, and index rebuilds.
- Wire the CLI, public API, command registry, changelog matching, generated command reference, skill guidance, and user documentation to the new supported workflows.
 
## Tests

- Add unit and integration coverage for identity keys, alias selectors, import-tags, reconciliation groups, finalize retries, terminal update guards, restore modes, and event data.
- Add replacement and entry move tests for dry-run immutability, successful ownership transfer, collision rejection, injected-failure rollback, audit preservation, source-reference validation, and index correctness.
- Add a Lexhint-shaped end-to-end fixture that performs the supported rename, restore, chain repair, changelog rebuild, strict reconcile, and strict chain checks.
- Run `pytest -q tests/test_release_reconcile.py tests/test_release_corrections.py tests/test_version_correction_workflow.py` and `pytest -q tests/test_cli.py tests/test_releaseledger_skill_protocol.py`.
 
## Assumptions

- Existing schema version and exact release directory naming remain backward compatible; identity normalization is comparison and selection behavior, not a storage migration.
- Direct Git tag deletion or rewriting remains outside Releaseledger. Matching tags are diagnosed or consumed by restore, never removed automatically.
- Changelog content is generated only after ledger ownership is corrected; this task does not manually edit a historical changelog to hide inconsistent release state.
- The repository's existing command-generation workflow is used to refresh generated documentation rather than hand-maintaining divergent command inventories.
 
## Out of Scope

- Automatic deletion or rewriting of Git tags.
- General Ledgercore transaction abstractions unrelated to release bundle replacement.
- Inventing missing historical source references or changelog prose.

## Plan input checklist before upsert

- [x] I ran `taskledger plan check --file plan.md`.
- [x] Every acceptance criterion uses `text`, not `description`.
- [x] Todo mappings use supported keys only: `id`, `id_hint`, `text`, `mandatory`, `validation_hint`, `worker_step`.
- [x] File references are plan-level `files:` entries or are mentioned in todo text/body; todo-level `files:` is not captured.
- [x] The Markdown body explains enough context for implementation handoff.
