---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0004
release_version: 0.3.4
kind: fixed
summary:
  Fixed mypy findings and pre-commit formatting across releaseledger services
  and tests
status: accepted
audience: null
scopes: []
source_refs:
  - git:9f64538a27b5bdd309444c5670a60ded1502d0c4
paths:
  - releaseledger/cli.py
  - releaseledger/services/audit.py
  - releaseledger/services/changelog_build.py
  - releaseledger/services/entries.py
  - releaseledger/services/entry_lint.py
  - releaseledger/services/git_sources.py
  - releaseledger/services/releases.py
  - releaseledger/services/review.py
  - releaseledger/storage/config.py
  - releaseledger/storage/store.py
issues: []
prs: []
sources:
  - git:9f64538a27b5bdd309444c5670a60ded1502d0c4
breaking: false
internal: true
order: 4
---
