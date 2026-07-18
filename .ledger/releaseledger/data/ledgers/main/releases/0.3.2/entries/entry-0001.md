---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 2
entry_id: entry-0001
release_version: 0.3.2
kind: added
summary:
  Added --unreleased-version to fold a planned release into the Unreleased
  section
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
order: 1
---

Full builds (build with no VERSION, or build --all) accept --unreleased-version VERSION to fold a planned, draft, or candidate release's accepted entries into the canonical ## [Unreleased] section without a version heading, and to exclude that release from the normal release sections. It is rejected for a missing, canceled, yanked, or already released version, and for single-section (non-full) builds.
