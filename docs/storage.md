# Storage and configuration

Releaseledger storage topology is owned by the canonical Ledgercore project
manifest. New projects use schema 3 and keep Releaseledger configuration at
`.ledger/releaseledger/config.toml`; authoritative data is a `data` mount and
derived indexes are a `cache` mount. Inspect the resolved topology with:

```bash
releaseledger --root PATH storage where
releaseledger --root PATH storage validate --strict
```

Change topology through the command boundary, with a real dry-run before a
write:

```bash
releaseledger storage set data --storage external \
  --storage-root ../ledger --scope project --dry-run
releaseledger storage set data --storage external \
  --storage-root ../ledger --scope project
```

Authoritative data must not use cache storage. Local overrides can be removed
with `storage clear-override data`; its result reports the effective location.

Legacy projects are migrated through the named lifecycle:

```bash
releaseledger migrate status
releaseledger migrate plan storage-layout --output migration-plan.json
releaseledger migrate apply storage-layout --plan-file migration-plan.json \
  --reason "Adopt the canonical Ledgercore storage layout"
releaseledger migrate recover --journal PATH --policy auto --dry-run
releaseledger migrate cleanup storage-layout --dry-run
releaseledger migrate cleanup storage-layout --yes \
  --reason "Remove verified legacy artifacts"
```

Plans are deterministic and hash-protected. The v2 plan carries one shared
migration ID, exact source/before/target fingerprints, and the rendered config
target. Apply is copy-only; physical activation and recovery belong to the
Ledgercore schema-3 journal. Cleanup never happens implicitly as part of
apply and requires a committed journal plus Releaseledger receipt.

The examples below document the pre-migration layout retained for discovery;
they are not the canonical configuration format.

## Default layout

A normal project stores release state inside the workspace:

```toml
config_version = 1
releaseledger_dir = ".releaseledger"

ledger_ref = "main"
ledger_parent_ref = ""
ledger_next_entry_number = 1
ledger_branch_guard = "off"

[ledger]
code = "rl"
name = "releaseledger"

[release]
default_changelog = "CHANGELOG.md"
default_status = "planned"
allow_dirty_worktree = true
```

Storage tree:

```text
.releaseledger/
  ledgers/
    main/
      releases/
        <version>/
          release.md
          entries/
            entry-0001.md
      events/
        events.jsonl
      indexes/
        releases.json
        entries.json
```

Release and entry Markdown records use schema version 2 and include validated
record metadata:

```yaml
schema_version: 2
object_type: release
versioning:
  schema_version: 1
  revision: 1
version: 1.2.0
status: released
released_at: 2026-06-14
```

`released_at` is the public changelog date. Mutation dates are not stored.
Indexes are derived and expose `record_revision` for inspection. Events are
append-only operation rows with affected record revisions; git history provides
chronology and exact before/after content.

## External state directories

Projects that keep generated state in a sibling repository can opt in to an
external path:

```toml
releaseledger_dir = "../ledger/release/releaseledger"
releaseledger_dir_policy = "external"
```

The CLI form is:

```bash
releaseledger init \
  --releaseledger-dir ../ledger/release/releaseledger \
  --external-dir

releaseledger config set releaseledger_dir \
  ../ledger/release/releaseledger \
  --external-dir
```

Relative paths that escape the workspace are rejected unless the external
policy is explicit.

## Diagnostics

Inspect effective paths and layout health without mutating state:

```bash
releaseledger storage where
releaseledger --json storage where
releaseledger config show
releaseledger --json config show
```
