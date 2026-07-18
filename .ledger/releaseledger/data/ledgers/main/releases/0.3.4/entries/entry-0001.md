---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 2
entry_id: entry-0001
release_version: 0.3.4
kind: added
summary:
  Added release prepare, release check, audit evidence and complete phases,
  snapshot drift detection, and entry batch duplicate detection with strict and sync-audit
  options
status: accepted
audience: null
scopes: []
source_refs:
  - git:e1ed352bb1276209ed0a52fd1295139fc04f5f5d
paths:
  - README.md
  - docs/changelog.md
  - docs/commands.md
  - docs/concepts.md
  - docs/quickstart.md
  - releaseledger/cli.py
  - releaseledger/services/audit.py
  - releaseledger/services/changelog_build.py
  - releaseledger/services/entries.py
  - releaseledger/services/entry_lint.py
  - releaseledger/services/git_sources.py
  - releaseledger/services/releases.py
  - releaseledger/services/review.py
  - releaseledger/storage/store.py
  - skills/releaseledger/SKILL.md
issues: []
prs: []
sources:
  - git:e1ed352bb1276209ed0a52fd1295139fc04f5f5d
breaking: false
internal: false
order: 1
---
