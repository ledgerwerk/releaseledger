---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0001
release_version: 0.3.1
kind: added
summary:
  Added commit audit sheet system for per-release git-range review evidence
  with init, show, update, validate, and sync commands
status: accepted
audience: null
scopes: []
source_refs:
  - git:ffe9d5578a84fb171f22b2ad385e502f178a176d
paths:
  - releaseledger/domain/audit.py
  - releaseledger/services/audit.py
  - releaseledger/api/audit.py
  - releaseledger/cli.py
issues: []
prs: []
sources: []
breaking: false
internal: false
order: 1
---
