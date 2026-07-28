---
schema_version: 1
object_type: implementation_check
file_version: v2
check_id: check-0003
task_id: task-0001
implementation_run: run-0002
timestamp: '2026-07-28T07:12:43Z'
command: pytest -q tests/test_ledgercore_backend.py tests/test_cli_storage_migrate_v2.py
  tests/test_storage_migration.py
argv:
- pytest
- -q
- tests/test_ledgercore_backend.py
- tests/test_cli_storage_migrate_v2.py
- tests/test_storage_migration.py
exit_code: 1
status: failed
category: test
summary: 'Ran pytest -q tests/test_ledgercore_backend.py tests/test_cli_storage_migrate_v2.py
  tests/test_storage_migration.py (exit 1) output: @tasks/task-0001/artifacts/run-0002-command-0002.log'
stdout_ref: null
stderr_ref: null
combined_ref: null
---

