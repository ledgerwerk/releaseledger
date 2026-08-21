---
schema_version: 1
object_type: implementation_check
file_version: v2
check_id: check-0031
task_id: task-0004
implementation_run: run-0002
timestamp: '2026-08-21T12:10:41Z'
command: ruff check releaseledger tests/test_entry_move.py tests/test_lexhint_recovery.py
  tests/test_release_identity.py tests/test_release_replace.py tests/test_release_restore.py
argv:
- ruff
- check
- releaseledger
- tests/test_entry_move.py
- tests/test_lexhint_recovery.py
- tests/test_release_identity.py
- tests/test_release_replace.py
- tests/test_release_restore.py
exit_code: 1
status: failed
category: lint
summary: 'Ran ruff check releaseledger tests/test_entry_move.py tests/test_lexhint_recovery.py
  tests/test_release_identity.py tests/test_release_replace.py tests/test_release_restore.py
  (exit 1) output: @tasks/task-0004/artifacts/run-0002-command-0005.log'
stdout_ref: null
stderr_ref: null
combined_ref: null
---

