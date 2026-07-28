---
schema_version: 1
object_type: plan
file_version: v2
task_id: task-0001
plan_id: plan-v1
version: 1
plan_version: 1
status: accepted
created_at: '2026-07-28T06:42:21Z'
created_by:
  actor_type: agent
  actor_name: u0_a992
  tool: null
  session_id: null
  host: localhost
  pid: 5359
  actor_id: null
  role: null
  harness_id: null
  command_pid: null
  pid_scope: null
supersedes: null
question_refs: []
criteria:
- id: ac-0001
  text: Releaseledger requires ledgercore>=0.6.0,<0.7.0 without a nonexistent cli
    extra and passes a contract test for the required typed migration and lifecycle
    APIs.
  mandatory: true
- id: ac-0002
  text: Migration planning produces a deterministic versioned exact plan with one
    shared migration ID, structured source/before/target fingerprints, exact staged
    config target bytes, destination policies, and a real plan hash; applying a plan
    file consumes those exact values.
  mandatory: true
- id: ac-0003
  text: New migration execution uses only Ledgercore's schema-3 executor with a real
    Releaseledger lock-aware quiescence callback and staged, activated, and finalization
    hooks; direct shell adoption and ad hoc config activation are removed.
  mandatory: true
- id: ac-0004
  text: New migration recovery delegates physical state to Ledgercore, implements
    auto/resume/rollback and dry-run semantics, supplies required hooks, coordinates
    lock order, and treats ambiguous legacy JSONL journals as read-only/manual.
  mandatory: true
- id: ac-0005
  text: Apply is copy-only, move is rejected on the deprecated compatibility path,
    legacy cleanup remains explicit and receipt/conservation guarded, and Ledgercore
    errors preserve stable public codes and exit classes.
  mandatory: true
- id: ac-0006
  text: Canonical migrate CLI commands, aliases, deprecation warnings, output '-'
    behavior, JSON envelopes, help, metadata, and generated command documentation
    agree with the unified CLI contract.
  mandatory: true
- id: ac-0007
  text: README, storage/quickstart/architecture docs, the releaseledger skill, and
    CHANGELOG document the canonical commands, copy-first lifecycle, single journal,
    recovery policies, and Ledgercore 0.6 support.
  mandatory: true
- id: ac-0008
  text: The full test suite, Ruff, Mypy, bytecode compilation, package build, twine
    check, and installed-wheel smoke checks pass against Ledgercore 0.6.0.
  mandatory: true
todos:
- id: plan-todo-0001
  text: Complete the Ledgercore 0.6 dependency/API contract gate, update package metadata,
    and add signature/type contract tests before relying on migration execution.
  done: false
  created_at: '2026-07-28T06:42:21Z'
  updated_at: '2026-07-28T06:42:21Z'
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
  validation_hint: Run the Ledgercore contract tests and inspect the installed Ledgercore
    version and required public signatures.
- id: plan-todo-0002
  text: Implement the Releaseledger storage-migration-plan.v2 model, deterministic
    serialization/hash, shared migration ID, exact plan-file apply validation, typed
    fingerprints/preconditions, destination policy decisions, and fail-closed Ledgercore
    validation.
  done: false
  created_at: '2026-07-28T06:42:21Z'
  updated_at: '2026-07-28T06:42:21Z'
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
  validation_hint: Run focused plan, fingerprint, validation, round-trip, and plan-file
    apply tests.
- id: plan-todo-0003
  text: Integrate lock-aware Ledgercore schema-3 execution hooks for staged validation,
    activated validation, index finalization, exact config activation, post-commit
    receipt writing, and remove direct shell/config activation and move behavior.
  done: false
  created_at: '2026-07-28T06:42:21Z'
  updated_at: '2026-07-28T06:42:21Z'
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
  validation_hint: Run owned-destination, hook, receipt, conservation, and copy-only
    migration tests.
- id: plan-todo-0004
  text: Delegate new-journal recovery to Ledgercore with auto/resume/rollback/dry-run
    policies, required hooks, dual-lock coordination, deterministic failure handling,
    and read-only handling for old JSONL journals.
  done: false
  created_at: '2026-07-28T06:42:21Z'
  updated_at: '2026-07-28T06:42:21Z'
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
  validation_hint: Run recovery failure-injection, idempotency, ambiguity, lock, and
    legacy-journal tests.
