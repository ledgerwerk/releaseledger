---
schema_version: 1
object_type: plan
file_version: v2
task_id: task-0003
plan_id: plan-v1
version: 1
plan_version: 1
status: superseded
created_at: "2026-08-02T06:51:16Z"
created_by:
  actor_type: agent
  actor_name: u0_a992
  tool: null
  session_id: null
  host: localhost
  pid: 13746
  actor_id: null
  role: null
  harness_id: null
  command_pid: null
  pid_scope: null
supersedes: null
question_refs: []
criteria:
  - id: ac-0001
    text:
      A planned release can be renamed without a force flag, with dry-run preview,
      preserved title/status, successor and audit movement, and explicit changelog detection
      or correction.
    mandatory: true
  - id: ac-0002
    text:
      Release check renders every gate that contributes to its final result, exposes
      stable failed_checks and actionable next_actions in JSON, and never presents an
      unexplained Result FAIL.
    mandatory: true
  - id: ac-0003
    text:
      Release coverage honors complete internal and rejected audit decisions without
      requiring unrelated public-entry git refs, while accepted/grouped and unresolved
      commits retain correct blocking behavior.
    mandatory: true
  - id: ac-0004
    text:
      Entry source refs support explicit add, remove, clear, and replacement modes
      with conflict validation, canonical ordering, idempotence, preserved existing
      refs, and operation-aware output.
    mandatory: true
  - id: ac-0005
    text:
      Audit decisions can be generated as a directly consumable worksheet, and audit
      apply dry-run reports all evidence deficiencies that strict evidence validation
      would report.
    mandatory: true
  - id: ac-0006
    text:
      Release checks support current, finalize, and published phases with phase-appropriate
      tag, date, changelog, chain, reconciliation, audit, and coverage requirements.
    mandatory: true
  - id: ac-0007
    text:
      The decoded-run end-to-end regression completes the 0.3.0 to 0.2.8 correction
      without manual changelog edits, provenance loss, or fake public refs, and publication
      passes after tagging.
    mandatory: true
  - id: ac-0008
    text:
      Skill guidance, command documentation, concepts, quickstart, generated CLI
      reference, and examples describe the implemented safe correction workflow and
      actual options.
    mandatory: true
  - id: ac-0009
    text:
      Focused tests, lint, type checks, and the full test suite pass with development
      dependencies installed.
    mandatory: true
todos:
  - id: todo-0001
    text:
      Add the failing version-correction end-to-end regression and focused release-check
      renderer characterization tests described in the brief, without production fixes.
    done: false
    created_at: "2026-08-02T06:51:16Z"
    updated_at: "2026-08-02T06:51:16Z"
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
    validation_hint:
      Run the new focused regression and renderer tests; they should
      demonstrate the documented failures before fixes.
  - id: todo-0002
    text:
      Patch release review/check result modeling and human/JSON rendering so snapshot,
      audit, coverage, lint, state, changelog, audit completion, chain, and reconciliation
      gates plus reasons and next actions are all visible.
    done: false
    created_at: "2026-08-02T06:51:16Z"
    updated_at: "2026-08-02T06:51:16Z"
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
    validation_hint:
      Run release-check renderer, review, CLI, and focused regression
      tests and compare human and JSON failed gates.
  - id: todo-0003
    text:
      Align git coverage with validated audit decisions, retaining raw coverage
      diagnostics while implementing public-entry, internal-entry, internal-audit, rejected-audit,
      and unresolved gate semantics.
    done: false
    created_at: "2026-08-02T06:51:16Z"
    updated_at: "2026-08-02T06:51:16Z"
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
    validation_hint:
      Run commit-audit, review, CLI, and coverage-focused tests for internal,
      rejected, accepted, grouped, and needs-review commits.
  - id: todo-0004
    text:
      Implement service-layer and CLI source-ref mutation modes for add, remove,
      clear, and explicit replacement, including conflicts, canonical ordering, idempotence,
      reason forwarding, and operation-aware output.
    done: false
    created_at: "2026-08-02T06:51:16Z"
    updated_at: "2026-08-02T06:51:16Z"
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
    validation_hint:
      Run entry and CLI tests covering preservation, replacement, removal,
      clear, conflicts, dry-run parity, human output, and JSON output.
  - id: todo-0005
    text:
      Make release rename changelog-aware with read-only section planning, warnings
      and exact remediation, explicit section mutation, dry-run output, destination
      conflict preflight, title/status/date preservation, and safe write behavior.
    done: false
    created_at: "2026-08-02T06:51:16Z"
    updated_at: "2026-08-02T06:51:16Z"
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
    validation_hint:
      Run release correction and end-to-end tests for stale headings,
      explicit rename, conflicts, dry-run zero writes, and preserved metadata.
  - id: todo-0006
    text:
      Add audit decisions worksheet generation and complete-row early validation
      so generated templates round-trip and apply dry-run reports all missing evidence
      fields.
    done: false
    created_at: "2026-08-02T06:51:16Z"
    updated_at: "2026-08-02T06:51:16Z"
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
    validation_hint:
      Run audit worksheet and CLI tests, then validate a curated worksheet
      through apply and evidence validation.
  - id: todo-0007
    text:
      Add phase-aware release checks for current, finalize, and published workflows,
      with documented severity and explicit pre-tag versus post-tag behavior, and update
      prepare/next-action orchestration guidance where required.
    done: false
    created_at: "2026-08-02T06:51:16Z"
    updated_at: "2026-08-02T06:51:16Z"
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
    validation_hint:
      Run phase-aware release-check tests and the focused end-to-end
      workflow through finalize, pre-tag published failure, tag, and published success.
  - id: todo-0008
    text:
      Update the releaseledger skill, command/concept/quickstart documentation,
      generated CLI reference, and migration notes to match the eight-commit safe correction
      workflow and actual command surface.
    done: false
    created_at: "2026-08-02T06:51:16Z"
    updated_at: "2026-08-02T06:51:16Z"
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
    validation_hint:
      Regenerate the CLI reference, inspect documentation examples against
      help output, and run docs-related checks plus the full suite.
