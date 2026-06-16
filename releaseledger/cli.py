"""Releaseledger command-line interface.

The root :data:`app` exposes ``--cwd``, ``--json`` and ``--version`` and stores
a :class:`~releaseledger.cli_common.CLIState` on the typer context for
subcommands. Subcommand groups are registered progressively (``init``,
``release``, ``entry``, ``changelog``) at the bottom of this module.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from releaseledger._version import __version__
from releaseledger.cli_common import (
    CLIState,
    CommandResult,
    cli_state_from_context,
    emit_error,
    emit_payload,
    launch_error_exit_code,
    render_json,
    resolve_workspace_root,
    run_command,
    store_cli_state,
    write_text_output,
)
from releaseledger.errors import CODE_USAGE_ERROR, LaunchError, ReleaseledgerError
from releaseledger.services.audit import (
    collect_commit_subjects,
    create_commit_audit_sheet,
    guard_entry_summaries,
    render_commit_audit_sheet,
    sync_audit_targets_from_entries,
    update_commit_audit_sheet,
    validate_commit_audit_sheet,
)
from releaseledger.services.branch import (
    branch_merge,
    branch_start,
    branch_status,
)
from releaseledger.services.changelog import build_changelog_context
from releaseledger.services.changelog_build import (
    build_changelog_file,
    build_full_changelog_file,
)
from releaseledger.services.config import (
    config_set_releaseledger_dir,
    config_show,
    storage_where,
)
from releaseledger.services.entries import (
    add_many_release_entries,
    add_release_entry,
    import_release_entry_file,
    list_release_entries,
    load_entry_batch_file,
    show_release_entry,
    update_release_entry,
)
from releaseledger.services.entry_lint import lint_release_entries
from releaseledger.services.entry_prompt import build_entry_prompt
from releaseledger.services.git_sources import (
    GIT_DEFAULT_HEAD,
    GIT_DEFAULT_INCLUDE_MERGES,
    GitSourceCandidate,
    collect_git_candidates,
    resolve_git_ref,
)
from releaseledger.services.releases import (
    UNSET,
    cancel_release,
    check_release_chain,
    create_release,
    finalize_release,
    list_release_records,
    remove_changelog_section,
    rename_changelog_section,
    rename_release,
    repair_release_chain,
    show_release,
    tag_release,
    update_release,
)
from releaseledger.services.review import build_release_review
from releaseledger.storage.config import load_project_config
from releaseledger.storage.paths import (
    ProjectPaths,
    initialize_project,
    require_project,
)
from releaseledger.storage.store import load_release

app = typer.Typer(
    add_completion=True,
    help="Manage project-local release state.",
    no_args_is_help=True,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"releaseledger {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def releaseledger_main(
    ctx: typer.Context,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Print version and exit.",
        ),
    ] = False,
    cwd: Annotated[
        Path | None,
        typer.Option("--cwd", help="Run as if started from PATH."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON envelopes."),
    ] = False,
) -> None:
    """Manage project-local release state."""
    store_cli_state(
        ctx,
        CLIState(cwd=resolve_workspace_root(cwd), json_output=json_output),
    )
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()


def _paths(ctx: typer.Context) -> ProjectPaths:
    """Resolve project paths from CLI state, raising on uninitialized projects."""
    state = cli_state_from_context(ctx)
    return require_project(state.cwd)


@app.command("init")
def init_command(
    ctx: typer.Context,
    releaseledger_dir: Annotated[
        str | None,
        typer.Option("--releaseledger-dir", help="State directory name or path."),
    ] = None,
    project_name: Annotated[
        str | None,
        typer.Option("--project-name", help="Project name for changelog headers."),
    ] = None,
    force: Annotated[
        bool,
        typer.Option("--force", help="Overwrite an existing config."),
    ] = False,
    external_dir: Annotated[
        bool,
        typer.Option(
            "--external-dir",
            help="Allow --releaseledger-dir to resolve outside the workspace.",
        ),
    ] = False,
) -> None:
    """Initialize .releaseledger.toml and the default state layout."""
    state = cli_state_from_context(ctx)
    workspace_root = state.cwd

    def produce() -> CommandResult:
        result = initialize_project(
            workspace_root,
            releaseledger_dir=releaseledger_dir,
            project_name=project_name,
            force=force,
            external_dir=external_dir,
        )
        rel_dir = Path(str(result["releaseledger_dir"]))
        try:
            display = rel_dir.relative_to(workspace_root.resolve())
            display_str = str(display)
        except ValueError:
            display_str = str(rel_dir)
        human = f"initialized releaseledger in {display_str}\nwrote .releaseledger.toml"
        return result, [], human

    run_command(
        command="init",
        result_type="project_init",
        json_output=state.json_output,
        produce=produce,
    )


release_app = typer.Typer(help="Manage releases.")
app.add_typer(release_app, name="release")


def _release_human_summary(record: dict[str, object]) -> str:
    version = str(record.get("version", ""))
    status = str(record.get("status", ""))
    date_value = record.get("released_at") or ""
    title = record.get("title") or record.get("note") or ""
    title_text = str(title).splitlines()[0] if title else ""
    return f"{version}  {status}  {date_value}  {title_text}".rstrip()


@release_app.command("create")
def release_create_command(
    ctx: typer.Context,
    version: Annotated[str, typer.Argument(help="Release version string.")],
    title: Annotated[str | None, typer.Option("--title", help="Release title.")] = None,
    status: Annotated[
        str,
        typer.Option("--status", help="planned|draft|candidate|released."),
    ] = "planned",
    previous_version: Annotated[
        str | None,
        typer.Option("--previous", help="Explicit previous release version."),
    ] = None,
    note: Annotated[
        str | None, typer.Option("--note", help="Release note body.")
    ] = None,
    changelog_file: Annotated[
        str | None,
        typer.Option("--changelog-file", help="Target changelog file."),
    ] = None,
    released_at: Annotated[
        str | None,
        typer.Option("--released-at", help="Release date YYYY-MM-DD."),
    ] = None,
    boundary_ref: Annotated[
        str | None, typer.Option("--boundary-ref", help="Upper source boundary ref.")
    ] = None,
    source_refs: Annotated[
        list[str] | None,
        typer.Option("--source-ref", help="Included global source ref (repeatable)."),
    ] = None,
    source_count: Annotated[
        int | None, typer.Option("--source-count", help="Number of source records.")
    ] = None,
) -> None:
    """Create a new release record."""
    state = cli_state_from_context(ctx)

    def produce() -> CommandResult:
        workspace_root = _paths(ctx).workspace_root
        result = create_release(
            workspace_root,
            version=version,
            title=title,
            status=status,
            note=note,
            previous_version=previous_version,
            changelog_file=changelog_file,
            released_at=released_at,
            boundary_ref=boundary_ref,
            source_refs=tuple(source_refs or ()),
            source_count=source_count,
        )
        return result, _event_ids(result), f"created release {version}"

    run_command(
        command="release.create",
        result_type="release",
        json_output=state.json_output,
        produce=produce,
        workspace_root=_paths(ctx).workspace_root,
        mutating=True,
    )


@release_app.command("tag")
def release_tag_command(
    ctx: typer.Context,
    version: Annotated[str, typer.Argument(help="Release version string.")],
    previous_version: Annotated[
        str | None,
        typer.Option("--previous", help="Explicit previous release version."),
    ] = None,
    note: Annotated[
        str | None, typer.Option("--note", help="Release note body.")
    ] = None,
    changelog_file: Annotated[
        str | None,
        typer.Option("--changelog-file", help="Target changelog file."),
    ] = None,
    released_at: Annotated[
        str | None,
        typer.Option("--released-at", help="Release date YYYY-MM-DD."),
    ] = None,
    boundary_ref: Annotated[
        str | None, typer.Option("--boundary-ref", help="Upper source boundary ref.")
    ] = None,
    source_refs: Annotated[
        list[str] | None,
        typer.Option("--source-ref", help="Included global source ref (repeatable)."),
    ] = None,
    source_count: Annotated[
        int | None, typer.Option("--source-count", help="Number of source records.")
    ] = None,
) -> None:
    """Create a release with status 'released'."""
    state = cli_state_from_context(ctx)

    def produce() -> CommandResult:
        workspace_root = _paths(ctx).workspace_root
        result = tag_release(
            workspace_root,
            version=version,
            note=note,
            previous_version=previous_version,
            changelog_file=changelog_file,
            released_at=released_at,
            boundary_ref=boundary_ref,
            source_refs=tuple(source_refs or ()),
            source_count=source_count,
        )
        return result, _event_ids(result), f"tagged release {version}"

    run_command(
        command="release.tag",
        result_type="release",
        json_output=state.json_output,
        produce=produce,
    )


@release_app.command("update")
def release_update_command(
    ctx: typer.Context,
    version: Annotated[str, typer.Argument(help="Release version string.")],
    title: Annotated[str | None, typer.Option("--title")] = None,
    status: Annotated[str | None, typer.Option("--status")] = None,
    note: Annotated[str | None, typer.Option("--note")] = None,
    previous_version: Annotated[str | None, typer.Option("--previous")] = None,
    changelog_file: Annotated[str | None, typer.Option("--changelog-file")] = None,
    boundary_ref: Annotated[str | None, typer.Option("--boundary-ref")] = None,
    source_refs: Annotated[list[str] | None, typer.Option("--source-ref")] = None,
    source_count: Annotated[int | None, typer.Option("--source-count")] = None,
    released_at: Annotated[
        str | None,
        typer.Option("--released-at", help="Release date YYYY-MM-DD."),
    ] = None,
    clear_previous: Annotated[
        bool,
        typer.Option("--clear-previous", help="Clear the previous_version field."),
    ] = False,
    clear_changelog_file: Annotated[
        bool,
        typer.Option("--clear-changelog-file", help="Clear the changelog_file field."),
    ] = False,
    clear_boundary_ref: Annotated[
        bool,
        typer.Option("--clear-boundary-ref", help="Clear the boundary_ref field."),
    ] = False,
    clear_source_refs: Annotated[
        bool,
        typer.Option("--clear-source-refs", help="Clear the source_refs field."),
    ] = False,
    clear_source_count: Annotated[
        bool,
        typer.Option("--clear-source-count", help="Clear the source_count field."),
    ] = False,
    clear_released_at: Annotated[
        bool,
        typer.Option("--clear-released-at", help="Clear the released_at field."),
    ] = False,
    git_base_ref: Annotated[
        str | None,
        typer.Option(
            "--git-base",
            help="Git range base ref (e.g. v0.1.0); resolved to a full SHA.",
        ),
    ] = None,
    git_head_ref: Annotated[
        str | None,
        typer.Option(
            "--git-head",
            help="Git range head ref (e.g. HEAD); resolved to a full SHA.",
        ),
    ] = None,
    clear_git_range: Annotated[
        bool,
        typer.Option("--clear-git-range", help="Clear all stored git range metadata."),
    ] = False,
    force: Annotated[
        bool,
        typer.Option(
            "--force", help="Allow clearing released_at on a released release."
        ),
    ] = False,
) -> None:
    """Update release metadata, with explicit clear flags for optional fields."""
    state = cli_state_from_context(ctx)

    def produce() -> CommandResult:
        result = update_release(
            _paths(ctx).workspace_root,
            version=version,
            title=title,
            status=status,
            note=note,
            previous_version=(
                previous_version if previous_version is not None else UNSET
            ),
            changelog_file=(changelog_file if changelog_file is not None else UNSET),
            boundary_ref=boundary_ref if boundary_ref is not None else UNSET,
            source_refs=(tuple(source_refs) if source_refs is not None else UNSET),
            source_count=source_count if source_count is not None else UNSET,
            released_at=released_at if released_at is not None else UNSET,
            clear_previous=clear_previous,
            clear_changelog_file=clear_changelog_file,
            clear_boundary_ref=clear_boundary_ref,
            clear_source_refs=clear_source_refs,
            clear_source_count=clear_source_count,
            clear_released_at=clear_released_at,
            git_base_ref=git_base_ref if git_base_ref is not None else UNSET,
            git_head_ref=git_head_ref if git_head_ref is not None else UNSET,
            clear_git_range=clear_git_range,
            force=force,
        )
        return result, _event_ids(result), f"updated release {version}"

    run_command(
        command="release.update",
        result_type="release",
        json_output=state.json_output,
        produce=produce,
        workspace_root=_paths(ctx).workspace_root,
        mutating=True,
    )


@release_app.command("finalize")
def release_finalize_command(
    ctx: typer.Context,
    version: Annotated[str, typer.Argument(help="Release version string.")],
    released_at: Annotated[
        str | None,
        typer.Option("--released-at", help="Release date YYYY-MM-DD."),
    ] = None,
    changelog_file: Annotated[
        str | None,
        typer.Option("--changelog-file", help="Target changelog file."),
    ] = None,
) -> None:
    """Transition a planned/draft/candidate release to 'released'."""
    state = cli_state_from_context(ctx)

    def produce() -> CommandResult:
        workspace_root = _paths(ctx).workspace_root
        result = finalize_release(
            workspace_root,
            version=version,
            released_at=released_at,
            changelog_file=changelog_file,
        )
        return result, _event_ids(result), f"finalized release {version}"

    run_command(
        command="release.finalize",
        result_type="release",
        json_output=state.json_output,
        produce=produce,
    )


@release_app.command("list")
def release_list_command(ctx: typer.Context) -> None:
    """List all releases."""
    state = cli_state_from_context(ctx)

    def produce() -> CommandResult:
        workspace_root = _paths(ctx).workspace_root
        releases = list_release_records(workspace_root)
        result: dict[str, object] = {"kind": "release_list", "releases": releases}
        if releases:
            lines = ["RELEASES"]
            for record in releases:
                lines.append(_release_human_summary(record))
            human = "\n".join(lines)
        else:
            human = "RELEASES\n(none)"
        return result, [], human

    run_command(
        command="release.list",
        result_type="release_list",
        json_output=state.json_output,
        produce=produce,
    )


@release_app.command("show")
def release_show_command(
    ctx: typer.Context,
    version: Annotated[str, typer.Argument(help="Release version string.")],
) -> None:
    """Show a release and its entries."""
    state = cli_state_from_context(ctx)

    def produce() -> CommandResult:
        workspace_root = _paths(ctx).workspace_root
        result = show_release(workspace_root, version)
        release_raw = result.get("release", {})
        record = dict(release_raw) if isinstance(release_raw, dict) else {}
        lines = [f"version: {record.get('version', '')}"]
        lines.append(f"status: {record.get('status', '')}")
        if record.get("title"):
            lines.append(f"title: {record['title']}")
        if record.get("released_at"):
            lines.append(f"released_at: {record['released_at']}")
        if record.get("previous_version"):
            lines.append(f"previous_version: {record['previous_version']}")
        lines.append(f"entry_count: {result.get('entry_count', 0)}")
        note = record.get("note")
        if note:
            note_text = str(note).splitlines()[0] if str(note).splitlines() else ""
            if note_text:
                lines.append(f"note: {note_text}")
        human = "\n".join(lines)
        return result, [], human

    run_command(
        command="release.show",
        result_type="release",
        json_output=state.json_output,
        produce=produce,
    )


@release_app.command("cancel")
def release_cancel_command(
    ctx: typer.Context,
    version: Annotated[str, typer.Argument(help="Release version to cancel.")],
    reason: Annotated[
        str | None,
        typer.Option("--reason", help="Why the release was canceled."),
    ] = None,
    superseded_by: Annotated[
        str | None,
        typer.Option("--superseded-by", help="Release version that replaces this one."),
    ] = None,
    force_released_unshipped: Annotated[
        bool,
        typer.Option(
            "--force-released-unshipped",
            help="Allow canceling a release currently marked 'released'.",
        ),
    ] = False,
    target_file: Annotated[
        Path | None,
        typer.Option("--target-file", help="Changelog file to update."),
    ] = None,
    remove_changelog_section: Annotated[
        bool,
        typer.Option(
            "--remove-changelog-section",
            help="Remove the release section from the changelog file.",
        ),
    ] = False,
    ignore_missing_section: Annotated[
        bool,
        typer.Option("--ignore-missing", help="Skip a missing changelog section."),
    ] = False,
) -> None:
    """Mark a release as canceled (never shipped)."""
    state = cli_state_from_context(ctx)

    def produce() -> CommandResult:
        result = cancel_release(
            _paths(ctx).workspace_root,
            version=version,
            reason=reason,
            superseded_by=superseded_by,
            force_released_unshipped=force_released_unshipped,
            target_file=target_file,
            remove_changelog_section=remove_changelog_section,
            ignore_missing_section=ignore_missing_section,
        )
        return result, _event_ids(result), f"canceled release {version}"

    run_command(
        command="release.cancel",
        result_type="release",
        json_output=state.json_output,
        produce=produce,
    )


@release_app.command("rename")
def release_rename_command(
    ctx: typer.Context,
    old_version: Annotated[str, typer.Argument(help="Release version to rename.")],
    new_version: Annotated[str, typer.Argument(help="New release version string.")],
    previous_version: Annotated[
        str | None,
        typer.Option(
            "--previous", help="Override previous_version for the renamed release."
        ),
    ] = None,
    title: Annotated[
        str | None,
        typer.Option("--title", help="Override the release title."),
    ] = None,
    released_at: Annotated[
        str | None,
        typer.Option("--released-at", help="Release date YYYY-MM-DD."),
    ] = None,
    force_released_unshipped: Annotated[
        bool,
        typer.Option(
            "--force-released-unshipped",
            help="Allow renaming a release currently marked 'released'.",
        ),
    ] = False,
    rewrite_successors: Annotated[
        bool,
        typer.Option(
            "--rewrite-successors",
            help="Update releases whose previous_version points at the old version.",
        ),
    ] = False,
    target_file: Annotated[
        Path | None,
        typer.Option("--target-file", help="Changelog file to update."),
    ] = None,
    rename_changelog_section: Annotated[
        bool,
        typer.Option(
            "--rename-changelog-section",
            help="Rename the changelog section heading to the new version.",
        ),
    ] = False,
    replace_existing_section: Annotated[
        bool,
        typer.Option(
            "--replace-existing-section",
            help="Overwrite a destination changelog section if it exists.",
        ),
    ] = False,
) -> None:
    """Rename a release and move its bundle to the new version."""
    state = cli_state_from_context(ctx)

    def produce() -> CommandResult:
        result = rename_release(
            _paths(ctx).workspace_root,
            old_version=old_version,
            new_version=new_version,
            previous_version=(
                previous_version if previous_version is not None else UNSET
            ),
            title=title,
            released_at=released_at if released_at is not None else UNSET,
            force_released_unshipped=force_released_unshipped,
            rewrite_successors=rewrite_successors,
            target_file=target_file,
            rename_changelog_section=rename_changelog_section,
            replace_existing_section=replace_existing_section,
        )
        return (
            result,
            _event_ids(result),
            f"renamed release {old_version} to {new_version}",
        )

    run_command(
        command="release.rename",
        result_type="release",
        json_output=state.json_output,
        produce=produce,
    )


chain_app = typer.Typer(help="Inspect and repair the release predecessor chain.")
release_app.add_typer(chain_app, name="chain")


@chain_app.command("check")
def release_chain_check_command(ctx: typer.Context) -> None:
    """Report problems in the release predecessor chain."""
    state = cli_state_from_context(ctx)

    def produce() -> CommandResult:
        result = check_release_chain(_paths(ctx).workspace_root)
        problems = result.get("problems", [])
        if isinstance(problems, list) and problems:
            lines = ["CHAIN PROBLEMS"]
            for problem in problems:
                assert isinstance(problem, dict)
                lines.append(
                    f"{problem.get('version')}  {problem.get('kind')}"
                    f"  -> {problem.get('previous_version')}"
                )
            human = "\n".join(lines)
        else:
            human = "CHAIN OK"
        return result, [], human

    run_command(
        command="release.chain.check",
        result_type="release_chain_check",
        json_output=state.json_output,
        produce=produce,
    )


@chain_app.command("repair")
def release_chain_repair_command(
    ctx: typer.Context,
    apply_changes: Annotated[
        bool,
        typer.Option("--apply", help="Write the computed chain fixes."),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Preview chain fixes without writing."),
    ] = False,
) -> None:
    """Recompute predecessor links from release order (dry-run or --apply)."""
    state = cli_state_from_context(ctx)
    should_apply = apply_changes and not dry_run

    def produce() -> CommandResult:
        result = repair_release_chain(
            _paths(ctx).workspace_root, apply_changes=should_apply
        )
        changes = result.get("changes", [])
        if isinstance(changes, list) and changes:
            lines = ["CHAIN CHANGES" + (" (applied)" if should_apply else " (dry-run)")]
            for change in changes:
                assert isinstance(change, dict)
                lines.append(
                    f"{change.get('version')}  {change.get('from')}"
                    f"  ->  {change.get('to')}"
                )
            human = "\n".join(lines)
        else:
            human = "CHAIN OK (no changes)"
        return result, _event_ids(result), human

    run_command(
        command="release.chain.repair",
        result_type="release_chain_repair",
        json_output=state.json_output,
        produce=produce,
    )


def _event_ids(result: dict[str, object]) -> list[str]:
    events = result.get("events")
    if isinstance(events, list):
        return [str(item) for item in events]
    return []


def _as_int(value: object) -> int:
    """Coerce a result-dict value to int for human/JSON rendering."""
    if isinstance(value, bool) or not isinstance(value, int):
        return int(str(value))
    return value


entry_app = typer.Typer(help="Manage release entries.")
app.add_typer(entry_app, name="entry")


@entry_app.command("add")
def entry_add_command(
    ctx: typer.Context,
    version: Annotated[str, typer.Argument(help="Release version string.")],
    kind: Annotated[str, typer.Option("--kind", help="Entry kind.")] = "added",
    summary: Annotated[
        str,
        typer.Option("--summary", help="One-line change summary."),
    ] = "",
    body: Annotated[
        str | None,
        typer.Option("--body", help="Optional longer entry details."),
    ] = None,
    paths: Annotated[
        list[str] | None,
        typer.Option("--path", help="Relative path affected (repeatable)."),
    ] = None,
    issues: Annotated[
        list[str] | None,
        typer.Option("--issue", help="Issue reference (repeatable)."),
    ] = None,
    prs: Annotated[
        list[str] | None,
        typer.Option("--pr", help="Pull request reference (repeatable)."),
    ] = None,
    sources: Annotated[
        list[str] | None,
        typer.Option("--source", help="Provenance source reference (repeatable)."),
    ] = None,
    status: Annotated[
        str, typer.Option("--status", help="draft|accepted|rejected.")
    ] = "accepted",
    audience: Annotated[str | None, typer.Option("--audience")] = None,
    scopes: Annotated[
        list[str] | None, typer.Option("--scope", help="Entry scope (repeatable).")
    ] = None,
    source_refs: Annotated[
        list[str] | None,
        typer.Option("--source-ref", help="Global source ref (repeatable)."),
    ] = None,
    breaking: Annotated[
        bool,
        typer.Option("--breaking", help="Mark as a breaking change."),
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Validate without writing.")
    ] = False,
    internal: Annotated[
        bool,
        typer.Option("--internal", help="Hide from default changelog output."),
    ] = False,
) -> None:
    """Add a changelog entry to a release."""
    state = cli_state_from_context(ctx)

    def produce() -> CommandResult:
        workspace_root = _paths(ctx).workspace_root
        result = add_release_entry(
            workspace_root,
            release_version=version,
            kind=kind,
            summary=summary,
            body=body,
            paths=tuple(paths or ()),
            issues=tuple(issues or ()),
            prs=tuple(prs or ()),
            sources=tuple(sources or ()),
            status=status,
            audience=audience,
            scopes=tuple(scopes or ()),
            source_refs=tuple(source_refs or ()),
            breaking=breaking,
            internal=internal,
            dry_run=dry_run,
        )
        entry_raw = result.get("entry", {})
        entry = dict(entry_raw) if isinstance(entry_raw, dict) else {}
        entry_id = str(entry.get("entry_id", ""))
        human = (
            f"previewed entry {entry_id} for release {version}"
            if dry_run
            else f"added entry {entry_id} to release {version}"
        )
        return result, _event_ids(result), human

    run_command(
        command="entry.add",
        result_type="release_entry",
        json_output=state.json_output,
        produce=produce,
        workspace_root=_paths(ctx).workspace_root,
        mutating=True,
    )


@entry_app.command("show")
def entry_show_command(
    ctx: typer.Context,
    version: Annotated[str, typer.Argument()],
    entry_id: Annotated[str, typer.Argument()],
) -> None:
    """Show one release entry."""
    state = cli_state_from_context(ctx)

    def produce() -> CommandResult:
        result = show_release_entry(_paths(ctx).workspace_root, version, entry_id)
        entry = result["entry"]
        assert isinstance(entry, dict)
        return result, [], f"{entry_id}  {entry['kind']}  {entry['summary']}"

    run_command(
        command="entry.show",
        result_type="release_entry",
        json_output=state.json_output,
        produce=produce,
    )


@entry_app.command("update")
def entry_update_command(
    ctx: typer.Context,
    version: Annotated[str, typer.Argument()],
    entry_id: Annotated[str, typer.Argument()],
    kind: Annotated[str | None, typer.Option("--kind")] = None,
    summary: Annotated[str | None, typer.Option("--summary")] = None,
    body: Annotated[str | None, typer.Option("--body")] = None,
    status: Annotated[str | None, typer.Option("--status")] = None,
    audience: Annotated[str | None, typer.Option("--audience")] = None,
    scopes: Annotated[list[str] | None, typer.Option("--scope")] = None,
    source_refs: Annotated[list[str] | None, typer.Option("--source-ref")] = None,
    paths: Annotated[list[str] | None, typer.Option("--path")] = None,
    issues: Annotated[list[str] | None, typer.Option("--issue")] = None,
    prs: Annotated[list[str] | None, typer.Option("--pr")] = None,
    breaking: Annotated[bool | None, typer.Option("--breaking/--no-breaking")] = None,
    internal: Annotated[bool | None, typer.Option("--internal/--no-internal")] = None,
) -> None:
    """Update explicitly supplied entry fields."""
    state = cli_state_from_context(ctx)

    def produce() -> CommandResult:
        result = update_release_entry(
            _paths(ctx).workspace_root,
            release_version=version,
            entry_id=entry_id,
            kind=kind,
            summary=summary,
            body=body,
            status=status,
            audience=audience,
            scopes=tuple(scopes) if scopes is not None else None,
            source_refs=(tuple(source_refs) if source_refs is not None else None),
            paths=tuple(paths) if paths is not None else None,
            issues=tuple(issues) if issues is not None else None,
            prs=tuple(prs) if prs is not None else None,
            breaking=breaking,
            internal=internal,
        )
        return result, _event_ids(result), f"updated entry {entry_id}"

    run_command(
        command="entry.update",
        result_type="release_entry",
        json_output=state.json_output,
        produce=produce,
    )


@entry_app.command("import")
def entry_import_command(
    ctx: typer.Context,
    version: Annotated[str, typer.Argument()],
    source_path: Annotated[Path, typer.Option("--file")],
    replace_existing: Annotated[bool, typer.Option("--replace")] = False,
    source_ledger: Annotated[str | None, typer.Option("--source-ledger")] = None,
) -> None:
    """Import a releaseledger or legacy taskledger entry document."""
    state = cli_state_from_context(ctx)

    def produce() -> CommandResult:
        result = import_release_entry_file(
            _paths(ctx).workspace_root,
            release_version=version,
            source_path=source_path,
            replace_existing=replace_existing,
            source_ledger=source_ledger,
        )
        entry = result["entry"]
        assert isinstance(entry, dict)
        entry_id = str(entry["entry_id"])
        return result, _event_ids(result), f"imported entry {entry_id}"

    run_command(
        command="entry.import",
        result_type="release_entry",
        json_output=state.json_output,
        produce=produce,
    )


@entry_app.command("add-many")
def entry_add_many_command(
    ctx: typer.Context,
    version: Annotated[str, typer.Argument()],
    source_path: Annotated[Path, typer.Option("--file")],
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    guard_commit_subjects: Annotated[
        bool,
        typer.Option(
            "--guard-commit-subjects",
            help=(", ").join(
                [
                    "Reject the batch when an entry summary copies or trivially "
                    "transforms a commit subject from the audit sheet / git range.",
                ]
            ),
        ),
    ] = False,
) -> None:
    """Add a validated YAML batch atomically."""
    state = cli_state_from_context(ctx)

    def produce() -> CommandResult:
        entries = load_entry_batch_file(source_path)
        if guard_commit_subjects:
            workspace_root = _paths(ctx).workspace_root
            subjects = collect_commit_subjects(workspace_root, version=version)
            summaries = [str(entry.get("summary", "")) for entry in entries]
            violations = guard_entry_summaries(summaries, subjects)
            if violations:
                raise ReleaseledgerError(
                    "Entry summaries must not copy commit subjects: "
                    + "; ".join(violations),
                    code="VALIDATION_ERROR",
                    exit_code=2,
                )
        result = add_many_release_entries(
            _paths(ctx).workspace_root,
            release_version=version,
            entries=entries,
            dry_run=dry_run,
        )
        issues = result.get("issues")
        if isinstance(issues, list) and issues:
            raise ReleaseledgerError(
                f"Entry batch validation failed with {len(issues)} issue(s).",
                code="VALIDATION_ERROR",
                exit_code=2,
            )
        action = "previewed" if dry_run else "added"
        return (
            result,
            _event_ids(result),
            f"{action} {len(entries)} entries for release {version}",
        )

    run_command(
        command="entry.add-many",
        result_type="release_entry_batch",
        json_output=state.json_output,
        produce=produce,
        workspace_root=_paths(ctx).workspace_root,
        mutating=True,
    )


@entry_app.command("list")
def entry_list_command(
    ctx: typer.Context,
    version: Annotated[str, typer.Argument(help="Release version string.")],
) -> None:
    """List entries for a release."""
    state = cli_state_from_context(ctx)

    def produce() -> CommandResult:
        workspace_root = _paths(ctx).workspace_root
        entries = list_release_entries(workspace_root, version)
        result: dict[str, object] = {
            "kind": "release_entry_list",
            "release_version": version,
            "entries": entries,
        }
        if entries:
            lines = ["ENTRIES"]
            for entry in entries:
                eid = str(entry.get("entry_id", ""))
                k = str(entry.get("kind", ""))
                s = str(entry.get("summary", ""))
                lines.append(f"{eid}  {k}  {s}")
            human = "\n".join(lines)
        else:
            human = "ENTRIES\n(none)"
        return result, [], human

    run_command(
        command="entry.list",
        result_type="release_entry_list",
        json_output=state.json_output,
        produce=produce,
    )


@entry_app.command("lint")
def entry_lint_command(
    ctx: typer.Context,
    version: Annotated[str, typer.Argument()],
    strict: Annotated[bool, typer.Option("--strict")] = False,
    include_statuses: Annotated[
        list[str] | None, typer.Option("--include-status")
    ] = None,
) -> None:
    """Lint release entries and optionally fail on warnings."""
    state = cli_state_from_context(ctx)

    def produce() -> CommandResult:
        result = lint_release_entries(
            _paths(ctx).workspace_root,
            release_version=version,
            strict=strict,
            include_statuses=(
                tuple(include_statuses) if include_statuses is not None else None
            ),
        )
        if not result["passed"]:
            summary = result["summary"]
            assert isinstance(summary, dict)
            raise ReleaseledgerError(
                f"Entry lint failed with {summary['errors']} error(s) and "
                f"{summary['warnings']} warning(s).",
                code="VALIDATION_ERROR",
                exit_code=2,
            )
        summary = result["summary"]
        assert isinstance(summary, dict)
        human = (
            f"entry lint passed: {summary['errors']} error(s), "
            f"{summary['warnings']} warning(s)"
        )
        return result, [], human

    run_command(
        command="entry.lint",
        result_type="entry_lint",
        json_output=state.json_output,
        produce=produce,
    )


@entry_app.command("prompt")
def entry_prompt_command(
    ctx: typer.Context,
    version: Annotated[str, typer.Argument()],
    source_refs: Annotated[list[str] | None, typer.Option("--source-ref")] = None,
    context_file: Annotated[Path | None, typer.Option("--context-file")] = None,
    format_name: Annotated[str, typer.Option("--format")] = "markdown",
    output: Annotated[Path | None, typer.Option("--output")] = None,
) -> None:
    """Render a prompt for drafting release entries."""
    state = cli_state_from_context(ctx)
    try:
        result = build_entry_prompt(
            _paths(ctx).workspace_root,
            release_version=version,
            source_refs=tuple(source_refs or ()),
            context_file=context_file,
            format_name=format_name,
        )
    except ReleaseledgerError as exc:
        emit_error(command="entry.prompt", error=exc, json_output=state.json_output)
        raise typer.Exit(launch_error_exit_code(exc)) from exc
    text = render_json(result) if isinstance(result, dict) else result
    if output is not None:
        target = write_text_output(output, text)
        if state.json_output:
            typer.echo(
                render_json(
                    {
                        "ok": True,
                        "command": "entry.prompt",
                        "result_type": "entry_prompt",
                        "result": {"output": str(target), "format": format_name},
                    }
                )
            )
        else:
            typer.echo(f"wrote {target}")
        return
    typer.echo(text)


@app.command("changelog")
def changelog_command(
    ctx: typer.Context,
    version: Annotated[str, typer.Argument(help="Release version string.")],
    format_name: Annotated[
        str,
        typer.Option("--format", help="Output format: markdown or json."),
    ] = "markdown",
    output: Annotated[
        Path | None,
        typer.Option("--output", help="Write rendered content to PATH."),
    ] = None,
    include_internal: Annotated[
        bool,
        typer.Option("--include-internal", help="Include internal entries."),
    ] = False,
    target_changelog: Annotated[
        str | None,
        typer.Option("--target-changelog", help="Target changelog file."),
    ] = None,
    release_date: Annotated[
        str | None,
        typer.Option("--release-date", help="Release date YYYY-MM-DD."),
    ] = None,
    include_sources: Annotated[
        bool,
        typer.Option(
            "--include-sources", help="Show provenance sources in markdown output."
        ),
    ] = False,
    include_statuses: Annotated[
        list[str] | None, typer.Option("--include-status")
    ] = None,
    lint: Annotated[bool, typer.Option("--lint")] = False,
) -> None:
    """Render changelog context for a release."""
    state = cli_state_from_context(ctx)
    if format_name not in {"markdown", "json"}:
        err = ReleaseledgerError(
            f"Unsupported --format: {format_name!r}",
            code="USAGE_ERROR",
            exit_code=2,
        )
        emit_error(command="changelog", error=err, json_output=state.json_output)
        raise typer.Exit(launch_error_exit_code(err)) from err
    try:
        workspace_root = _paths(ctx).workspace_root
        content = build_changelog_context(
            workspace_root,
            version=version,
            format_name=format_name,
            include_internal=include_internal,
            include_sources=include_sources,
            target_changelog=target_changelog,
            release_date=release_date,
            include_statuses=tuple(include_statuses or ("accepted",)),
            lint=lint,
        )
    except ReleaseledgerError as exc:
        emit_error(command="changelog", error=exc, json_output=state.json_output)
        raise typer.Exit(launch_error_exit_code(exc)) from exc
    if format_name == "json":
        text = render_json(content) if isinstance(content, dict) else str(content)
    else:
        text = content if isinstance(content, str) else render_json(content)
    if output is not None:
        out_path = write_text_output(output, text)
        if state.json_output:
            payload: dict[str, object] = {
                "ok": True,
                "command": "changelog",
                "result_type": "changelog",
                "result": {"output": str(out_path), "format": format_name},
            }
            typer.echo(render_json(payload))
        else:
            typer.echo(f"wrote {out_path}")
        return
    typer.echo(text)


def _format_coverage_row(row: dict[str, object]) -> str:
    ref = str(row.get("source_ref", ""))
    label = str(row.get("status", ""))
    accepted = row.get("accepted_entry_ids", [])
    entries_text = ""
    if isinstance(accepted, list) and accepted:
        entries_text = " -> " + ", ".join(str(e) for e in accepted)
    elif label in {"draft_only", "rejected_only", "internal_only"}:
        ids = row.get("entry_ids", [])
        if isinstance(ids, list) and ids:
            entries_text = " -> " + ", ".join(str(e) for e in ids)
    return f"  {label:<14} {ref}{entries_text}"


@app.command("review")
def review_command(
    ctx: typer.Context,
    version: Annotated[str, typer.Argument(help="Release version string.")],
    include_internal: Annotated[
        bool,
        typer.Option(
            "--include-internal",
            help="Include internal entries in coverage and the dry-run build.",
        ),
    ] = False,
    include_statuses: Annotated[
        list[str] | None,
        typer.Option("--include-status", help="Included entry statuses."),
    ] = None,
    target_file: Annotated[
        Path | None,
        typer.Option("--target-file", help="CHANGELOG target file for the dry-run."),
    ] = None,
    strict: Annotated[
        bool,
        typer.Option("--strict", help="Exit non-zero when the release is not OK."),
    ] = False,
    git: Annotated[
        bool,
        typer.Option(
            "--git",
            help="Enable git-backed coverage review.",
        ),
    ] = False,
    git_base: Annotated[
        str | None,
        typer.Option(
            "--git-base",
            help="Git range base ref for the review.",
        ),
    ] = None,
    git_head: Annotated[
        str | None,
        typer.Option(
            "--git-head",
            help="Git range head ref for the review.",
        ),
    ] = None,
    require_audit_sheet: Annotated[
        bool,
        typer.Option(
            "--require-audit-sheet",
            help="Require a commit audit sheet; gate when absent or incomplete.",
        ),
    ] = False,
) -> None:
    """Review release coverage, orphans, lint, and a strict changelog dry-run."""
    state = cli_state_from_context(ctx)
    statuses = tuple(include_statuses) if include_statuses is not None else None
    try:
        result = build_release_review(
            _paths(ctx).workspace_root,
            version=version,
            include_internal=include_internal,
            include_statuses=statuses or ("accepted",),
            target_file=target_file,
            strict=strict,
            git=git,
            git_base=git_base,
            git_head=git_head,
            require_audit_sheet=require_audit_sheet,
        )
    except ReleaseledgerError as exc:
        emit_error(command="review", error=exc, json_output=state.json_output)
        raise typer.Exit(launch_error_exit_code(exc)) from exc

    ok = bool(result.get("ok", False))
    if state.json_output:
        payload: dict[str, object] = {
            "ok": ok,
            "command": "review",
            "result_type": "release_review",
            "result": result,
        }
        typer.echo(render_json(payload))
        if strict and not ok:
            raise typer.Exit(2)
        return
    typer.echo(_render_review_human(version, result))
    if strict and not ok:
        raise typer.Exit(2)


def _render_review_human(version: str, result: dict[str, object]) -> str:
    release_block = result.get("release", {})
    release_dict = release_block if isinstance(release_block, dict) else {}
    lines = [f"RELEASE REVIEW {version}", ""]
    lines.append("Release:")
    lines.append(f"  status: {release_dict.get('status', '')}")
    if release_dict.get("previous_version"):
        lines.append(f"  previous_version: {release_dict['previous_version']}")
    if release_dict.get("changelog_file"):
        lines.append(f"  changelog_file: {release_dict['changelog_file']}")
    source_refs = release_dict.get("source_refs", [])
    if isinstance(source_refs, list) and source_refs:
        lines.append("  source_refs: " + ", ".join(str(r) for r in source_refs))
    if release_dict.get("boundary_ref"):
        lines.append(f"  boundary_ref: {release_dict['boundary_ref']}")

    coverage = result.get("coverage", [])
    if isinstance(coverage, list):
        lines.append("")
        lines.append("Coverage:")
        if coverage:
            for row in coverage:
                assert isinstance(row, dict)
                lines.append(_format_coverage_row(row))
        else:
            lines.append("  (no expected source refs)")

    counts = result.get("entry_counts", {})
    if isinstance(counts, dict):
        lines.append("")
        lines.append("Entries:")
        lines.append(f"  accepted: {counts.get('accepted', 0)}")
        lines.append(f"  draft: {counts.get('draft', 0)}")
        lines.append(f"  rejected: {counts.get('rejected', 0)}")
        hidden = counts.get("internal", 0)
        if hidden:
            lines.append(f"  internal: {hidden}")

    lint = result.get("lint", {})
    lint_errors = 0
    lint_warnings = 0
    if isinstance(lint, dict):
        lint_errors = int(lint.get("errors", 0))
        lint_warnings = int(lint.get("warnings", 0))
    lines.append("")
    lines.append("Strict checks:")
    checks = result.get("checks", {})
    coverage_ok = checks.get("coverage_ok") if isinstance(checks, dict) else None
    changelog_ok = checks.get("changelog_ok") if isinstance(checks, dict) else None
    coverage_label = "OK" if coverage_ok else "FAIL"
    if not coverage:
        coverage_label = "OK"
    changelog_block = result.get("changelog", {})
    changelog_dict = changelog_block if isinstance(changelog_block, dict) else {}
    changelog_status = "OK" if changelog_ok else "FAIL"
    reason = changelog_dict.get("reason")
    reason_text = f": {reason}" if reason else ""
    lines.append(f"  {coverage_label:<4} release source refs coverage")
    lines.append(
        f"  {'OK' if lint_errors == 0 else 'FAIL':<4} entry lint "
        f"({lint_errors} error(s), {lint_warnings} warning(s))"
    )
    lines.append(f"  {changelog_status:<4} changelog dry-run build{reason_text}")

    # Git block (when present).
    git_block = result.get("git")
    if isinstance(git_block, dict):
        git_cov_ok = isinstance(checks, dict) and checks.get("git_coverage_ok", True)
        lines.append(f"  {'OK' if git_cov_ok else 'FAIL':<4} git commit coverage")
        lines.append("")
        lines.append("Git:")
        base_sha = str(git_block.get("base_sha", ""))[:7]
        head_sha = str(git_block.get("head_sha", ""))[:7]
        lines.append(f"  base: {git_block.get('base_ref', '')} -> {base_sha}")
        lines.append(f"  head: {git_block.get('head_ref', '')} -> {head_sha}")
        lines.append(f"  range: {str(git_block.get('range', ''))[:21]}")
        lines.append(f"  commits: {git_block.get('commit_count', 0)}")
        skipped = int(git_block.get("merge_commits_skipped", 0))
        if skipped:
            lines.append(f"  merge commits skipped: {skipped}")

    audit_block = result.get("audit")
    if isinstance(audit_block, dict):
        lines.append("")
        lines.append("Audit:")
        lines.append(f"  rows: {audit_block.get('row_count', 0)}")
        lines.append(f"  needs review: {audit_block.get('needs_review_count', 0)}")
        lines.append(f"  uninspected: {audit_block.get('uninspected_count', 0)}")
        lines.append(f"  ok: {audit_block.get('ok')}")

    orphans = result.get("orphan_entries", [])
    if isinstance(orphans, list) and orphans:
        lines.append("")
        lines.append("Orphan entries:")
        for orphan in orphans:
            assert isinstance(orphan, dict)
            lines.append(f"  {orphan.get('entry_id')} {orphan.get('reason')}")

    recommendations = result.get("recommendations", [])
    if isinstance(recommendations, list) and recommendations:
        lines.append("")
        lines.append("Recommendations:")
        for rec in recommendations:
            lines.append(f"  - {rec}")

    lines.append("")
    lines.append(f"Result: {'OK' if result.get('ok') else 'FAIL'}")
    return "\n".join(lines)


@app.command("build")
def build_command(
    ctx: typer.Context,
    version: Annotated[
        str | None,
        typer.Argument(help="Release version string (omit for full rebuild)."),
    ] = None,
    target_file: Annotated[
        Path | None,
        typer.Option("--target-file", help="CHANGELOG target file."),
    ] = None,
    release_date: Annotated[
        str | None,
        typer.Option("--release-date", help="Release date YYYY-MM-DD."),
    ] = None,
    unreleased: Annotated[
        bool,
        typer.Option("--unreleased", help="Render the date as Unreleased/no date."),
    ] = False,
    include_internal: Annotated[
        bool,
        typer.Option("--include-internal", help="Include internal entries."),
    ] = False,
    template: Annotated[
        str,
        typer.Option("--template", help="Named template profile."),
    ] = "default",
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Print rendered output; do not write."),
    ] = False,
    replace_existing: Annotated[
        bool,
        typer.Option(
            "--replace-existing",
            help="Replace an existing section for VERSION (single-section only).",
        ),
    ] = False,
    all_releases: Annotated[
        bool,
        typer.Option("--all", help="Rebuild the full changelog file."),
    ] = False,
    include_release_statuses: Annotated[
        list[str] | None,
        typer.Option(
            "--include-release-status",
            help="Release status to include (full build).",
        ),
    ] = None,
    preserve_unreleased: Annotated[
        bool,
        typer.Option(
            "--preserve-unreleased/--no-preserve-unreleased",
            help="Preserve the existing Unreleased body (full build).",
        ),
    ] = True,
    format_name: Annotated[
        str,
        typer.Option("--format", help="Output format: markdown or json."),
    ] = "markdown",
    include_statuses: Annotated[
        list[str] | None, typer.Option("--include-status")
    ] = None,
    strict: Annotated[bool, typer.Option("--strict")] = False,
    allow_empty: Annotated[bool, typer.Option("--allow-empty")] = False,
) -> None:
    """Build or rebuild CHANGELOG.md.

    With VERSION (and no --all), update one release section. With no VERSION or
    --all, rebuild the complete target file from ledger state.
    """
    state = cli_state_from_context(ctx)
    if format_name not in {"markdown", "json"}:
        err = ReleaseledgerError(
            f"Unsupported --format: {format_name!r}",
            code="USAGE_ERROR",
            exit_code=2,
        )
        emit_error(command="build", error=err, json_output=state.json_output)
        raise typer.Exit(launch_error_exit_code(err)) from err
    full_build = all_releases or version is None
    if all_releases and version is not None:
        err = ReleaseledgerError(
            "--all cannot be combined with a VERSION argument.",
            code="USAGE_ERROR",
            exit_code=2,
            remediation=[
                "Use `releaseledger build --all` for a full rebuild, or"
                "`releaseledger build VERSION` for one section.",
            ],
        )
        emit_error(command="build", error=err, json_output=state.json_output)
        raise typer.Exit(launch_error_exit_code(err)) from err
    try:
        workspace_root = _paths(ctx).workspace_root
        if full_build:
            result = build_full_changelog_file(
                workspace_root,
                target_file=target_file,
                include_internal=include_internal,
                template_name=template,
                dry_run=dry_run,
                include_statuses=tuple(include_statuses or ("accepted",)),
                include_release_statuses=tuple(
                    include_release_statuses or ("released",)
                ),
                strict=strict,
                allow_empty=allow_empty,
                preserve_unreleased=preserve_unreleased,
            )
        else:
            assert version is not None
            result = build_changelog_file(
                workspace_root,
                version=version,
                target_file=target_file,
                include_internal=include_internal,
                release_date=release_date,
                unreleased=unreleased,
                template_name=template,
                dry_run=dry_run,
                replace_existing=replace_existing,
                include_statuses=tuple(include_statuses or ("accepted",)),
                strict=strict,
                allow_empty=allow_empty,
            )
    except ReleaseledgerError as exc:
        emit_error(command="build", error=exc, json_output=state.json_output)
        raise typer.Exit(launch_error_exit_code(exc)) from exc
    target = str(result.get("target_file", ""))
    if full_build:
        if dry_run:
            human = str(result.get("document", ""))
        else:
            release_count = _as_int(result.get("release_count", 0))
            human = f"wrote {target} ({release_count} release sections)"
        result_type = "changelog_full_build"
    else:
        if dry_run:
            human = str(result.get("section", ""))
        else:
            human = f"wrote {target}"
        result_type = "changelog_build"
    emit_payload(
        command="build",
        result_type=result_type,
        result=result,
        human=human,
        json_output=state.json_output,
    )


changelog_section_app = typer.Typer(
    help="Correct release sections in an existing changelog file."
)
app.add_typer(changelog_section_app, name="changelog-section")


@changelog_section_app.command("remove-section")
def changelog_remove_section_command(
    ctx: typer.Context,
    version: Annotated[str, typer.Argument(help="Release section to remove.")],
    target_file: Annotated[
        Path,
        typer.Option("--target-file", help="Changelog file to update."),
    ],
    ignore_missing: Annotated[
        bool,
        typer.Option("--ignore-missing", help="Skip a missing section."),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Preview without writing."),
    ] = False,
) -> None:
    """Remove a release section from a changelog file."""
    state = cli_state_from_context(ctx)

    def produce() -> CommandResult:
        result = remove_changelog_section(
            _paths(ctx).workspace_root,
            version=version,
            target_file=target_file,
            ignore_missing=ignore_missing,
            dry_run=dry_run,
        )
        human = (
            f"previewed removal of section {version}"
            if dry_run
            else f"removed section {version}"
        )
        return result, [], human

    run_command(
        command="changelog-section.remove",
        result_type="changelog_section_remove",
        json_output=state.json_output,
        produce=produce,
    )


@changelog_section_app.command("rename-section")
def changelog_rename_section_command(
    ctx: typer.Context,
    old_version: Annotated[str, typer.Argument(help="Section version to rename.")],
    new_version: Annotated[str, typer.Argument(help="New section version.")],
    target_file: Annotated[
        Path,
        typer.Option("--target-file", help="Changelog file to update."),
    ],
    ignore_missing: Annotated[
        bool,
        typer.Option("--ignore-missing", help="Skip a missing source section."),
    ] = False,
    replace_existing: Annotated[
        bool,
        typer.Option(
            "--replace-existing", help="Overwrite an existing destination section."
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Preview without writing."),
    ] = False,
) -> None:
    """Rename a release section heading in a changelog file."""
    state = cli_state_from_context(ctx)

    def produce() -> CommandResult:
        result = rename_changelog_section(
            _paths(ctx).workspace_root,
            old_version=old_version,
            new_version=new_version,
            target_file=target_file,
            ignore_missing=ignore_missing,
            replace_existing=replace_existing,
            dry_run=dry_run,
        )
        human = (
            f"previewed rename of section {old_version} to {new_version}"
            if dry_run
            else f"renamed section {old_version} to {new_version}"
        )
        return result, [], human

    run_command(
        command="changelog-section.rename",
        result_type="changelog_section_rename",
        json_output=state.json_output,
        produce=produce,
    )


# -- Git-first release evidence commands (design §7) -------------------

git_app = typer.Typer(
    help="Git-first release evidence: range scanning and candidate import."
)
app.add_typer(git_app, name="git")


@git_app.command("range")
def git_range_command(
    ctx: typer.Context,
    version: Annotated[
        str,
        typer.Argument(
            help="Release version (or 'next' for a non-persisting preview)."
        ),
    ],
    base: Annotated[
        str,
        typer.Option("--base", help="Base ref (e.g. v0.1.0); resolved to a full SHA."),
    ] = "",
    head: Annotated[
        str,
        typer.Option("--head", help="Head ref (default HEAD); resolved to a full SHA."),
    ] = GIT_DEFAULT_HEAD,
    include_merges: Annotated[
        str,
        typer.Option(
            "--include-merges",
            help="Merge policy: never, always, nontrivial (default nontrivial).",
        ),
    ] = GIT_DEFAULT_INCLUDE_MERGES,
    evidence: Annotated[
        bool,
        typer.Option(
            "--evidence",
            help="Emit per-commit evidence (paths, additions, deletions, refs, diff).",
        ),
    ] = False,
) -> None:
    """Inspect the git commit range for a release (or preview with 'next').

    With a real version the stored release's git range is used when --base/--head
    are not supplied. With the special version 'next' the refs must be provided.
    No release record is written.
    """
    state = cli_state_from_context(ctx)
    workspace_root = _paths(ctx).workspace_root

    if version == "next":
        if not base:
            emit_error(
                command="git.range",
                error=LaunchError(
                    "--base is required for 'git range next'.",
                    code=CODE_USAGE_ERROR,
                    exit_code=2,
                ),
                json_output=state.json_output,
            )
            raise typer.Exit(2)
        _run_git_range(
            state,
            workspace_root,
            display_version="next",
            base=base,
            head=head,
            include_merges=include_merges,
            evidence=evidence,
        )
        return

    # Real release: use stored git_* fields when --base/--head not supplied.
    existing = load_release(workspace_root, version)
    use_base = base or existing.git_base_ref
    use_head = head or existing.git_head_ref
    if not use_base:
        emit_error(
            command="git.range",
            error=LaunchError(
                f"Release {version} has no stored git base."
                " Pass --base or use 'release update --git-base'.",
                code=CODE_USAGE_ERROR,
                exit_code=2,
            ),
            json_output=state.json_output,
        )
        raise typer.Exit(2)
    _run_git_range(
        state,
        workspace_root,
        display_version=version,
        base=use_base,
        head=use_head or GIT_DEFAULT_HEAD,
        include_merges=include_merges,
        evidence=evidence,
    )


def _candidate_payload(c: GitSourceCandidate, *, evidence: bool) -> dict[str, object]:
    """Build a git-range candidate dict, optionally with full evidence."""
    payload: dict[str, object] = {
        "sha": c.sha,
        "short_sha": c.short_sha,
        "source_ref": c.source_ref,
        "inferred_kind": c.inferred_kind,
        "subject": c.subject,
    }
    if not evidence:
        return payload
    payload["paths"] = list(c.paths)
    payload["additions"] = c.additions
    payload["deletions"] = c.deletions
    payload["pr_refs"] = list(c.pr_refs)
    payload["issue_refs"] = list(c.issue_refs)
    payload["diff_excerpt"] = c.diff_excerpt
    return payload


def _run_git_range(
    state: CLIState,
    workspace_root: Path,
    *,
    display_version: str,
    base: str,
    head: str,
    include_merges: str,
    evidence: bool = False,
) -> None:
    """Render a git range scan (human + JSON)."""
    try:
        candidates = collect_git_candidates(
            workspace_root,
            base_ref=base,
            head_ref=head,
            include_merges=include_merges,
        )
        base_sha = resolve_git_ref(workspace_root, base)
        head_sha = resolve_git_ref(workspace_root, head)
    except LaunchError as exc:
        emit_error(command="git.range", error=exc, json_output=state.json_output)
        raise typer.Exit(launch_error_exit_code(exc)) from exc

    skipped = sum(
        1
        for _ in collect_git_candidates(
            workspace_root,
            base_ref=base,
            head_ref=head,
            include_merges="always",
        )
    ) - len(candidates)
    if skipped < 0:
        skipped = 0

    result: dict[str, object] = {
        "kind": "git_range",
        "version": display_version,
        "base_ref": base,
        "base_sha": base_sha,
        "head_ref": head,
        "head_sha": head_sha,
        "range": f"{base_sha}..{head_sha}",
        "commit_count": len(candidates) + skipped,
        "merge_commits_skipped": skipped,
        "candidate_count": len(candidates),
        "include_merges": include_merges,
        "candidates": [_candidate_payload(c, evidence=evidence) for c in candidates],
    }
    if state.json_output:
        payload: dict[str, object] = {
            "ok": True,
            "command": "git.range",
            "result_type": "git_range",
            "result": result,
        }
        typer.echo(render_json(payload))
        return

    lines = [f"GIT RANGE {display_version}", ""]
    lines.append(f"  base: {base} -> {base_sha[:7]}")
    lines.append(f"  head: {head} -> {head_sha[:7]}")
    lines.append(f"  commits: {len(candidates) + skipped}")
    if skipped:
        lines.append(f"  merge commits skipped: {skipped}")
    lines.append("")
    lines.append("Candidates:")
    for c in candidates:
        lines.append(f"  {c.source_ref:<52} {c.inferred_kind:<12} {c.subject[:72]}")
        if evidence:
            paths_line = ", ".join(c.paths[:6]) + ("  ..." if len(c.paths) > 6 else "")
            lines.append(f"    paths: {len(c.paths)}  {paths_line}")
            add_del = ""
            if c.additions is not None or c.deletions is not None:
                add_del = f"  +{c.additions or 0}/-{c.deletions or 0}"
            refs: list[str] = []
            refs.extend(f"pr:{ref}" for ref in c.pr_refs)
            refs.extend(f"issue:{ref}" for ref in c.issue_refs)
            tail = add_del
            if refs:
                tail += ("  " if add_del else "") + " ".join(refs)
            if tail:
                lines.append(f"    evidence:{tail}")
            if c.diff_excerpt:
                excerpt = c.diff_excerpt.replace("\n", " ")[:120]
                lines.append(f"    diff: {excerpt}")
    typer.echo("\n".join(lines))


@git_app.command("import")
def git_import_command(
    ctx: typer.Context,
    version: Annotated[
        str,
        typer.Argument(
            help="Release version (or 'next' for a non-persisting preview)."
        ),
    ],
    output: Annotated[
        Path,
        typer.Option(
            "--output",
            help="Output YAML file path for the entry batch.",
        ),
    ],
    base: Annotated[
        str,
        typer.Option("--base", help="Base ref (e.g. v0.1.0)."),
    ] = "",
    head: Annotated[
        str,
        typer.Option("--head", help="Head ref (default HEAD)."),
    ] = GIT_DEFAULT_HEAD,
    include_merges: Annotated[
        str,
        typer.Option(
            "--include-merges",
            help="Merge policy: never, always, nontrivial (default nontrivial).",
        ),
    ] = GIT_DEFAULT_INCLUDE_MERGES,
    status: Annotated[
        str,
        typer.Option(
            "--status",
            help="Status for generated entries (default draft).",
        ),
    ] = "draft",
) -> None:
    """Generate an entry batch YAML from the git commit range.

    With a real version the stored release's git range is used when --base/--head
    are not supplied. With 'next' the refs must be provided and no release is
    read or written.

    The output YAML is intended for review and manual curation before running
    ``releaseledger entry add-many VERSION --file FILE``.
    """
    state = cli_state_from_context(ctx)
    workspace_root = _paths(ctx).workspace_root

    if version == "next":
        if not base:
            emit_error(
                command="git.import",
                error=LaunchError(
                    "--base is required for 'git import next'.",
                    code=CODE_USAGE_ERROR,
                    exit_code=2,
                ),
                json_output=state.json_output,
            )
            raise typer.Exit(2)
    else:
        existing = load_release(workspace_root, version)
        if not base:
            base = existing.git_base_ref or ""
        if not base:
            emit_error(
                command="git.import",
                error=LaunchError(
                    f"Release {version} has no stored git base."
                    " Pass --base or 'release update --git-base'.",
                    code=CODE_USAGE_ERROR,
                    exit_code=2,
                ),
                json_output=state.json_output,
            )
            raise typer.Exit(2)

    try:
        candidates = collect_git_candidates(
            workspace_root,
            base_ref=base,
            head_ref=head,
            include_merges=include_merges,
        )
    except LaunchError as exc:
        emit_error(command="git.import", error=exc, json_output=state.json_output)
        raise typer.Exit(launch_error_exit_code(exc)) from exc

    yaml_entries: list[dict[str, object]] = []
    for c in candidates:
        entry: dict[str, object] = {
            "kind": c.inferred_kind,
            # Summary is intentionally blank. `git import` is a coverage
            # scaffold; changelog prose must be authored from reviewed evidence,
            # not copied or inferred from commit subjects.
            "summary": "",
            "status": status,
            "source_refs": [c.source_ref],
            "paths": list(c.paths),
            "sources": [c.source_ref],
        }
        yaml_entries.append(entry)
    batch: dict[str, object] = {"entries": yaml_entries}

    # Write the YAML file.
    try:
        import yaml as _yaml

        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8") as f:
            _yaml.dump(batch, f, default_flow_style=False, sort_keys=False)
    except Exception as exc:
        emit_error(
            command="git.import",
            error=LaunchError(
                f"Failed to write output file {output}: {exc}",
                code=CODE_USAGE_ERROR,
                exit_code=2,
            ),
            json_output=state.json_output,
        )
        raise typer.Exit(2) from exc

    result: dict[str, object] = {
        "kind": "git_import",
        "version": version,
        "output": str(output),
        "entry_count": len(yaml_entries),
        "status": status,
        "entries": yaml_entries,
    }
    if state.json_output:
        payload: dict[str, object] = {
            "ok": True,
            "command": "git.import",
            "result_type": "git_import",
            "result": result,
        }
        typer.echo(render_json(payload))
        return

    lines = [f"GIT IMPORT {version}", ""]
    lines.append(f"  output: {output}")
    lines.append(f"  entries: {len(yaml_entries)} (status={status})")
    lines.append("")
    lines.append("Next steps:")
    lines.append(
        "  This is an entry scaffold, not changelog prose. For a durable "
        "review worksheet run:"
    )
    lines.append(f"  releaseledger audit init {version} --base {base} --head {head}")
    lines.append(
        "  edit the YAML and write user-facing summaries from diffs/docs/tests"
    )
    lines.append("  do not copy or paraphrase git commit messages into summaries")
    lines.append(f"  releaseledger entry add-many {version} --file {output} --dry-run")
    lines.append(f"  releaseledger entry add-many {version} --file {output}")
    typer.echo("\n".join(lines))


# --- end git_app ---

# -- Branch ledger commands (Phase 5, design §9) -------------------------

branch_app = typer.Typer(
    help="Branch-scoped release ledger operations (optional, advanced)."
)
app.add_typer(branch_app, name="branch")


@branch_app.command("status")
def branch_status_command(ctx: typer.Context) -> None:
    """Show the current git branch vs the configured ledger_ref."""
    state = cli_state_from_context(ctx)

    def produce() -> CommandResult:
        workspace_root = _paths(ctx).workspace_root
        config = load_project_config(workspace_root / ".releaseledger.toml")
        result = branch_status(
            workspace_root,
            ledger_ref=config.ledger_ref,
            branch_guard=config.ledger_branch_guard,
        )
        lines = ["BRANCH STATUS", ""]
        lines.append(
            f"  current git branch: {result['current_git_branch'] or '(none)'}"
        )
        lines.append(f"  ledger_ref: {result['ledger_ref']}")
        lines.append(f"  branch_guard: {result['branch_guard']}")
        match = result["match"]
        if match is None:
            lines.append("  match: (not in git)")
        else:
            lines.append(f"  match: {'yes' if match else 'no'}")
        human = "\n".join(lines)
        return result, [], human

    run_command(
        command="branch.status",
        result_type="branch_status",
        json_output=state.json_output,
        produce=produce,
    )


@branch_app.command("start")
def branch_start_command(
    ctx: typer.Context,
    branch: Annotated[str, typer.Argument(help="New branch ledger ref.")],
    parent: Annotated[
        str,
        typer.Option("--parent", help="Parent ledger ref to fork from."),
    ],
) -> None:
    """Start a new branch ledger forked from a parent."""
    state = cli_state_from_context(ctx)

    def produce() -> CommandResult:
        workspace_root = _paths(ctx).workspace_root
        config = load_project_config(workspace_root / ".releaseledger.toml")
        result = branch_start(
            workspace_root,
            branch_ref=branch,
            parent_ref=parent,
            current_ledger_ref=config.ledger_ref,
        )
        return result, [], f"started branch ledger {branch} from {parent}"

    run_command(
        command="branch.start",
        result_type="branch_start",
        json_output=state.json_output,
        produce=produce,
    )


@branch_app.command("merge")
def branch_merge_command(
    ctx: typer.Context,
    branch: Annotated[str, typer.Argument(help="Branch ledger ref to merge from.")],
    into: Annotated[
        str,
        typer.Option("--into", help="Target ledger ref to merge into."),
    ],
    release: Annotated[
        str,
        typer.Option("--release", help="Release version to merge entries for."),
    ],
) -> None:
    """Merge branch entries into a target ledger by source_refs."""
    state = cli_state_from_context(ctx)

    def produce() -> CommandResult:
        workspace_root = _paths(ctx).workspace_root
        result = branch_merge(
            workspace_root,
            branch_ref=branch,
            into_ref=into,
            release_version=release,
        )
        added = result.get("merged_count", 0)
        human = f"merged {added} entry/entries from {branch} into {into}"
        warnings = result.get("warnings", [])
        if isinstance(warnings, list) and warnings:
            human += "\n" + "\n".join(f"  warning: {w}" for w in warnings)
        return result, [], human

    run_command(
        command="branch.merge",
        result_type="branch_merge",
        json_output=state.json_output,
        produce=produce,
    )


storage_app = typer.Typer(help="Storage diagnostics.")
app.add_typer(storage_app, name="storage")


@storage_app.command("where")
def storage_where_command(ctx: typer.Context) -> None:
    """Show the effective storage location, layout health, and config source."""
    state = cli_state_from_context(ctx)

    def produce() -> CommandResult:
        result = storage_where(state.cwd)
        # Build human output
        inside = "yes" if result.get("inside_workspace") else "no"
        layout = "ok" if result.get("layout_exists") else "missing"
        indexes = "ok" if result.get("indexes_exist") else "missing"
        lines = [
            f"Workspace: {result.get('workspace_root', '')}",
            f"Config: {result.get('config_path', '')}",
            f"Storage: {result.get('releaseledger_dir', '')}",
            f"Ledger: {result.get('ledger_ref', '')}",
            f"Inside workspace: {inside}",
            f"Source: {result.get('source', '')}",
            f"Layout: {layout}",
            f"Indexes: {indexes}",
        ]
        human = "\n".join(lines)
        return result, [], human

    run_command(
        command="storage.where",
        result_type="storage_location",
        json_output=state.json_output,
        produce=produce,
    )


config_app = typer.Typer(help="Config management.")
app.add_typer(config_app, name="config")


@config_app.command("show")
def config_show_command(ctx: typer.Context) -> None:
    """Show the validated project configuration and resolved paths."""
    state = cli_state_from_context(ctx)

    def produce() -> CommandResult:
        result = config_show(state.cwd)
        cfg = result.get("config", {})
        if not isinstance(cfg, dict):
            cfg = {}
        lines = [
            f"Workspace: {result.get('workspace_root', '')}",
            f"Config: {result.get('config_path', '')}",
            f"Storage: {result.get('releaseledger_dir', '')}",
            f"Policy: {cfg.get('releaseledger_dir_policy', 'workspace')}",
            f"Ledger ref: {cfg.get('ledger_ref', '')}",
        ]
        human = "\n".join(lines)
        return result, [], human

    run_command(
        command="config.show",
        result_type="config_show",
        json_output=state.json_output,
        produce=produce,
    )


@config_app.command("set")
def config_set_command(
    ctx: typer.Context,
    key: Annotated[str, typer.Argument(help="Config key to set.")],
    value: Annotated[str, typer.Argument(help="New value.")],
    external_dir: Annotated[
        bool,
        typer.Option(
            "--external-dir",
            help="Allow releaseledger_dir to resolve outside the workspace.",
        ),
    ] = False,
) -> None:
    """Atomically set a config key in .releaseledger.toml."""
    state = cli_state_from_context(ctx)
    if key != "releaseledger_dir":
        err = ReleaseledgerError(
            f"Unsupported config key: {key!r}."
            " Only 'releaseledger_dir' is currently supported.",
            code="USAGE_ERROR",
            exit_code=2,
        )
        emit_error(command="config.set", error=err, json_output=state.json_output)
        raise typer.Exit(launch_error_exit_code(err)) from err

    def produce() -> CommandResult:
        result = config_set_releaseledger_dir(
            state.cwd, value, external_dir=external_dir
        )
        human = f"set releaseledger_dir: {result['before']} -> {result['after']}"
        return result, [], human

    run_command(
        command="config.set",
        result_type="config_set",
        json_output=state.json_output,
        produce=produce,
    )


# ---------------------------------------------------------------------------
# Commit audit sheet commands
# ---------------------------------------------------------------------------


audit_app = typer.Typer(
    help="Per-release commit audit sheets (git-range review evidence)."
)
app.add_typer(audit_app, name="audit")


@audit_app.command("init")
def audit_init_command(
    ctx: typer.Context,
    version: Annotated[str, typer.Argument(help="Release version string.")],
    base: Annotated[
        str,
        typer.Option("--base", help="Git base ref (e.g. v0.2.0)."),
    ] = "",
    head: Annotated[
        str,
        typer.Option("--head", help="Git head ref (default HEAD)."),
    ] = "",
    overwrite: Annotated[
        bool,
        typer.Option("--overwrite", help="Replace an existing sheet."),
    ] = False,
    format_name: Annotated[
        str,
        typer.Option("--format", help="Output format: markdown or json."),
    ] = "markdown",
) -> None:
    """Create the canonical commit audit sheet from the git range."""
    state = cli_state_from_context(ctx)
    if format_name not in {"markdown", "json"}:
        err = ReleaseledgerError(
            f"Unsupported --format: {format_name!r}",
            code="USAGE_ERROR",
            exit_code=2,
        )
        emit_error(command="audit.init", error=err, json_output=state.json_output)
        raise typer.Exit(launch_error_exit_code(err)) from err
    try:
        workspace_root = _paths(ctx).workspace_root
        result = create_commit_audit_sheet(
            workspace_root,
            version=version,
            git_base=base or None,
            git_head=head or None,
            overwrite=overwrite,
        )
    except ReleaseledgerError as exc:
        emit_error(command="audit.init", error=exc, json_output=state.json_output)
        raise typer.Exit(launch_error_exit_code(exc)) from exc
    if format_name == "json":
        human = ""
    else:
        human = (
            f"created audit sheet for {version} ({_as_int(result['row_count'])} rows)"
        )
    emit_payload(
        command="audit.init",
        result_type="commit_audit_sheet_created",
        result=result,
        human=human,
        json_output=state.json_output,
    )


@audit_app.command("show")
def audit_show_command(
    ctx: typer.Context,
    version: Annotated[str, typer.Argument(help="Release version string.")],
    format_name: Annotated[
        str,
        typer.Option("--format", help="Output format: markdown or json."),
    ] = "markdown",
    output: Annotated[
        Path | None,
        typer.Option("--output", help="Write rendered output to a file."),
    ] = None,
) -> None:
    """Render the commit audit sheet for display or export."""
    state = cli_state_from_context(ctx)
    if format_name not in {"markdown", "json"}:
        err = ReleaseledgerError(
            f"Unsupported --format: {format_name!r}",
            code="USAGE_ERROR",
            exit_code=2,
        )
        emit_error(command="audit.show", error=err, json_output=state.json_output)
        raise typer.Exit(launch_error_exit_code(err)) from err
    try:
        workspace_root = _paths(ctx).workspace_root
        rendered = render_commit_audit_sheet(
            workspace_root, version=version, format_name=format_name
        )
    except ReleaseledgerError as exc:
        emit_error(command="audit.show", error=exc, json_output=state.json_output)
        raise typer.Exit(launch_error_exit_code(exc)) from exc
    if output is not None:
        text = (
            json.dumps(rendered, indent=2, sort_keys=True)
            if isinstance(rendered, dict)
            else str(rendered)
        )
        try:
            output.write_text(text, encoding="utf-8")
        except OSError as exc:
            err = ReleaseledgerError(
                f"Failed to write {output}: {exc}",
                code="USAGE_ERROR",
                exit_code=2,
            )
            emit_error(command="audit.show", error=err, json_output=state.json_output)
            raise typer.Exit(launch_error_exit_code(err)) from err
        human = f"wrote {output}"
    elif format_name == "json":
        human = ""
    else:
        human = str(rendered)
    payload: dict[str, object] = {"version": version, "format": format_name}
    if isinstance(rendered, dict):
        payload["sheet"] = rendered
    else:
        payload["document"] = rendered
    emit_payload(
        command="audit.show",
        result_type="commit_audit_sheet",
        result=payload,
        human=human,
        json_output=state.json_output,
    )


@audit_app.command("update")
def audit_update_command(
    ctx: typer.Context,
    version: Annotated[str, typer.Argument(help="Release version string.")],
    file: Annotated[Path, typer.Option("--file", help="Edited YAML sheet file.")],
) -> None:
    """Import an edited YAML sheet, validating enums and row completeness."""
    state = cli_state_from_context(ctx)
    try:
        workspace_root = _paths(ctx).workspace_root
        result = update_commit_audit_sheet(workspace_root, version=version, file=file)
    except ReleaseledgerError as exc:
        emit_error(command="audit.update", error=exc, json_output=state.json_output)
        raise typer.Exit(launch_error_exit_code(exc)) from exc
    human = (
        f"updated audit sheet for {version} "
        f"(revision {_as_int(result['revision'])}, {_as_int(result['row_count'])} rows)"
    )
    emit_payload(
        command="audit.update",
        result_type="commit_audit_sheet_updated",
        result=result,
        human=human,
        json_output=state.json_output,
    )


@audit_app.command("validate")
def audit_validate_command(
    ctx: typer.Context,
    version: Annotated[str, typer.Argument(help="Release version string.")],
    strict: Annotated[bool, typer.Option("--strict")] = False,
    include_internal: Annotated[
        bool,
        typer.Option("--include-internal", help="Check internal row coverage."),
    ] = False,
) -> None:
    """Validate the audit sheet against release entries and git coverage."""
    state = cli_state_from_context(ctx)
    try:
        workspace_root = _paths(ctx).workspace_root
        result = validate_commit_audit_sheet(
            workspace_root,
            version=version,
            strict=strict,
            include_internal=include_internal,
        )
    except ReleaseledgerError as exc:
        emit_error(command="audit.validate", error=exc, json_output=state.json_output)
        raise typer.Exit(launch_error_exit_code(exc)) from exc
    ok = bool(result.get("ok"))
    if ok:
        human = f"audit validation passed for {version}"
    else:
        needs = _as_int(result.get("needs_review_count", 0))
        uninsp = _as_int(result.get("uninspected_count", 0))
        missing = len(result.get("missing_entry_coverage", []))  # type: ignore[arg-type]
        human = (
            f"audit validation for {version}: ok=false "
            f"(needs_review={needs}, uninspected={uninsp}, "
            f"missing_coverage={missing})"
        )
    emit_payload(
        command="audit.validate",
        result_type="commit_audit_validation",
        result=result,
        human=human,
        json_output=state.json_output,
    )


@audit_app.command("sync")
def audit_sync_command(
    ctx: typer.Context,
    version: Annotated[str, typer.Argument(help="Release version string.")],
) -> None:
    """Fill target_entry_id on rows from matching entry source refs."""
    state = cli_state_from_context(ctx)
    try:
        workspace_root = _paths(ctx).workspace_root
        result = sync_audit_targets_from_entries(workspace_root, version=version)
    except ReleaseledgerError as exc:
        emit_error(command="audit.sync", error=exc, json_output=state.json_output)
        raise typer.Exit(launch_error_exit_code(exc)) from exc
    human = (
        f"synced audit sheet for {version}: "
        f"{_as_int(result['updated_rows'])} row(s) updated "
        f"(revision {_as_int(result['revision'])})"
    )
    emit_payload(
        command="audit.sync",
        result_type="commit_audit_sync",
        result=result,
        human=human,
        json_output=state.json_output,
    )
