---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0001
release_version: v0.4.12
kind: changed
summary:
  Changed strict public builds to accept complete internal or rejected audit
  decisions as commit coverage
status: accepted
audience: null
scopes: []
source_refs:
  - git:5e107b15e7df9f27c960d0ee2d50029faa6497ad
paths:
  - releaseledger/services/audit.py
  - releaseledger/services/changelog_build.py
  - releaseledger/services/review.py
  - tests/test_changelog_full_build.py
  - tests/test_commit_audit.py
issues: []
prs: []
sources:
  - git:5e107b15e7df9f27c960d0ee2d50029faa6497ad
contributors:
  - "@holgern"
breaking: false
internal: false
order: 1
---

Strict public changelog builds can now account for commits with complete, current internal or rejected audit decisions without requiring public entries. Incomplete or stale decisions remain uncovered, and internal- inclusive builds still require an entry for internal commits. Release review uses the same evidence-completeness rule.
