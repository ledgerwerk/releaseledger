---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0002
release_version: v0.4.6
kind: added
summary: Added safe rebinding for generated index caches before ledger writes
status: accepted
audience: null
scopes: []
source_refs: []
paths:
  - releaseledger/ledgercore_backend.py
  - releaseledger/services/config.py
  - releaseledger/services/project_state.py
  - releaseledger/storage/store.py
  - tests/test_index_binding.py
  - tests/test_stale_marker_workflow.py
issues: []
prs: []
sources:
  - git:99d03b10f6853e0103cee5b6349bee7db88b06d9
contributors: []
breaking: false
internal: false
order: 2
---
