---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0006
release_version: v0.4.6
kind: internal
summary: Changed release bookkeeping and formatting after the v0.4.5 tag
status: accepted
audience: null
scopes: []
source_refs:
  - git:95109145aa3a64687ced6253309922eca3b53ecf
paths:
  - .ledger/releaseledger/data/ledgers/main/events/events.jsonl
  - .ledger/releaseledger/data/ledgers/main/releases/v0.4.5/release.md
  - docs/changelog.md
  - releaseledger/cli.py
  - releaseledger/ledgercore_backend.py
  - releaseledger/migration.py
  - releaseledger/services/changelog_build.py
  - releaseledger/services/config.py
  - releaseledger/services/entries.py
  - releaseledger/services/review.py
  - releaseledger/storage/store.py
  - skills/releaseledger/SKILL.md
  - tests/test_changelog_github.py
  - tests/test_entry_atomicity.py
  - tests/test_index_binding.py
  - tests/test_stale_marker_workflow.py
issues: []
prs: []
sources: []
contributors: []
breaking: false
internal: true
order: 6
---
