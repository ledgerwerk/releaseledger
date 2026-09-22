---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0001
release_version: v0.4.10
kind: added
summary:
  Added Agent Skill discovery across project, user, and admin locations with
  metadata validation and conflict diagnostics
status: accepted
audience: null
scopes: []
source_refs:
  - git:848795af918b53e25ec679a37bf4ac16df9c3043
paths:
  - docs/concepts.md
  - docs/quickstart.md
  - releaseledger/protocol.py
  - releaseledger/services/project_state.py
  - tests/test_cli_common_commands.py
  - tests/test_protocol.py
  - tests/test_releaseledger_skill_protocol.py
issues: []
prs: []
sources:
  - git:848795af918b53e25ec679a37bf4ac16df9c3043
contributors:
  - "@holgern"
breaking: false
internal: false
order: 1
---
