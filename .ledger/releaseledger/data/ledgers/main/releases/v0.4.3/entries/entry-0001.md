---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0001
release_version: v0.4.3
kind: added
summary:
  Added safe recovery workflows for canceled releases, version aliases, and
  atomic bundle and entry moves
status: accepted
audience: null
scopes: []
source_refs:
  - git:5f534fd97d5c7caa0ab341510735194adfe19254
paths:
  - releaseledger/services/releases.py
  - releaseledger/services/entries.py
  - releaseledger/domain/release.py
  - docs/commands.md
  - docs/concepts.md
  - docs/quickstart.md
  - tests/test_release_restore.py
  - tests/test_release_replace.py
  - tests/test_release_identity.py
  - tests/test_entry_move.py
issues: []
prs: []
sources: []
contributors: []
breaking: false
internal: false
order: 1
---
