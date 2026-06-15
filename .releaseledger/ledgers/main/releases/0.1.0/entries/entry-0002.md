---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0002
release_version: 0.1.0
kind: added
summary:
  Added events.jsonl audit log and idempotent JSON index rebuild across all
  mutations
status: accepted
audience: null
scopes: []
source_refs:
  - git:e3a0eeb6a605f4c3059f83240e91e2d936c27030
paths:
  - releaseledger/services/events.py
  - releaseledger/domain/event.py
issues: []
prs: []
sources: []
breaking: false
internal: false
order: 2
---