generation_reason: initial
based_on_question_ids: []
based_on_answer_hash: null
supersedes_plan_id: null
approved_at: null
approved_by: null
approval_note: null
approval_source: null
approved_plan_hash: null
goal:
  Implement the eight-commit release-version correction workflow fix so rename,
  audit coverage, source provenance, changelog handling, phased checks, and documentation
  agree safely.
files:
  - "@releaseledger/cli.py"
  - "@releaseledger/services/releases.py"
  - "@releaseledger/services/review.py"
  - "@releaseledger/services/audit.py"
  - "@releaseledger/services/entries.py"
  - "@releaseledger/services/changelog_build.py"
  - "@tests/test_release_corrections.py"
  - "@tests/test_commit_audit.py"
  - "@tests/test_commit_audit_cli.py"
  - "@tests/test_cli.py"
  - "@tests/test_version_correction_workflow.py"
  - "@skills/releaseledger/SKILL.md"
  - "@docs/commands.md"
  - "@docs/quickstart.md"
  - "@docs/concepts.md"
test_commands:
  - pytest -q tests/test_release_corrections.py tests/test_commit_audit.py tests/test_commit_audit_cli.py
    tests/test_cli.py tests/test_version_correction_workflow.py
  - ruff check .
  - mypy releaseledger
  - pytest -q
expected_outputs:
  - Focused pytest suite exits 0
  - ruff exits 0
  - mypy exits 0
  - Full pytest suite exits 0
todos_waived_reason: null
---

# Releaseledger version correction workflow

## Summary

Implement the eight commits in `releaseledger_version_modification_fix_brief.md` as one coherent, reviewable change. The work makes release checks explain all blockers, makes audit accounting authoritative for git coverage, adds non-destructive source-ref mutation, makes release rename changelog-aware, supplies valid audit worksheets, introduces phase-aware readiness checks, and updates the agent-facing documentation. The implementation will preserve existing JSON fields and event/revision conventions while adding compatible fields and explicit commands.

## Implementation Changes

1. Capture the decoded-run regressions in tests: the 0.3.0 to 0.2.8 correction, hidden chain/reconciliation blockers, internal audit coverage, and source-ref preservation.
2. Expose all release-check gates and normalized reasons in human output, stable `failed_checks`, and actionable `next_actions` JSON.
3. Load complete audit evidence before final coverage gating and distinguish public entry coverage from internal/rejected audit accounting.
4. Add explicit source-ref replace/add/remove/clear service intent and CLI validation, preserving canonical existing refs for additive operations.
5. Add changelog section inspection, dry-run planning, explicit rename handling, conflict preflight, and safe metadata preservation to release rename.
6. Generate an editable audit-decisions worksheet and validate all completed-row evidence requirements in one dry-run.
7. Add `current`, `finalize`, and `published` release-check phases, then align preparation and next-action guidance with the corrected release-day sequence.
8. Update all requested skill/docs/generated references and verify the resulting workflow with focused tests, static checks, full tests, and a temporary-git CLI smoke scenario.

## Tests

- Add and run the focused unit/CLI/end-to-end tests named in the brief.
- Run `ruff check .`, `mypy releaseledger`, and `pytest -q` after installing `.[dev]` as needed.
- Regenerate the CLI reference with `python scripts/generate_cli_reference.py` and verify the generated changes.
- Exercise human and JSON finalize/published checks in a temporary git repository, including tag creation and no direct `CHANGELOG.md` mutation.

## Assumptions

- The eight requested commits are implemented and committed in the stated order, with the brief's commit messages unless repository conventions require a small wording adjustment.
- Existing compatibility behavior remains: `--source-ref` replaces the full list, `current` is the default check phase, changelog writes remain explicit, and existing JSON fields are not removed.
- Development dependencies may need installation before validation; unavailable external dependencies will be recorded rather than hidden.
- The existing releaseledger and ledgercore APIs are extended only as needed; no storage-layout or broad CLI rewrite is included.

## Out of Scope

- Storage-layout migration, ledgercore redesign, automatic public/internal classification, automatic changelog prose generation, silent finalization, silent changelog replacement, and a new high-level correction command.
- General cleanup of unrelated existing taskledger or releaseledger behavior.
