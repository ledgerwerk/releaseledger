---
schema_version: 1
object_type: implementation_check
file_version: v2
check_id: check-0032
task_id: task-0004
implementation_run: run-0002
timestamp: '2026-08-21T12:11:15Z'
command: ruff check releaseledger/api/entries.py releaseledger/api/releases.py releaseledger/cli.py
  releaseledger/command_registry.py releaseledger/domain/event.py releaseledger/domain/release.py
  releaseledger/services/changelog_build.py releaseledger/services/entries.py releaseledger/services/releases.py
  releaseledger/storage/store.py tests/test_changelog_full_build.py tests/test_cli.py
  tests/test_release_reconcile.py tests/test_entry_move.py tests/test_lexhint_recovery.py
  tests/test_release_identity.py tests/test_release_replace.py tests/test_release_restore.py
  --select E9,F,I
argv:
- ruff
- check
- releaseledger/api/entries.py
- releaseledger/api/releases.py
- releaseledger/cli.py
- releaseledger/command_registry.py
- releaseledger/domain/event.py
- releaseledger/domain/release.py
- releaseledger/services/changelog_build.py
- releaseledger/services/entries.py
- releaseledger/services/releases.py
- releaseledger/storage/store.py
- tests/test_changelog_full_build.py
- tests/test_cli.py
- tests/test_release_reconcile.py
- tests/test_entry_move.py
- tests/test_lexhint_recovery.py
- tests/test_release_identity.py
- tests/test_release_replace.py
- tests/test_release_restore.py
- --select
- E9,F,I
exit_code: 0
status: passed
category: lint
summary: Ran ruff check releaseledger/api/entries.py releaseledger/api/releases.py
  releaseledger/cli.py releaseledger/command_registry.py releaseledger/domain/event.py
  releaseledger/domain/release.py releaseledger/services/changelog_build.py releaseledger/services/entries.py
  releaseledger/services/releases.py releaseledger/storage/store.py tests/test_changelog_full_build.py
  tests/test_cli.py tests/test_release_reconcile.py tests/test_entry_move.py tests/test_lexhint_recovery.py
  tests/test_release_identity.py tests/test_release_replace.py tests/test_release_restore.py
  --select E9,F,I (exit 0)
stdout_ref: null
stderr_ref: null
combined_ref: null
---

