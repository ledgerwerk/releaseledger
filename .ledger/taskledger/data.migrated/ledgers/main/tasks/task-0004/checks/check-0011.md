---
schema_version: 1
object_type: implementation_check
file_version: v2
check_id: check-0011
task_id: task-0004
implementation_run: run-0002
timestamp: '2026-08-21T11:41:13Z'
command: python -m pytest -q tests/test_release_restore.py tests/test_release_corrections.py
  tests/test_cli.py tests/test_version_correction_workflow.py
argv:
- python
- -m
- pytest
- -q
- tests/test_release_restore.py
- tests/test_release_corrections.py
- tests/test_cli.py
- tests/test_version_correction_workflow.py
exit_code: 1
status: failed
category: test
summary: 'Ran python -m pytest -q tests/test_release_restore.py tests/test_release_corrections.py
  tests/test_cli.py tests/test_version_correction_workflow.py (exit 1) output: @tasks/task-0004/artifacts/run-0002-command-0001.log'
stdout_ref: null
stderr_ref: null
combined_ref: null
---

