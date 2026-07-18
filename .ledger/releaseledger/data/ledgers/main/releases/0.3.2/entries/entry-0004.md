---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 4
entry_id: entry-0004
release_version: 0.3.2
kind: changed
summary: Changed full builds to omit an empty Unreleased section and its link reference
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
order: 4
---

A full build no longer renders an empty ## [Unreleased] section. The heading and its [Unreleased] link reference are emitted only when an unreleased body exists, and the link reference is regenerated when a body is present.
