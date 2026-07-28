---
schema_version: 1
object_type: implementation_check
file_version: v2
check_id: check-0005
task_id: task-0001
implementation_run: run-0002
timestamp: "2026-07-28T07:21:36Z"
command:
  pytest -q tests/test_cli_storage_migrate_v2.py::test_migration_plan_is_hashed_and_stale_source_is_conflict
  tests/test_storage_migration.py::TestModeBehavior::test_move_removes_legacy_after_success
argv:
  - pytest
  - -q
  - tests/test_cli_storage_migrate_v2.py::test_migration_plan_is_hashed_and_stale_source_is_conflict
  - tests/test_storage_migration.py::TestModeBehavior::test_move_removes_legacy_after_success
exit_code: 0
status: passed
category: test
summary:
  Ran pytest -q tests/test_cli_storage_migrate_v2.py::test_migration_plan_is_hashed_and_stale_source_is_conflict
  tests/test_storage_migration.py::TestModeBehavior::test_move_removes_legacy_after_success
  (exit 0)
stdout_ref: null
stderr_ref: null
combined_ref: null
---
