---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0009
release_version: 0.3.0
kind: added
summary:
  Added atomic entry add-many with dry-run validation that catches all batch
  errors before writing any entry
status: accepted
audience: null
scopes: []
source_refs:
  - git:48db86196ae626aa5b10427fed70f23219942da4
paths:
  - releaseledger/services/entries.py
  - releaseledger/cli.py
issues: []
prs: []
sources: []
breaking: false
internal: false
order: 9
---
