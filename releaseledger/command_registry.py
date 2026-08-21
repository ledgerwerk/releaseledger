"""Single source of truth for Releaseledger command metadata.

Typer remains the console framework, but inventory consumers (``commands``,
``help``, docs, and drift checks) use this registry rather than maintaining a
second hand-written command table.
"""

from __future__ import annotations

from ledgercore.cli import CommandInventory, CommandMetadata


def _metadata(
    path: str,
    summary: str,
    *,
    effect: str = "read",
    requires_workspace: bool = True,
    requires_active_record: bool = False,
    targeting: str = "none",
    aliases: tuple[str, ...] = (),
    stability: str = "stable",
    deprecated: bool = False,
    replacement: str | None = None,
) -> CommandMetadata:
    return CommandMetadata(
        path=path,
        summary=summary,
        effect=effect,  # type: ignore[arg-type]
        requires_workspace=requires_workspace,
        requires_active_record=requires_active_record,
        targeting=targeting,
        aliases=aliases,
        stability=stability,  # type: ignore[arg-type]
        deprecated=deprecated,
        replacement=replacement,
    )


def build_command_inventory() -> CommandInventory:
    """Build the deterministic canonical command inventory."""
    entries: list[CommandMetadata] = []

    def add(path: str, summary: str, **kwargs: object) -> None:
        entries.append(_metadata(path, summary, **kwargs))  # type: ignore[arg-type]

    add(
        "init",
        "Initialize a canonical Releaseledger project.",
        effect="workspace-write",
        requires_workspace=False,
    )
    add("status", "Show a concise read-only project status.")
    add("info", "Show the complete read-only project inventory.")
    add("doctor", "Run read-only project diagnostics.")
    add("next-action", "Recommend the next project command without executing it.")
    add(
        "commands",
        "List canonical commands and their effects.",
        requires_workspace=False,
    )
    add("help", "Show generated help for a command path.", requires_workspace=False)

    release = [
        ("create", "Create a release record.", "ledger-write"),
        ("tag", "Create a released release record.", "ledger-write"),
        ("update", "Update release metadata.", "ledger-write"),
        ("set-status", "Change a nonterminal release status.", "ledger-write"),
        ("finalize", "Finalize a release.", "ledger-write"),
        ("restore", "Restore a canceled release.", "ledger-write"),
        ("prepare", "Prepare release artifacts.", "external-write"),
        ("list", "List releases."),
        ("show", "Show one release."),
        ("review", "Review release coverage and readiness.", "read", ("review",)),
        ("reconcile", "Inspect release reconciliation."),
        ("import-tags", "Import release tags from Git.", "external-process"),
        ("check", "Check release readiness."),
        ("cancel", "Cancel a release.", "ledger-write"),
        ("rename", "Rename a release.", "ledger-write"),
    ]
    for item in release:
        path, summary, *rest = item
        effect = rest[0] if rest and isinstance(rest[0], str) else "read"
        aliases = rest[1] if len(rest) > 1 else ()
        add(
            f"release {path}",
            summary,  # type: ignore[arg-type]
            effect=effect,
            aliases=aliases,
            targeting="release-version",
        )
    add(
        "release chain check", "Check the predecessor chain.", targeting="release-chain"
    )
    add(
        "release chain repair",
        "Repair the predecessor chain.",
        effect="ledger-write",
        targeting="release-chain",
    )

    entry = [
        ("add", "Add a release entry.", "ledger-write"),
        ("show", "Show one release entry."),
        ("update", "Update entry metadata.", "ledger-write"),
        ("set-status", "Change an entry status.", "ledger-write"),
        ("delete", "Delete a release entry.", "ledger-write"),
        ("move", "Move a release entry.", "ledger-write"),
        ("import", "Import one release entry.", "ledger-write"),
        (
            "apply",
            "Apply a validated entry batch.",
            "ledger-write",
            ("entry add-many",),
        ),
        ("list", "List entries for a release."),
        ("lint", "Lint release entries."),
        ("prompt", "Render an entry drafting prompt."),
    ]
    for item in entry:
        path, summary, *rest = item
        effect = rest[0] if rest and isinstance(rest[0], str) else "read"
        aliases = rest[1] if len(rest) > 1 else ()
        add(
            f"entry {path}",
            summary,  # type: ignore[arg-type]
            effect=effect,
            aliases=aliases,
            targeting="release-entry",
        )

    add(
        "changelog preview",
        "Render a changelog without writing it.",
        aliases=("changelog",),
        targeting="release-version",
    )
    add(
        "changelog build",
        "Build the changelog artifact.",
        effect="external-write",
        aliases=("build",),
        targeting="release-version",
    )
    add(
        "changelog section remove",
        "Remove a changelog section.",
        effect="external-write",
        aliases=("changelog-section remove-section",),
        targeting="changelog",
    )
    add(
        "changelog section rename",
        "Rename a changelog section.",
        effect="external-write",
        aliases=("changelog-section rename-section",),
        targeting="changelog",
    )

    for path, summary, effect in (
        ("git range", "Inspect a Git source range.", "external-process"),
        ("git import", "Import Git source entries.", "ledger-write"),
        ("git evidence", "Export Git evidence.", "external-process"),
        ("git scaffold", "Generate a Git entry scaffold.", "read"),
        ("branch status", "Show branch status.", "read"),
        ("branch start", "Start a release branch.", "external-process"),
        ("branch merge", "Merge a release branch.", "external-process"),
    ):
        add(path, summary, effect=effect)

    for path, summary, effect in (
        ("audit init", "Create an audit sheet.", "ledger-write"),
        (
            "audit decisions",
            "Generate an editable audit decision worksheet.",
            "external-write",
        ),
        ("audit show", "Show an audit sheet.", "read"),
        ("audit apply", "Apply audit annotations.", "ledger-write"),
        ("audit refresh", "Refresh audit coverage.", "ledger-write"),
        ("audit update", "Update audit rows.", "ledger-write"),
        ("audit validate", "Validate an audit sheet without writing.", "read"),
        (
            "audit record-validation",
            "Record an explicit validation event.",
            "ledger-write",
        ),
        ("audit sync", "Synchronize audit targets.", "ledger-write"),
    ):
        add(path, summary, effect=effect, targeting="release-version")

    add("storage where", "Show effective storage topology.")
    add("storage validate", "Validate storage topology.")
    add("storage set", "Change storage topology.", effect="workspace-write")
    add(
        "storage clear-override",
        "Clear a local storage override.",
        effect="workspace-write",
    )
    add(
        "migrate status",
        "Show migration status.",
        aliases=("storage migrate", "storage migrate status"),
    )
    add(
        "migrate plan",
        "Plan a named migration.",
        aliases=("storage migrate plan",),
    )
    add(
        "migrate apply",
        "Apply a named migration.",
        effect="workspace-write",
        aliases=("storage migrate apply",),
    )
    add(
        "migrate recover",
        "Recover an interrupted migration.",
        effect="workspace-write",
        aliases=("storage migrate recover",),
    )
    add(
        "migrate cleanup",
        "Explicitly clean verified legacy state.",
        effect="workspace-write",
    )
    add("config show", "Show effective configuration.")
    add("config validate", "Validate configuration.")
    add(
        "config set",
        "Deprecated unsupported configuration mutation.",
        stability="deprecated",
        deprecated=True,
        replacement="config validate",
    )

    return CommandInventory(tuple(sorted(entries, key=lambda item: item.path)))


COMMAND_INVENTORY = build_command_inventory()


def resolve_command(path_parts: list[str]) -> CommandMetadata | None:
    """Resolve a canonical or compatibility path from command arguments."""
    return COMMAND_INVENTORY.resolve(" ".join(path_parts))


def command_help(path_parts: list[str]) -> dict[str, object]:
    """Return generated help data for a command or group path."""
    path = " ".join(path_parts).strip()
    metadata = COMMAND_INVENTORY.resolve(path)
    if metadata is not None:
        result = metadata.as_mapping()
        result["canonical"] = metadata.path
        if path != metadata.path:
            result["deprecated_invocation"] = path
        return result
    prefix = f"{path} " if path else ""
    children = [
        item.as_mapping()
        for item in COMMAND_INVENTORY.entries
        if item.path.startswith(prefix)
        and len(item.path.split()) == len(path_parts) + 1
    ]
    if children:
        return {"path": path, "children": children}
    raise KeyError(path)
