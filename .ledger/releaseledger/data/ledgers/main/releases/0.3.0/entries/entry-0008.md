---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0008
release_version: 0.3.0
kind: added
summary:
  Added commit message leakage guard to prevent git commit subjects from being
  used as changelog entry summaries
status: accepted
audience: null
scopes: []
source_refs:
  - git:7d389dbd6ad600af69b3886ee050a06dc493b685
paths:
  - releaseledger/services/git_sources.py
  - skills/releaseledger/SKILL.md
issues: []
prs: []
sources: []
breaking: false
internal: false
order: 8
---
