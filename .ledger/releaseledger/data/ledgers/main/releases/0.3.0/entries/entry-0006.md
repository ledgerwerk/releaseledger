---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0006
release_version: 0.3.0
kind: changed
summary:
  Changed event schema to use versioned per-record revisions instead of wall-clock
  timestamps
status: accepted
audience: null
scopes: []
source_refs:
  - git:74983fdf2ffab92aa7a2f15ec8edd16fccc687be
paths:
  - releaseledger/domain/event.py
  - releaseledger/services/events.py
  - releaseledger/storage/store.py
issues: []
prs: []
sources: []
breaking: false
internal: false
order: 6
---
