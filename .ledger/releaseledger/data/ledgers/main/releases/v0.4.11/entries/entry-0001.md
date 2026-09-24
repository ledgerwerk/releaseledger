---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0001
release_version: v0.4.11
kind: added
summary:
  Added `repair index` to rebind, rebuild, or quarantine the disposable indexes
  cache
status: accepted
audience: null
scopes: []
source_refs:
  - git:53a23296cd9b89076d83a93d518375ec19cf3383
paths:
  - releaseledger/cli.py
  - releaseledger/services/storage_repair.py
  - releaseledger/services/storage_health.py
issues: []
prs: []
sources:
  - git:53a23296cd9b89076d83a93d518375ec19cf3383
contributors:
  - "@holgern"
breaking: false
internal: false
order: 1
---

The command inspects the generated indexes cache and applies explicit actions: rebind-and-rebuild for generated or legacy caches, quarantine-and-rebuild for foreign caches behind --quarantine-foreign, and a blocked action when neither is authorized. Records are never modified.
