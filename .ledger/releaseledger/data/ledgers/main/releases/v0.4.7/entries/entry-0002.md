---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0002
release_version: v0.4.7
kind: changed
summary:
  Changed release validation to detect unacknowledged changelog edits within
  the reviewed Git range
status: accepted
audience: null
scopes: []
source_refs: []
paths:
  - releaseledger/cli.py
  - releaseledger/services/review.py
  - tests/test_git_review.py
issues: []
prs: []
sources:
  - git:fb370746f625697018e8e4a1006826997579b29b
contributors: []
breaking: false
internal: false
order: 2
---