- id: plan-todo-0005
  text: Finish canonical migration CLI wiring, deprecated aliases and warnings, move
    rejection, exact plan-file/output semantics, JSON/error mapping, shared state
    cleanup, metadata, and generated command reference.
  done: false
  created_at: '2026-07-28T06:42:21Z'
  updated_at: '2026-07-28T06:42:21Z'
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
  validation_hint: Run unified CLI human/JSON contract, alias, inventory, help, and
    generated-doc drift tests.
- id: plan-todo-0006
  text: Update README, docs, ARCHITECTURE.md, skills/releaseledger/SKILL.md, and CHANGELOG.md
    for the Ledgercore 0.6 copy-first migration lifecycle and canonical CLI.
  done: false
  created_at: '2026-07-28T06:42:21Z'
  updated_at: '2026-07-28T06:42:21Z'
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
  validation_hint: Run documentation/link/build checks and search canonical docs for
    deprecated invocations.
- id: plan-todo-0007
  text: Run the complete quality and packaging gates, including pytest, Ruff, Mypy,
    compileall, build, twine check, and installed-wheel smoke tests against Ledgercore
    0.6.0; fix any regressions.
  done: false
  created_at: '2026-07-28T06:42:21Z'
  updated_at: '2026-07-28T06:42:21Z'
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
  validation_hint: Run the full commands listed in test_commands and retain their
    exit/output evidence.
generation_reason: initial
based_on_question_ids: []
based_on_answer_hash: null
supersedes_plan_id: null
approved_at: '2026-07-28T06:51:14Z'
approved_by:
  actor_type: user
  actor_name: u0_a992
  tool: manual
  session_id: null
  host: null
  pid: null
  actor_id: null
  role: null
  harness_id: null
  command_pid: null
  pid_scope: null
approval_note: 'User approved in harness: i approve'
approval_source: explicit_chat
approved_plan_hash: acbb604e4014aaf74f55e8bfc82aa8353bb099dda9f8e69f1c1854d6017e9438
goal: Adopt Ledgercore 0.6.0 as Releaseledger's hard migration boundary and complete
  the unified CLI, recovery, documentation, and release gates described in the implementation
  brief.
