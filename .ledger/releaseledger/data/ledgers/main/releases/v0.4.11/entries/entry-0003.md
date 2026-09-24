---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0003
release_version: v0.4.11
kind: internal
summary: Improved storage health test isolation from user-level skill discovery
status: accepted
audience: null
scopes: []
source_refs:
  - git:4f515e44c08a27f27aa054461ff820033968305a
paths:
  - tests/test_storage_health.py
issues: []
prs: []
sources:
  - git:4f515e44c08a27f27aa054461ff820033968305a
contributors:
  - "@holgern"
breaking: false
internal: true
order: 3
---

Test-only hardening for the new storage health behavior: tests pin HOME and install a matching skill fixture so user-level Agent Skill discovery cannot affect results. No user-facing behavior change; hidden from the public changelog.
