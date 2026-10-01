---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0002
release_version: v0.4.12
kind: fixed
summary:
  Fixed release reconciliation to validate changelog dates against persisted
  release dates rather than tag target dates
status: accepted
audience: null
scopes: []
source_refs: []
paths:
  - releaseledger/services/releases.py
  - tests/test_release_reconcile.py
issues: []
prs: []
sources:
  - git:5e107b15e7df9f27c960d0ee2d50029faa6497ad
contributors:
  - "@holgern"
breaking: false
internal: false
order: 2
---

A tag pointing to a commit with a different target date no longer causes a changelog mismatch. Reconciliation still requires a released section's heading date to match the persisted release date.
