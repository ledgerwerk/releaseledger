---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0003
release_version: v0.4.7
kind: changed
summary:
  Changed release predecessor matching and changelog date checks to use canonical
  release identities
status: accepted
audience: null
scopes: []
source_refs: []
paths:
  - releaseledger/services/releases.py
  - tests/test_release_corrections.py
  - tests/test_release_reconcile.py
  - tests/test_stale_marker_workflow.py
issues: []
prs: []
sources:
  - git:fb370746f625697018e8e4a1006826997579b29b
contributors: []
breaking: false
internal: false
order: 3
---
