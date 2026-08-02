---
schema_version: 4
id: content-0011
kind: content
type: section
section: risks_and_technical_debt
title: Risks and Technical Debt
order: 110
status: accepted
version: 2
body_format: markdown
---

## Risks

### R1: Large Repository Performance

| Risk       | Repositories with thousands of commits may slow git operations                   |
| ---------- | -------------------------------------------------------------------------------- |
| Impact     | Medium — `git rev-list` and diff parsing scale linearly                          |
| Mitigation | `max_commits` config limit (default 500); `max_diff_chars_per_commit` truncation |
| Status     | Mitigated by configuration                                                       |

### R2: ledgercore Version Coupling

| Risk       | Breaking changes in ledgercore could require coordinated updates |
| ---------- | ---------------------------------------------------------------- |
| Impact     | High — core storage primitives depend on it                      |
| Mitigation | Pin minimum version (`>=0.2.0`); ledgercore follows semver       |
| Status     | Managed via dependency pinning                                   |

### R3: Concurrent Access

| Risk       | Multiple processes writing to the same ledger simultaneously          |
| ---------- | --------------------------------------------------------------------- |
| Impact     | Medium — revision tracking catches conflicts but doesn't prevent them |
| Mitigation | Revision validation; atomic writes via `ledgercore.atomic_write_text` |
| Status     | Partially mitigated                                                   |

### R4: Git Worktree Assumptions

| Risk       | Git-first features assume a standard git worktree         |
| ---------- | --------------------------------------------------------- |
| Impact     | Low — non-git projects still work, only git commands warn |
| Mitigation | Graceful fallback; `git.enabled` config option            |
| Status     | Mitigated                                                 |

## Technical Debt

### TD1: Large CLI Module

| Debt   | `cli.py` is ~74KB — all commands in one file |
| ------ | -------------------------------------------- |
| Impact | Low — functional but hard to navigate        |
| Plan   | Could split into command groups (not urgent) |

### TD2: Manual Changelog Template Management

| Debt   | Default templates are string literals in config.py       |
| ------ | -------------------------------------------------------- |
| Impact | Low — works but template evolution requires code changes |
| Plan   | External template files with versioned defaults          |

### TD3: No Schema Migration Tooling

| Debt   | Schema version bumps require manual migration guidance |
| ------ | ------------------------------------------------------ |
| Impact | Low — current schema is stable (v2)                    |
| Plan   | Add migration tooling when schema v3 is needed         |

### TD4: Entry Kind Extension Requires Code Changes

| Debt   | Adding new entry kinds requires updating `states.py` and `domain/entry.py` |
| ------ | -------------------------------------------------------------------------- |
| Impact | Low — kinds are rare to add                                                |
| Plan   | Could make kinds configurable (not urgent)                                 |
