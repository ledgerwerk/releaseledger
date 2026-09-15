---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0001
release_version: v0.4.7
kind: added
summary: Added verified Git ancestry checks for GitHub new-contributor attribution
status: accepted
audience: null
scopes: []
source_refs:
  - git:fb370746f625697018e8e4a1006826997579b29b
paths:
  - releaseledger/services/changelog_build.py
  - releaseledger/services/git_sources.py
  - tests/test_changelog_github.py
  - tests/test_git_sources.py
issues: []
prs: []
sources:
  - git:fb370746f625697018e8e4a1006826997579b29b
contributors:
  - "@holgern"
breaking: false
internal: false
order: 1
---
