---
schema_version: 1
object_type: implementation_check
file_version: v2
check_id: check-0010
task_id: task-0001
implementation_run: run-0002
timestamp: "2026-07-28T07:28:23Z"
command:
  python -m twine check dist/releaseledger-0.1.dev1+g7ee812656.d20260613-py3-none-any.whl
  dist/releaseledger-0.1.dev1+g7ee812656.d20260613.tar.gz dist/releaseledger-0.1.dev6+gdf3a6c925.d20260613-py3-none-any.whl
  dist/releaseledger-0.1.dev6+gdf3a6c925.d20260613.tar.gz dist/releaseledger-0.3.5.dev14+g2fa1340ca.d20260727-py3-none-any.whl
  dist/releaseledger-0.3.5.dev14+g2fa1340ca.d20260727.tar.gz dist/releaseledger-0.3.5.dev20+g05026ba43.d20260728-py3-none-any.whl
  dist/releaseledger-0.3.5.dev20+g05026ba43.d20260728.tar.gz
argv:
  - python
  - -m
  - twine
  - check
  - dist/releaseledger-0.1.dev1+g7ee812656.d20260613-py3-none-any.whl
  - dist/releaseledger-0.1.dev1+g7ee812656.d20260613.tar.gz
  - dist/releaseledger-0.1.dev6+gdf3a6c925.d20260613-py3-none-any.whl
  - dist/releaseledger-0.1.dev6+gdf3a6c925.d20260613.tar.gz
  - dist/releaseledger-0.3.5.dev14+g2fa1340ca.d20260727-py3-none-any.whl
  - dist/releaseledger-0.3.5.dev14+g2fa1340ca.d20260727.tar.gz
  - dist/releaseledger-0.3.5.dev20+g05026ba43.d20260728-py3-none-any.whl
  - dist/releaseledger-0.3.5.dev20+g05026ba43.d20260728.tar.gz
exit_code: 1
status: failed
category: other
summary:
  Ran python -m twine check dist/releaseledger-0.1.dev1+g7ee812656.d20260613-py3-none-any.whl
  dist/releaseledger-0.1.dev1+g7ee812656.d20260613.tar.gz dist/releaseledger-0.1.dev6+gdf3a6c925.d20260613-py3-none-any.whl
  dist/releaseledger-0.1.dev6+gdf3a6c925.d20260613.tar.gz dist/releaseledger-0.3.5.dev14+g2fa1340ca.d20260727-py3-none-any.whl
  dist/releaseledger-0.3.5.dev14+g2fa1340ca.d20260727.tar.gz dist/releaseledger-0.3.5.dev20+g05026ba43.d20260728-py3-none-any.whl
  dist/releaseledger-0.3.5.dev20+g05026ba43.d20260728.tar.gz (exit 1)
stdout_ref: null
stderr_ref: null
combined_ref: null
---
