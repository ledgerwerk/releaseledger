# releaseledger

Project-local release management for coding workflows.

`releaseledger` stores release records, release-note entries, append-only
events, and deterministic JSON indexes in a project-local or explicitly external
state directory. It also renders reviewable changelog context and final
`CHANGELOG.md` sections.

```{toctree}
:maxdepth: 2
:caption: User guide

quickstart
concepts
commands
changelog
storage
```

```{toctree}
:maxdepth: 2
:caption: Reference

api
development
```

## Design constraints

Releaseledger is standalone. It depends on `ledgercore` for storage and
reference primitives, but it does not import taskledger or inspect taskledger
state. External provenance is represented by explicit global refs such as
`tl:task-0103`.
