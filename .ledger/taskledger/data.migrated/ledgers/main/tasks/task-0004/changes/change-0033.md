---
schema_version: 1
object_type: change
file_version: v2
change_id: change-0033
task_id: task-0004
implementation_run: run-0002
timestamp: '2026-08-21T12:11:31Z'
kind: scan
path: .
summary: Reconciled implementation changes against approved canceled release recovery
  plan.
git_commit: null
git_diff_stat: "branch: main\nstatus:\nM docs/commands.generated.md\n M docs/commands.md\n\
  \ M docs/concepts.md\n M docs/quickstart.md\n M releaseledger/api/entries.py\n M\
  \ releaseledger/api/releases.py\n M releaseledger/cli.py\n M releaseledger/command_registry.py\n\
  \ M releaseledger/domain/audit.py\n M releaseledger/domain/event.py\n M releaseledger/domain/release.py\n\
  \ M releaseledger/domain/states.py\n M releaseledger/errors.py\n M releaseledger/ledgercore_backend.py\n\
  \ M releaseledger/migration.py\n M releaseledger/services/audit.py\n M releaseledger/services/branch.py\n\
  \ M releaseledger/services/changelog_build.py\n M releaseledger/services/entries.py\n\
  \ M releaseledger/services/git_sources.py\n M releaseledger/services/releases.py\n\
  \ M releaseledger/storage/config.py\n M releaseledger/storage/store.py\n M skills/releaseledger/SKILL.md\n\
  \ M tests/test_changelog_full_build.py\n M tests/test_cli.py\n M tests/test_release_reconcile.py\n\
  ?? .ledger/taskledger/data/checkouts/\n?? .ledger/taskledger/data/ledgers/main/events/2026-08-21.ndjson\n\
  ?? .ledger/taskledger/data/ledgers/main/tasks/task-0004/\n?? .repairledger/\n??\
  \ 01_todo.md\n?? repairledger.toml\n?? tests/test_entry_move.py\n?? tests/test_lexhint_recovery.py\n\
  ?? tests/test_release_identity.py\n?? tests/test_release_replace.py\n?? tests/test_release_restore.py\n\
  diff_stat:\ndocs/commands.generated.md                | 136 +++---\n docs/commands.md\
  \                          |  22 +-\n docs/concepts.md                         \
  \ |  10 +-\n docs/quickstart.md                        |  30 +-\n releaseledger/api/entries.py\
  \              |   6 +-\n releaseledger/api/releases.py             |   8 +-\n releaseledger/cli.py\
  \                      | 119 ++++-\n releaseledger/command_registry.py         |\
  \   2 +\n releaseledger/domain/audit.py             |   4 +-\n releaseledger/domain/event.py\
  \             |   2 +\n releaseledger/domain/release.py           |  18 +\n releaseledger/domain/states.py\
  \            |   4 +-\n releaseledger/errors.py                   |   6 +-\n releaseledger/ledgercore_backend.py\
  \       |  16 +-\n releaseledger/migration.py                |  42 +-\n releaseledger/services/audit.py\
  \           |   2 +-\n releaseledger/services/branch.py          |  10 +-\n releaseledger/services/changelog_build.py\
  \ |  26 +-\n releaseledger/services/entries.py         | 234 +++++++++-\n releaseledger/services/git_sources.py\
  \     |  10 +-\n releaseledger/services/releases.py        | 700 +++++++++++++++++++++++-------\n\
  \ releaseledger/storage/config.py           |   4 +-\n releaseledger/storage/store.py\
  \            | 101 ++++-\n skills/releaseledger/SKILL.md             |   5 +-\n\
  \ tests/test_changelog_full_build.py        |   6 +-\n tests/test_cli.py       \
  \                  |   8 +-\n tests/test_release_reconcile.py           |  69 +++\n\
  \ 27 files changed, 1297 insertions(+), 303 deletions(-)"
command: git branch --show-current && git status --short && git diff --stat
before_hash: null
after_hash: null
exit_code: null
---
Reconciled implementation changes against approved canceled release recovery plan.
