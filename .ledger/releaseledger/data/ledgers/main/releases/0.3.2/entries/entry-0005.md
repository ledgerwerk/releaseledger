---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 4
entry_id: entry-0005
release_version: 0.3.2
kind: changed
summary: Changed release show to display git range metadata
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
order: 5
---

release show now prints git_base_ref, git_head_ref, git_range, and git_commit_count when the release has an attached git range.
