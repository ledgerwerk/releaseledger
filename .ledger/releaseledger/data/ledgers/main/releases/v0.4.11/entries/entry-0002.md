---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0002
release_version: v0.4.11
kind: changed
summary:
  Changed `status`, `storage where`, and `storage validate` to report storage
  validity and repair guidance
status: accepted
audience: null
scopes: []
source_refs:
  - git:9404db00e154fcae3a8c8b08de0500cf7a1dd06a
paths:
  - releaseledger/services/storage_health.py
  - releaseledger/cli.py
issues: []
prs: []
sources:
  - git:53a23296cd9b89076d83a93d518375ec19cf3383
  - git:9404db00e154fcae3a8c8b08de0500cf7a1dd06a
contributors:
  - "@holgern"
breaking: false
internal: false
order: 2
---

Storage diagnostics now distinguish raw layout validity from effective validity of the disposable indexes cache, list unexpected cache paths with portable POSIX separators, show the indexes binding classification, and print the matching repair command. `migrate status` additionally prints remediation guidance.
