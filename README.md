# releaseledger

Project-local release management for coding workflows.

`releaseledger` is a standalone, branch-scoped release-state ledger. It tracks
releases, changelog entries, events, and indexes under a `.releaseledger/`
directory configured by `.releaseledger.toml`. It reuses primitives from
[`ledgercore`](https://github.com/holgern/ledgercore) and does **not** depend on
`taskledger`.

- Release records are stored as Markdown-with-front-matter bundles:
  `.releaseledger/ledgers/<ledger_ref>/releases/<version>/release.md`.
- Changelog entries live alongside: `releases/<version>/entries/entry-NNNN.md`.
- Every mutation appends a JSONL event and rebuilds JSON indexes.
- `--json` envelopes are deterministic (sorted keys, trailing newline).

## Quickstart

```bash
releaseledger init
releaseledger release create 1.2.0 --title "Release 1.2.0"
releaseledger entry add 1.2.0 --kind added --summary "Add release bundle storage"
releaseledger changelog 1.2.0 --target-changelog CHANGELOG.md --release-date 2026-06-13
releaseledger release tag 1.2.0
```

`changelog` produces source/context for review or drafting; `build` writes the
final `CHANGELOG.md` section. Build CHANGELOG.md:

```bash
releaseledger changelog 1.2.0 --format json
releaseledger build 1.2.0 --dry-run --target-file CHANGELOG.md
releaseledger build 1.2.0 --release-date 2026-06-13 --target-file CHANGELOG.md
```

After `init` you get a `.releaseledger.toml` and a `.releaseledger/` layout:

```text
.releaseledger/
  ledgers/
    main/
      releases/      # one bundle per version (release.md + entries/)
      events/        # events.jsonl audit log
      indexes/       # releases.json, entries.json
```

## Commands

```text
releaseledger init [--releaseledger-dir .releaseledger] [--project-name NAME] [--force]
releaseledger release create VERSION [--title TEXT] [--status planned|draft|candidate|released]
                                     [--previous VERSION] [--note TEXT] [--changelog-file PATH]
                                     [--released-at YYYY-MM-DD]
releaseledger release tag VERSION [--previous VERSION] [--note TEXT] [--changelog-file PATH]
                                  [--released-at YYYY-MM-DD]
releaseledger release finalize VERSION [--released-at YYYY-MM-DD] [--changelog-file PATH]
releaseledger release list
releaseledger release show VERSION
releaseledger entry add VERSION --kind KIND --summary TEXT [--body TEXT]
                               [--path PATH]... [--issue REF]... [--pr REF]...
                               [--breaking] [--internal]
releaseledger entry list VERSION
releaseledger changelog VERSION [--format markdown|json] [--output PATH]
                                [--include-internal] [--target-changelog PATH]
                                [--release-date YYYY-MM-DD]
releaseledger build VERSION [--target-file CHANGELOG.md] [--release-date YYYY-MM-DD]
                            [--unreleased] [--include-internal] [--dry-run]
                            [--replace-existing] [--format markdown|json]
```

Entry kinds: `added`, `changed`, `fixed`, `removed`, `deprecated`, `security`,
`docs`, `internal`.

Root options: `--cwd PATH` (run as if started from `PATH`; the project is
discovered upward), `--json` (emit JSON envelopes), `--version`.

## JSON envelopes

Success:

```json
{
  "ok": true,
  "command": "release.tag",
  "result_type": "release",
  "result": {
    "kind": "release",
    "ledger_ref": "main",
    "release": {"version": "1.2.0", "status": "released", "...": "..."},
    "events": ["event-0001"]
  },
  "events": ["event-0001"]
}
```

Error (machine codes: `USAGE_ERROR`, `NOT_FOUND`, `CONFIG_ERROR`,
`VALIDATION_ERROR`, `CONFLICT`):

```json
{
  "ok": false,
  "command": "release.tag",
  "error": {
    "code": "USAGE_ERROR",
    "message": "Release version already exists: 1.2.0",
    "exit_code": 2,
    "remediation": ["Run `releaseledger release show 1.2.0`."]
  }
}
```

## Python API

A narrow, stable surface is re-exported from `releaseledger.api`:

```python
from releaseledger.api.releases import create_release, tag_release, show_release
from releaseledger.api.entries import add_release_entry
from releaseledger.api.changelog import build_changelog_file, render_changelog_section
from releaseledger.api.config import load_project_locator, render_default_releaseledger_toml
```

Services return plain dict payloads and raise `releaseledger.errors.LaunchError`
on failure; they never print or call `typer.Exit`.

## Development

```bash
python -m pip install -e ".[dev]"
pytest -q
ruff check .
mypy releaseledger
python -m build
```

The project ships `py.typed` and targets Python 3.10+.

## License

Apache-2.0
