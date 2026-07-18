---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 4
entry_id: entry-0007
release_version: 0.3.2
kind: changed
summary: Changed entry lint to report per-entry issues on failure
status: accepted
audience: null
scopes: []
source_refs:
  - git:9f10c3d476e4bbc0326717d5c57613bdb86fa074
paths: []
issues: []
prs: []
sources: []
breaking: false
internal: false
order: 7
---

entry lint now emits the full per-entry issues and entries payload on failure. The JSON form returns result plus the error envelope, the text form lists each issue with severity, field, code, and message, and the command still exits non-zero.
