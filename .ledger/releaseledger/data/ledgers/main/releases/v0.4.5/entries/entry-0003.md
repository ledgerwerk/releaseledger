---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0003
release_version: v0.4.5
kind: quality
summary:
  Improved changelog and entry validation with canonical ordering, separate
  empty-section controls, and kind-aware linting
status: accepted
audience: null
scopes: []
source_refs: []
paths:
  - releaseledger/services/changelog_build.py
  - releaseledger/services/entry_lint.py
  - tests/test_entry_lint.py
issues: []
prs: []
sources:
  - git:19aef143a369ddd045cff8f5cbe4b09d363d58c2
contributors: []
breaking: false
internal: false
order: 3
---