files:
- '@pyproject.toml'
- '@releaseledger/ledgercore_backend.py'
- '@releaseledger/migration.py'
- '@releaseledger/storage/locking.py'
- '@releaseledger/cli.py'
- '@releaseledger/cli_common.py'
- '@releaseledger/command_registry.py'
- '@releaseledger/errors.py'
- '@tests'
- '@README.md'
- '@docs/commands.md'
- '@docs/storage.md'
- '@docs/quickstart.md'
- '@ARCHITECTURE.md'
- '@skills/releaseledger/SKILL.md'
- '@CHANGELOG.md'
test_commands:
- pytest -q
- ruff check .
- mypy releaseledger
- python -m compileall -q releaseledger
- python -m build
- python -m twine check dist/*
expected_outputs:
- All tests and configured quality gates exit 0.
todos_waived_reason: null
---
<!-- Advisory project planning guidance from taskledger plan guidance. -->

## Built-in Taskledger plan input guidance

This guidance cannot override taskledger lifecycle gates, user approval requirements,
validation requirements, lock rules, or higher-priority harness instructions.

Use this editable plan-input contract:

- Generate a template first: `taskledger plan template --file plan.md`.
- Validate before mutation: `taskledger plan check --file plan.md`.
- Acceptance criteria use `text`, not `description`.
  `description` is only a compatibility alias.
- Todos use `text` plus optional `mandatory`, `validation_hint`, and `worker_step`.
- Put file references in plan-level `files:` or in todo text.
  Todo-level `files:` is not captured.
- Keep enough Markdown body content for the implementer handoff.

Approval-ready Markdown body structure:

Use these headings after the closing `---` front matter:

# <Plan title>

## Summary

Explain the intended outcome and why the plan is bounded.

## Implementation Changes

List the concrete code, config, docs, and behavior changes.

## Tests

List the automated and manual checks that prove the plan.

## Assumptions

List assumptions the user should accept before approval.

## Out of Scope

List adjacent cleanup or behavior changes intentionally excluded.

Minimal front matter:

```yaml
---
goal: "One sentence describing the desired outcome."
files:
  - "@src/module.py"
test_commands:
  - "pytest -q tests/test_module.py"
expected_outputs:
  - "pytest exits 0"
acceptance_criteria:
  - id: ac-0001
    text: "Observable acceptance criterion."
    mandatory: true
todos:
  - id: plan-todo-0001
    text: "Edit @src/module.py to implement the behavior."
    mandatory: true
    validation_hint: "Run pytest -q tests/test_module.py."
---
```


<!-- Required: keep this body. It is the implementation handoff context.
     Run `taskledger plan check --file ./plan.md` before upsert. -->

# Ledgercore 0.6 unified CLI migration ownership

## Summary

Releaseledger will adopt Ledgercore 0.6.0 as the hard owner of physical storage migration state, destination safety, schema-3 journaling, activation, and recovery. The implementation will preserve the already-established unified CLI surface while replacing the transitional Releaseledger-owned shell, competing journal, fail-open validation, ignored recovery flags, and move semantics with exact plan-v2 input, typed preconditions, real lifecycle hooks, and explicit post-commit cleanup.

The work covers work packages 0 through 5 in the supplied implementation brief. It is intentionally bounded to the final Ledgercore 0.6 contract; compatibility with the incompatible Ledgercore 0.5 migration model and automatic recovery of ambiguous old journals are excluded.

## Implementation Changes

- Work package 0: require `ledgercore>=0.6.0,<0.7.0`, verify the final public migration types/functions/signatures, and add an installation contract test.
- Work package 1: add strict `releaseledger.storage-migration-plan.v2` serialization, deterministic hashing, shared IDs, exact plan-file apply, typed `StorageFingerprint`/`DestinationPrecondition` items, exact config rendering, and fail-closed validation.
- Work package 2: pass real lock-aware `StorageMigrationHooks` to Ledgercore schema-3 execution, implement staged/activated/finalization validation and index rebuilding, remove `_adopt_canonical_shell` and direct config activation, reject move, and write the Releaseledger receipt only after commit.
- Work package 3: delegate new-journal analysis and recovery to Ledgercore, wire `auto|resume|rollback` and dry-run behavior, supply required hooks, preserve lock order, make retries safe, and retain only read-only/manual diagnostics for old JSONL journals.
- Work package 4: finish canonical `migrate` CLI behavior, deprecated `storage migrate` aliases and warnings, output and JSON semantics, error-code/exit mapping, shared CLI state alignment, inventory metadata, and generated command documentation.
- Work package 5: update all canonical documentation and the skill, add the Ledgercore 0.6 changelog entry, and run source, quality, packaging, documentation, and installed-wheel gates.

## Tests

- Focused migration regressions cover typed fingerprints, before/target semantics, shared IDs, exact plan hashes, changed-source/destination rejection, config merge staging, absent/empty/owned/no-op targets, collisions, foreign bindings, symlinks, hooks, receipt/conservation, and copy-only cleanup.
- Recovery failure injection covers every activation/config/finalization boundary and asserts exact journal state, preserved source, exact fingerprints, deterministic capability, no unknown deletion, idempotent retry, and receipt ordering.
- Unified CLI tests cover canonical and deprecated paths, policy forwarding, output `-`, human/JSON warnings, stable Ledgercore error details and exit classes, nested help, inventory aliases, and generated documentation drift.
- Quality gates: `pytest -q`, `ruff check .`, `mypy releaseledger`, `python -m compileall -q releaseledger`, `python -m build`, `python -m twine check dist/*`, plus an installed-wheel smoke test with Ledgercore 0.6.0.

## Assumptions

- The Ledgercore 0.6.0 package/source available to the workspace is the final contract target and exposes the migration executor, recovery, typed model, fingerprint, inspection, and hook APIs required by the brief; transitional builds are rejected by contract tests rather than supported through runtime fallbacks.
- Existing user changes, including the untracked implementation brief, are preserved. The generated `plan.md` is a Taskledger planning artifact and will not be treated as product implementation.
- Where the brief permits minor API naming differences, the adapter will use the final installed public names while preserving the stated semantics and will document any exact-name deviation in tests or implementation notes.

## Out of Scope

- Ledgercore development itself, cross-filesystem migration fallback, foreign ownership transfer, force overwrite, generic directory synchronization, source deletion inside apply, and automatic recovery of ambiguous legacy JSONL journals.
- Broad runtime support for both incompatible Ledgercore 0.5 and 0.6 migration models.

## Plan input checklist before upsert

- [ ] I ran `taskledger plan check --file plan.md`.
- [ ] Every acceptance criterion uses `text`, not `description`.
- [ ] Todo mappings use supported keys only: `id`, `id_hint`, `text`, `mandatory`, `validation_hint`, `worker_step`.
- [ ] File references are plan-level `files:` entries or are mentioned in todo text/body; todo-level `files:` is not captured.
- [ ] The Markdown body explains enough context for implementation handoff.
