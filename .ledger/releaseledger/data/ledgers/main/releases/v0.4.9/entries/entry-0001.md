---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0001
release_version: v0.4.9
kind: added
summary:
  Added configurable Git tag ownership with external publication handoff guidance
  and published-tag validation
status: accepted
audience: null
scopes: []
source_refs:
  - git:0b4b7c10fa700021ba51cb14b14b7ecd48eff446
paths:
  - .ledger/releaseledger/config.toml
  - README.md
  - docs/commands.md
  - docs/concepts.md
  - docs/quickstart.md
  - docs/storage.md
  - releaseledger/cli.py
  - releaseledger/services/config.py
  - releaseledger/services/releases.py
  - releaseledger/services/review.py
  - releaseledger/storage/config.py
  - skills/releaseledger/SKILL.md
  - tests/test_cli.py
  - tests/test_config_v2.py
  - tests/test_git_cli.py
  - tests/test_release_review.py
  - tests/test_releaseledger_skill_protocol.py
issues: []
prs: []
sources:
  - git:0b4b7c10fa700021ba51cb14b14b7ecd48eff446
contributors:
  - "@holgern"
breaking: false
internal: false
order: 1
---
