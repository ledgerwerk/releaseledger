---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 2
entry_id: entry-0002
release_version: 0.3.2
kind: added
summary: Added :root base sentinel for first-release git ranges
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
order: 2
---

Git range base now accepts :root (also ROOT, EMPTY, or the empty-tree SHA) as a start-of-repository base so first releases can collect every commit from the beginning. Base refs are displayed as :root and the range renders as :root..HEAD.
