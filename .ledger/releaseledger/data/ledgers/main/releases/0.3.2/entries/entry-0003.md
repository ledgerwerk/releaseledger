---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 2
entry_id: entry-0003
release_version: 0.3.2
kind: added
summary: Added keepachangelog and extended changelog group modes
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
order: 3
---

changelog_group_mode under [changelog] selects how entry kinds are grouped for rendering. keepachangelog renders the six Keep a Changelog 1.1.0 groups in order, mapping docs, quality, and internal onto Changed. extended (the default) renders Documentation, Quality, and Internal as their own groups.
