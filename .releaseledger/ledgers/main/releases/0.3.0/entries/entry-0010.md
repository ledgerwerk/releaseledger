---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0010
release_version: 0.3.0
kind: changed
summary:
  Changed entry service to reject paths with trailing slashes and validate
  source_ref format before persisting
status: accepted
audience: null
scopes: []
source_refs:
  - git:48db86196ae626aa5b10427fed70f23219942da4
paths:
  - releaseledger/services/entries.py
  - releaseledger/storage/store.py
issues: []
prs: []
sources: []
breaking: false
internal: false
order: 10
---
