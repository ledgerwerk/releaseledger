---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 4
entry_id: entry-0006
release_version: 0.3.2
kind: changed
summary: Changed git range --head to default to the stored release head
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
order: 6
---

git range for a real version now uses the release's stored git refs unless --base or --head is supplied explicitly. --head falls back to HEAD only when no head is stored, instead of always defaulting to the current HEAD.
