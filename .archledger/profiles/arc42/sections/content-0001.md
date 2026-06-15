---
schema_version: 4
id: content-0001
kind: content
type: section
section: introduction_and_goals
title: Introduction and Goals
order: 10
status: accepted
version: 2
body_format: markdown
---

## Introduction

**releaseledger** is a project-local release-state ledger for Python projects and
other source repositories. It records releases, release-note entries, operation
events, and JSON indexes in a deterministic file layout. It renders reviewable
changelog context and writes final `CHANGELOG.md` sections from ledger entries.

Releaseledger is **git-first**: the authoritative evidence of shipped changes is
the git commit range between the previous shipped release (base) and the new
release target (head). Git tags and commit ranges define the shipped change set.
Taskledger, issue trackers, and PR descriptions are optional provenance context.

## Requirements Overview

| ID  | Requirement                                                           |
| --- | --------------------------------------------------------------------- |
| R1  | Record releases with deterministic Markdown + YAML front matter files |
| R2  | Attach changelog entries to releases with structured metadata         |
| R3  | Track every mutation via an append-only event log                     |
| R4  | Render `CHANGELOG.md` from entries using Jinja2 templates             |
| R5  | Derive release entries from git commit ranges (git-first workflow)    |
| R6  | Validate source coverage between release boundaries                   |
| R7  | Support branch-scoped ledger state safe for VCS commit                |
| R8  | Provide both JSON (machine) and human-readable CLI output             |

## Quality Goals

| Priority | Quality Goal              | Scenario                                                                      |
| -------- | ------------------------- | ----------------------------------------------------------------------------- |
| 1        | **Deterministic storage** | Identical inputs produce byte-identical Markdown/JSON output                  |
| 2        | **Git-first provenance**  | Every entry traces to a `git:<sha>` or a global ref (`tl:task-0006`)          |
| 3        | **Schema stability**      | Records validate against versioned schemas; new fields are additive           |
| 4        | **Offline operation**     | All commands work without network access                                      |
| 5        | **Extensibility**         | Entry kinds, template profiles, and changelog postprocessors are configurable |

## Key Stakeholders

| Role               | Expectation                                               |
| ------------------ | --------------------------------------------------------- |
| **Maintainer**     | Generate accurate changelogs from structured release data |
| **CI/CD pipeline** | Machine-readable JSON output for automation               |
| **Contributor**    | Understand what changed and why via structured entries    |
| **Auditor**        | Immutable event log with full mutation history            |
