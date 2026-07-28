"""Releaseledger command-line interface.

The root callback owns canonical ``--root`` selection and compatibility
handling for ``--cwd``. Domain services remain below this module while the
shared command boundary in :mod:`releaseledger.cli_common` owns rendering.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, Any, Literal, cast

import typer
import yaml
from ledgercore.cli import (
    CLIWarning,
    deprecated_command_warning,
    deprecated_option_warning,
)
from typer.core import TyperGroup

from releaseledger._version import __version__
from releaseledger.cli_common import (
    CLIState,
    CommandResult,
    add_cli_warning,
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
from releaseledger.command_registry import COMMAND_INVENTORY, command_help
from releaseledger.commands.common import (
    next_action,
    project_doctor,
    project_info,
    project_status,
)
from releaseledger.errors import CODE_USAGE_ERROR, LaunchError, ReleaseledgerError
from releaseledger.services.audit import (
    apply_commit_audit_annotations,
    collect_commit_subjects,
    create_commit_audit_sheet,
    guard_entry_summaries,
    refresh_commit_audit_sheet,
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
    config_show,
    config_validate,
    storage_where,
)
from releaseledger.services.entries import (
    add_many_release_entries,
    add_release_entry,
    delete_release_entry,
    import_release_entry_file,
    list_release_entries,
    load_entry_batch_file_with_metadata,
    load_entry_batch_payload,
    set_entry_status,
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
    export_git_evidence,
    generate_git_scaffold_batch,
    is_root_base_ref,
    release_snapshot_drift_report,
    resolve_base_sha,
    resolve_git_ref,
    resolve_release_snapshot,
)
from releaseledger.services.releases import (
    UNSET,
    cancel_release,
    check_release_chain,
    create_release,
    finalize_release,
    import_tags,
    list_release_records,
    prepare_release,
    reconcile_releases,
    remove_changelog_section,
    rename_changelog_section,
    rename_release,
    repair_release_chain,
    set_release_status,
    show_release,
    tag_release,
    update_release,
)
from releaseledger.services.review import build_release_review
from releaseledger.storage.paths import (
    ProjectPaths,
    ensure_canonical_project,
    load_releaseledger_project,
    require_project,
)
from releaseledger.storage.store import load_release


class LegacyChangelogGroup(TyperGroup):
    """Treat an unknown positional token as the legacy preview version."""

    def resolve_command(self, ctx: typer.Context, args: list[str]) -> Any:  # type: ignore[override]
        if args and not args[0].startswith("-") and args[0] not in self.commands:
            preview = self.commands.get("preview")
            if preview is not None:
                ctx.meta["legacy_changelog_preview"] = True
                return "preview", preview, args
        return super().resolve_command(ctx, args)


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
    root: Annotated[
        Path | None,
        typer.Option("--root", help="Project root for discovery and commands."),
    ] = None,
    cwd: Annotated[
        Path | None,
        typer.Option(
            "--cwd",
            help="Deprecated alias for --root.",
            hidden=True,
        ),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON envelopes."),
    ] = False,
) -> None:
    """Manage project-local release state."""
    # A CliRunner can reuse the same context across invocations.  Reset the
    # warning accumulator before the root callback handles an invocation that
    # may fail before CLIState is stored (notably conflicting --root/--cwd).
    from releaseledger.cli_common import _warnings

    _warnings.set([])
    resolved_root = resolve_workspace_root(root)
    resolved_cwd = resolve_workspace_root(cwd) if cwd is not None else None
    if root is not None and resolved_cwd is not None and resolved_root != resolved_cwd:
        error = LaunchError(
            "--root and deprecated --cwd refer to different paths.",
            code=CODE_USAGE_ERROR,
            exit_code=2,
            remediation=["Supply only --root PATH."],
        )
        emit_error(
            command=ctx.invoked_subcommand or "",
            error=error,
            json_output=json_output,
        )
        raise typer.Exit(2)
    effective_root = (
        resolved_cwd if root is None and resolved_cwd is not None else resolved_root
    )
    warnings = []
    if cwd is not None:
        warnings.append(deprecated_option_warning("--cwd", "--root"))
    store_cli_state(
        ctx,
        CLIState(
            root=effective_root,
            json_output=json_output,
            legacy_cwd=cwd is not None,
            warnings=warnings,
        ),
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
    data_storage: Annotated[
        str,
        typer.Option(
            "--data-storage",
            help="Data mount storage: project, external, or user-data.",
        ),
    ] = "project",
    external_root: Annotated[
        str | None,
        typer.Option("--external-root", help="External data root path."),
    ] = None,
    local_override: Annotated[
        bool,
        typer.Option(
            "--local-override",
            help="Write the data mount override to .ledger/ledger.local.toml.",
        ),
    ] = False,
    adopt_empty: Annotated[
        bool,
        typer.Option(
            "--adopt-empty",
            help="Adopt an existing empty data directory without a binding.",
        ),
    ] = False,
    force_config: Annotated[
        bool,
        typer.Option(
            "--force-config", help="Replace the Releaseledger tool config after backup."
        ),
    ] = False,
) -> None:
    """Initialize a Ledgercore schema-3 project with Releaseledger registration."""
    state = cli_state_from_context(ctx)
    workspace_root = state.cwd

    def produce() -> CommandResult:
        if releaseledger_dir is not None or external_dir:
            raise LaunchError(
                "--releaseledger-dir and --external-dir are no longer "
                "supported; configure the canonical Ledger project instead.",
                code=CODE_USAGE_ERROR,
                exit_code=2,
                data={
                    "flag": "--releaseledger-dir"
                    if releaseledger_dir is not None
                    else "--external-dir",
                },
                remediation=[
                    "Run `releaseledger init` without legacy flags.",
                    "Use `releaseledger storage set data --storage external` "
                    "`--root PATH` to change data storage after init.",
                    "to change data storage after init.",
                ],
            )
        result = ensure_canonical_project(
            workspace_root,
            project_name=project_name,
            force=force,
            data_storage=data_storage,
            external_root=external_root,
            local_override=local_override,
            adopt_empty=adopt_empty,
            force_config=force_config,
        )
        data_root = Path(str(result["data_root"]))
        try:
            display = data_root.relative_to(workspace_root.resolve())
            display_str = display.as_posix()
        except ValueError:
            display_str = str(data_root)
        human = (
            f"initialized releaseledger in {display_str}\n"
            "wrote .ledger/ledger.toml and .ledger/releaseledger/config.toml"
        )
        return result, [], human

    run_command(
        command="init",
        result_type="project_init",
        json_output=state.json_output,
        produce=produce,
    )


@app.command("commands")
def commands_command(ctx: typer.Context) -> None:
    """List canonical command metadata without requiring a project."""
    state = cli_state_from_context(ctx)

    def produce() -> CommandResult:
        commands = [entry.as_mapping() for entry in COMMAND_INVENTORY.entries]
        return (
            {"kind": "command_inventory", "commands": commands},
            [],
            COMMAND_INVENTORY.human_table(),
        )

    run_command(
        command="commands",
        result_type="command_inventory",
        json_output=state.json_output,
        produce=produce,
    )


@app.command("help")
def help_command_cli(
    ctx: typer.Context,
    path: Annotated[list[str] | None, typer.Argument(help="Command path.")] = None,
) -> None:
    """Show generated help for a command or command group."""
    state = cli_state_from_context(ctx)
    path_parts = list(path or [])
    requested = " ".join(path_parts)

    def produce() -> CommandResult:
        try:
            result = command_help(path_parts)
        except KeyError as exc:
            raise LaunchError(
                f"Unknown command path `{requested}`.",
                code=CODE_USAGE_ERROR,
                exit_code=2,
                remediation=["Run `releaseledger commands`."],
            ) from exc
        metadata = COMMAND_INVENTORY.resolve(requested)
        if metadata is not None and requested != metadata.path:
            add_cli_warning(deprecated_command_warning(requested, metadata.path))
        if "children" in result:
            children = result["children"]
            assert isinstance(children, list)
            human = "\n".join(
                f"{item['path']}  {item['summary']}"
                for item in children
                if isinstance(item, dict)
            )
        else:
            human = f"{result.get('path', requested)}\n{result.get('summary', '')}"
        return result, [], human

    run_command(
        command="help",
        result_type="command_help",
        json_output=state.json_output,
        produce=produce,
    )


@app.command("status")
def status_command(
    ctx: typer.Context,
    check: Annotated[
        bool, typer.Option("--check", help="Return 1 when unhealthy.")
    ] = False,
) -> None:
    """Show a concise, read-only project status."""
    state = cli_state_from_context(ctx)

    def produce() -> CommandResult:
        result = project_status(state.root, check=check)
        human = (
            f"{result.get('state', '')}: {result.get('project_root', '')}\n"
            f"health: {result.get('health', '')}\n"
            f"next: {result.get('next_action', {}).get('command', '')}"  # type: ignore[attr-defined]
        )
        return result, [], human

    run_command(
        command="status",
        result_type="project_status",
        json_output=state.json_output,
        produce=produce,
        check_passed=None
        if not check
        else bool(project_status(state.root, check=True).get("passed")),
    )


@app.command("info")
def info_command(ctx: typer.Context) -> None:
    """Show the full read-only project inventory."""
    state = cli_state_from_context(ctx)

    def produce() -> CommandResult:
        result = project_info(state.root)
        return (
            result,
            [],
            (
                f"project: {result.get('status', {}).get('project_root', '')}\n"  # type: ignore[attr-defined]
                f"releases: {result.get('release_count', 0)}\n"
                f"entries: {result.get('entry_count', 0)}"
            ),
        )

    run_command(
        command="info",
        result_type="project_info",
        json_output=state.json_output,
        produce=produce,
    )


@app.command("doctor")
def doctor_command(
    ctx: typer.Context,
    check: Annotated[
        bool, typer.Option("--check", help="Exit 1 on failed checks.")
    ] = False,
) -> None:
    """Run read-only project diagnostics."""
    state = cli_state_from_context(ctx)

    def produce() -> CommandResult:
        result = project_doctor(state.root, check=check)
        checks = result.get("checks", [])
        human = "\n".join(
            f"{item.get('status', ''):<5} {item.get('code', '')}: {item.get('message', '')}"
            for item in checks  # type: ignore[attr-defined]
            if isinstance(item, dict)
        )
        return result, [], human

    result = project_doctor(state.root, check=check)
    run_command(
        command="doctor",
        result_type="doctor",
        json_output=state.json_output,
        produce=produce,
        check_passed=None if not check else bool(result.get("passed")),
    )


@app.command("next-action")
def next_action_command(ctx: typer.Context) -> None:
    """Recommend the next command without executing it."""
    state = cli_state_from_context(ctx)

    def produce() -> CommandResult:
        result = next_action(state.root)
        return result, [], f"next: {result['command']}\nreason: {result['reason']}"

    run_command(
        command="next-action",
        result_type="next_action",
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
    reason: Annotated[str | None, typer.Option("--reason")] = None,
) -> None:
    """Update release metadata, with explicit clear flags for optional fields."""
    state = cli_state_from_context(ctx)

    if status is not None:
        metadata_supplied = any(
            value is not None
            for value in (
                title,
                note,
                previous_version,
                changelog_file,
                boundary_ref,
                source_refs,
                source_count,
                released_at,
                git_base_ref,
                git_head_ref,
            )
        ) or any(
            (
                clear_previous,
                clear_changelog_file,
                clear_boundary_ref,
                clear_source_refs,
                clear_source_count,
                clear_released_at,
                clear_git_range,
            )
        )
        if metadata_supplied:
            error = LaunchError(
                "--status is now a lifecycle command and cannot be combined with metadata updates.",
                code=CODE_USAGE_ERROR,
                exit_code=2,
                remediation=["Use `release set-status VERSION STATUS --reason TEXT`."],
            )
            emit_error(
                command="release update", error=error, json_output=state.json_output
            )
            raise typer.Exit(2)
        add_cli_warning(
            deprecated_command_warning("release update --status", "release set-status")
        )

        def legacy_status() -> CommandResult:
            if state.legacy_cwd and status == "released":
                result = update_release(
                    _paths(ctx).workspace_root,
                    version=version,
                    status=status,
                )
                return result, _event_ids(result), f"updated release {version} status"
            result = set_release_status(
                _paths(ctx).workspace_root,
                version=version,
                status=status,
                reason=reason or "Legacy release update --status transition.",
            )
            return result, _event_ids(result), f"updated release {version} status"

        run_command(
            command="release set-status",
            result_type="release_status_change",
            json_output=state.json_output,
            produce=legacy_status,
            workspace_root=_paths(ctx).workspace_root,
            mutating=True,
        )
        return

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


@release_app.command("set-status")
def release_set_status_command(
    ctx: typer.Context,
    version: Annotated[str, typer.Argument()],
    status: Annotated[str, typer.Argument()],
    reason: Annotated[str, typer.Option("--reason")],
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Set a nonterminal release status explicitly."""
    state = cli_state_from_context(ctx)

    def produce() -> CommandResult:
        result = set_release_status(
            _paths(ctx).workspace_root,
            version=version,
            status=status,
            reason=reason,
            dry_run=dry_run,
        )
        action = "previewed" if dry_run else "set"
        return (
            result,
            _event_ids(result),
            f"{action} release {version} status to {status}",
        )

    run_command(
        command="release set-status",
        result_type="release_status_change",
        json_output=state.json_output,
        produce=produce,
        workspace_root=_paths(ctx).workspace_root,
        mutating=not dry_run,
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


@release_app.command("prepare")
def release_prepare_command(
    ctx: typer.Context,
    version: Annotated[str, typer.Argument(help="Release version string.")],
    previous_version: Annotated[
        str | None,
        typer.Option("--previous", help="Explicit previous release version."),
    ] = None,
    released_at: Annotated[
        str | None,
        typer.Option("--released-at", help="Release date YYYY-MM-DD."),
    ] = None,
    git_base_ref: Annotated[
        str | None,
        typer.Option("--git-base", help="Git range base ref."),
    ] = None,
    git_head_ref: Annotated[
        str | None,
        typer.Option("--git-head", help="Git range head ref."),
    ] = None,
    output_dir: Annotated[
        Path,
        typer.Option(
            "--output-dir", help="Directory for exported preparation artifacts."
        ),
    ] = Path(".releaseledger/work"),
) -> None:
    """Create/update a planned release snapshot and export working artifacts."""
    state = cli_state_from_context(ctx)

    def produce() -> CommandResult:
        result = prepare_release(
            _paths(ctx).workspace_root,
            version=version,
            previous_version=previous_version,
            released_at=released_at,
            git_base_ref=git_base_ref,
            git_head_ref=git_head_ref,
            output_dir=output_dir,
        )
        outputs = result.get("outputs")
        outputs_dict = outputs if isinstance(outputs, dict) else {}
        human = (
            f"prepared release {version}\n"
            f"  range: {outputs_dict.get('range_json', '')}\n"
            f"  audit: {outputs_dict.get('audit_yaml', '')}\n"
            f"  scaffold: {outputs_dict.get('entries_yaml', '')}"
        )
        return result, [], human

    run_command(
        command="release.prepare",
        result_type="release_prepare",
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
        if record.get("git_base_ref"):
            lines.append(f"git_base_ref: {record['git_base_ref']}")
        if record.get("git_base_sha"):
            lines.append(f"git_base_sha: {record['git_base_sha']}")
        if record.get("git_head_ref"):
            lines.append(f"git_head_ref: {record['git_head_ref']}")
        if record.get("git_head_sha"):
            lines.append(f"git_head_sha: {record['git_head_sha']}")
        if record.get("git_range"):
            lines.append(f"git_range: {record['git_range']}")
        if record.get("git_commit_count") is not None:
            lines.append(f"git_commit_count: {record['git_commit_count']}")
        drift = result.get("snapshot_drift")
        if isinstance(drift, dict):
            lines.append(f"snapshot_drift: {drift.get('status', 'unknown')}")
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


@release_app.command("reconcile")
def release_reconcile_command(
    ctx: typer.Context,
    strict: Annotated[
        bool,
        typer.Option(
            "--strict", help="Exit non-zero when reconciliation finds problems."
        ),
    ] = False,
    target_file: Annotated[
        Path | None,
        typer.Option("--target-file", help="CHANGELOG file to inspect."),
    ] = None,
) -> None:
    """Compare release records with Git tags and changelog headings."""
    state = cli_state_from_context(ctx)

    def produce() -> CommandResult:
        result = reconcile_releases(
            _paths(ctx).workspace_root, changelog_file=target_file
        )
        problems = result.get("problems", [])
        lines = ["RECONCILIATION PROBLEMS"] if problems else ["RECONCILIATION OK"]
        if isinstance(problems, list):
            for problem in problems:
                if isinstance(problem, dict):
                    lines.append(
                        f"{problem.get('kind')}  {problem.get('version', '')}".rstrip()
                    )
        human = "\\n".join(lines)
        if strict and not bool(result.get("ok")):
            raise ReleaseledgerError(
                "Release state reconciliation failed.",
                code="VALIDATION_ERROR",
                exit_code=2,
                data={"result": result, "human": human},
            )
        return result, [], human

    run_command(
        command="release.reconcile",
        result_type="release_reconcile",
        json_output=state.json_output,
        produce=produce,
    )


@release_app.command("import-tags")
def release_import_tags_command(
    ctx: typer.Context,
    apply: Annotated[
        bool,
        typer.Option(
            "--apply",
            help="Create missing release records. Without this flag, only dry-run.",
        ),
    ] = False,
) -> None:
    """Discover semver git tags and create missing release records."""
    state = cli_state_from_context(ctx)

    def produce() -> CommandResult:
        result = import_tags(_paths(ctx).workspace_root, apply=apply)
        planned = [
            e
            for e in result.get("plans", [])  # type: ignore[attr-defined]
            if isinstance(e, dict) and e.get("action") == "create"
        ]
        lines = []
        if apply:
            lines.append(f"Applied {len(result.get('applied_versions', []))} tag(s).")  # type: ignore[arg-type]
            lines.append(
                f"Skipped {len(result.get('skipped_versions', []))} existing tag(s)."  # type: ignore[arg-type]
            )
        else:
            lines.append(f"DRY RUN: {len(planned)} tag(s) would be imported.")
            lines.append("Use --apply to create release records.")
        for entry in planned:
            lines.append(
                f"  {entry['version']} (tag: {entry['tag']}, date: {entry.get('released_at', '')})"
            )
        human = "\n".join(lines)
        return result, [], human

    run_command(
        command="release.import_tags",
        result_type="release_import_tags",
        json_output=state.json_output,
        produce=produce,
    )


@release_app.command("check")
def release_check_command(
    ctx: typer.Context,
    version: Annotated[str, typer.Argument(help="Release version string.")],
    target_file: Annotated[
        Path | None,
        typer.Option("--target-file", help="CHANGELOG target file for the dry-run."),
    ] = None,
    strict: Annotated[
        bool,
        typer.Option("--strict", help="Exit non-zero when the release check fails."),
    ] = False,
    include_internal: Annotated[
        bool,
        typer.Option(
            "--include-internal", help="Include internal entries in coverage."
        ),
    ] = False,
) -> None:
    """Run the consolidated read-only release gate."""
    state = cli_state_from_context(ctx)
    try:
        release_record = load_release(_paths(ctx).workspace_root, version)
        require_audit_sheet = bool(
            release_record.git_base_sha
            or release_record.git_base_ref
            or release_record.git_head_sha
            or release_record.git_head_ref
        )
        result = build_release_review(
            _paths(ctx).workspace_root,
            version=version,
            include_internal=include_internal,
            include_statuses=("accepted",),
            target_file=target_file,
            strict=strict,
            git=True,
            require_audit_sheet=require_audit_sheet,
            include_history_health=True,
        )
    except ReleaseledgerError as exc:
        emit_error(command="release check", error=exc, json_output=state.json_output)
        raise typer.Exit(launch_error_exit_code(exc)) from exc
    ok = bool(result.get("ok", False))
    emit_payload(
        command="release check",
        result_type="release_check",
        result=result,
        human=_render_release_check_human(version, result),
        json_output=state.json_output,
    )
    if strict and not ok:
        raise typer.Exit(1)


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
    rewrite_successors: Annotated[
        bool,
        typer.Option(
            "--rewrite-successors",
            help="Rewrite direct successors to a safe predecessor.",
        ),
    ] = False,
    successor_previous_version: Annotated[
        str | None,
        typer.Option("--successor-previous", help="New predecessor for successors."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Preview cancellation without writing."),
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
            rewrite_successors=rewrite_successors,
            successor_previous_version=(
                successor_previous_version
                if successor_previous_version is not None
                else UNSET
            ),
            dry_run=dry_run,
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
        workspace_root=_paths(ctx).workspace_root,
        mutating=not dry_run,
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
def release_chain_check_command(
    ctx: typer.Context,
    strict: Annotated[
        bool,
        typer.Option("--strict", help="Exit non-zero when chain health fails."),
    ] = False,
) -> None:
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
        if strict and not bool(result.get("ok")):
            raise ReleaseledgerError(
                "Release chain check failed.",
                code="VALIDATION_ERROR",
                exit_code=2,
                data={"result": result, "human": human},
            )
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
    reason: Annotated[str | None, typer.Option("--reason")] = None,
) -> None:
    """Update explicitly supplied entry fields."""
    state = cli_state_from_context(ctx)

    if status is not None and all(
        value is None
        for value in (
            kind,
            summary,
            body,
            audience,
            scopes,
            source_refs,
            paths,
            issues,
            prs,
            breaking,
            internal,
        )
    ):
        add_cli_warning(
            deprecated_command_warning("entry update --status", "entry set-status")
        )

        def legacy_status() -> CommandResult:
            result = set_entry_status(
                _paths(ctx).workspace_root,
                release_version=version,
                entry_id=entry_id,
                status=status,
                reason=reason or "Legacy entry update --status transition.",
            )
            return result, _event_ids(result), f"updated entry {entry_id} status"

        run_command(
            command="entry set-status",
            result_type="entry_status_change",
            json_output=state.json_output,
            produce=legacy_status,
            workspace_root=_paths(ctx).workspace_root,
            mutating=True,
        )
        return

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


@entry_app.command("set-status")
def entry_set_status_command(
    ctx: typer.Context,
    version: Annotated[str, typer.Argument()],
    entry_id: Annotated[str, typer.Argument()],
    status: Annotated[str, typer.Argument()],
    reason: Annotated[str, typer.Option("--reason")],
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Set an entry status explicitly."""
    state = cli_state_from_context(ctx)

    def produce() -> CommandResult:
        result = set_entry_status(
            _paths(ctx).workspace_root,
            release_version=version,
            entry_id=entry_id,
            status=status,
            reason=reason,
            dry_run=dry_run,
        )
        action = "previewed" if dry_run else "set"
        return (
            result,
            _event_ids(result),
            f"{action} entry {entry_id} status to {status}",
        )

    run_command(
        command="entry set-status",
        result_type="entry_status_change",
        json_output=state.json_output,
        produce=produce,
        workspace_root=_paths(ctx).workspace_root,
        mutating=not dry_run,
    )


@entry_app.command("delete")
def entry_delete_command(
    ctx: typer.Context,
    version: Annotated[str, typer.Argument()],
    entry_id: Annotated[str, typer.Argument()],
    reason: Annotated[str, typer.Option("--reason", help="Why the entry is deleted.")],
    force_accepted: Annotated[
        bool,
        typer.Option("--force-accepted", help="Allow deletion of accepted entries."),
    ] = False,
    detach_audit: Annotated[
        bool,
        typer.Option("--detach-audit", help="Detach audit rows targeting the entry."),
    ] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Delete a release entry with lifecycle and audit safety checks."""
    state = cli_state_from_context(ctx)

    def produce() -> CommandResult:
        result = delete_release_entry(
            _paths(ctx).workspace_root,
            release_version=version,
            entry_id=entry_id,
            reason=reason,
            force_accepted=force_accepted,
            detach_audit=detach_audit,
            dry_run=dry_run,
        )
        action = "previewed deletion of" if dry_run else "deleted"
        return result, _event_ids(result), f"{action} entry {entry_id}"

    run_command(
        command="entry.delete",
        result_type="release_entry_delete",
        json_output=state.json_output,
        produce=produce,
        workspace_root=_paths(ctx).workspace_root,
        mutating=not dry_run,
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
    strict: Annotated[
        bool,
        typer.Option("--strict", help="Fail on lint warnings as well as errors."),
    ] = False,
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
    sync_audit: Annotated[
        bool,
        typer.Option(
            "--sync-audit",
            help="Sync audit target_entry_id values in the same batch operation.",
        ),
    ] = False,
) -> None:
    """Add a validated YAML batch atomically."""
    state = cli_state_from_context(ctx)
    if "entry apply" not in ctx.command_path:
        add_cli_warning(deprecated_command_warning("entry add-many", "entry apply"))

    def produce() -> CommandResult:
        if source_path == Path("-"):
            try:
                payload = yaml.safe_load(sys.stdin.read())
            except yaml.YAMLError as exc:
                raise LaunchError(
                    f"Invalid entry batch input: {exc}",
                    code=CODE_USAGE_ERROR,
                    exit_code=2,
                ) from exc
            entries, legacy_batch = load_entry_batch_payload(payload)
        else:
            entries, legacy_batch = load_entry_batch_file_with_metadata(source_path)
        if legacy_batch:
            add_cli_warning(
                CLIWarning(
                    code="legacy_input",
                    message="Unversioned entry batches are deprecated.",
                    replacement="schema: releaseledger.entry-batch.v1",
                )
            )
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
            fail_on_warning=strict,
            sync_audit=sync_audit,
        )
        issues = result.get("issues")
        blocking_issues = (
            [
                issue
                for issue in issues
                if isinstance(issue, dict)
                and (str(issue.get("severity", "error")) == "error" or strict)
            ]
            if isinstance(issues, list)
            else []
        )
        if blocking_issues:
            lint = result.get("lint", {})
            lint_summary = lint.get("summary", {}) if isinstance(lint, dict) else {}
            warnings = (
                int(lint_summary.get("warnings", 0))
                if isinstance(lint_summary, dict)
                else 0
            )
            raise ReleaseledgerError(
                f"Entry batch validation failed with {len(blocking_issues)} issue(s)"
                f" and {warnings} warning(s).",
                code="VALIDATION_ERROR",
                exit_code=2,
                data={
                    "result": result,
                    "human": (
                        f"Entry batch validation failed with {len(blocking_issues)} issue(s) "
                        f"and {warnings} warning(s).\n"
                        + _render_lint_issues(blocking_issues)
                    ),
                },
            )
        action = "previewed" if dry_run else "added"
        return (
            result,
            _event_ids(result),
            f"{action} {len(entries)} entries for release {version}",
        )

    run_command(
        command="entry apply",
        result_type="release_entry_batch",
        json_output=state.json_output,
        produce=produce,
        workspace_root=_paths(ctx).workspace_root,
        mutating=not dry_run,
    )


entry_app.command("apply")(entry_add_many_command)


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


def _render_lint_issues(issues: list[dict[str, object]]) -> str:
    """Format per-entry lint issues as aligned rows plus their messages."""
    lines: list[str] = []
    for issue in issues:
        entry_id = str(issue.get("entry_id") or "-")
        severity = str(issue.get("severity", ""))
        field = str(issue.get("field", ""))
        code = str(issue.get("code", ""))
        message = str(issue.get("message", ""))
        lines.append(f"{entry_id}  {severity}  {field}  {code}")
        if message:
            lines.append(f"  {message}")
    return "\n".join(lines)


@entry_app.command("lint")
def entry_lint_command(
    ctx: typer.Context,
    version: Annotated[str, typer.Argument()],
    strict: Annotated[bool, typer.Option("--strict")] = False,
    include_statuses: Annotated[
        list[str] | None, typer.Option("--include-status")
    ] = None,
) -> None:
    """Lint release entries and optionally fail on warnings.

    On failure the command still emits the full per-entry ``issues`` and
    ``entries`` payload (JSON ``result`` plus the standard ``error`` envelope),
    and exits non-zero. ``--strict`` fails on warnings as today.
    """
    state = cli_state_from_context(ctx)
    try:
        result = lint_release_entries(
            _paths(ctx).workspace_root,
            release_version=version,
            strict=strict,
            include_statuses=(
                tuple(include_statuses) if include_statuses is not None else None
            ),
        )
    except ReleaseledgerError as exc:
        emit_error(command="entry lint", error=exc, json_output=state.json_output)
        raise typer.Exit(launch_error_exit_code(exc)) from exc

    summary = result["summary"]
    assert isinstance(summary, dict)
    errors = int(summary["errors"])
    warnings = int(summary["warnings"])

    if result["passed"]:
        human = f"entry lint passed: {errors} error(s), {warnings} warning(s)"
        emit_payload(
            command="entry lint",
            result_type="entry_lint",
            result=result,
            human=human,
            json_output=state.json_output,
        )
        return

    lint_error = ReleaseledgerError(
        f"Entry lint failed with {errors} error(s) and {warnings} warning(s).",
        code="VALIDATION_ERROR",
        exit_code=2,
    )
    emit_error(
        command="entry lint",
        error=lint_error,
        json_output=state.json_output,
        result=result,
        result_type="entry_lint",
    )
    if not state.json_output:
        issues = result.get("issues", [])
        if isinstance(issues, list) and issues:
            typer.echo("", err=True)
            typer.echo(_render_lint_issues(issues), err=True)
    raise typer.Exit(1)


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
        emit_error(command="entry prompt", error=exc, json_output=state.json_output)
        raise typer.Exit(launch_error_exit_code(exc)) from exc
    text = render_json(result) if isinstance(result, dict) else result
    if format_name == "json" and not state.json_output:
        # The format option itself is the machine-output request for this
        # legacy command.  Do not prepend the deprecated --cwd warning to the
        # JSON document in CliRunner/older Typer environments that merge
        # stderr into stdout.
        state.warnings.clear()
    if output is not None:
        target = write_text_output(output, text)
        emit_payload(
            command="entry prompt",
            result_type="entry_prompt",
            result={"output": str(target), "format": format_name},
            human=f"wrote {target}",
            json_output=state.json_output,
        )
        return
    emit_payload(
        command="entry prompt",
        result_type="entry_prompt",
        result={"format": format_name, "content": text},
        human=text,
        json_output=state.json_output,
    )


changelog_app = typer.Typer(
    help="Preview and build changelog artifacts.",
    cls=LegacyChangelogGroup,
    invoke_without_command=True,
)
app.add_typer(changelog_app, name="changelog")


@changelog_app.callback(invoke_without_command=True)
def changelog_group(
    ctx: typer.Context,
) -> None:
    """Route the legacy positional changelog form to preview."""


@changelog_app.command("preview")
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
    if ctx.meta.get("legacy_changelog_preview"):
        add_cli_warning(
            deprecated_command_warning("changelog VERSION", "changelog preview VERSION")
        )
    if target_changelog is not None:
        add_cli_warning(deprecated_option_warning("--target-changelog", "--output"))
    if format_name not in {"markdown", "json"}:
        err = ReleaseledgerError(
            f"Unsupported --format: {format_name!r}",
            code="USAGE_ERROR",
            exit_code=2,
        )
        emit_error(
            command="changelog preview", error=err, json_output=state.json_output
        )
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
        emit_error(
            command="changelog preview", error=exc, json_output=state.json_output
        )
        raise typer.Exit(launch_error_exit_code(exc)) from exc
    if format_name == "json":
        text = render_json(content) if isinstance(content, dict) else str(content)
    else:
        text = content if isinstance(content, str) else render_json(content)
    if (
        state.legacy_cwd
        and state.json_output
        and format_name == "json"
        and output is None
        and isinstance(content, dict)
    ):
        # Preserve the historical ``changelog VERSION --format json`` shape
        # for deprecated --cwd callers.  Canonical --root callers get the v1
        # envelope below.
        state.warnings.clear()
        typer.echo(render_json(content))
        return
    if output is not None:
        out_path = write_text_output(output, text)
        emit_payload(
            command="changelog preview",
            result_type="changelog_preview",
            result={"output": str(out_path), "format": format_name},
            human=f"wrote {out_path}",
            json_output=state.json_output,
        )
        return
    emit_payload(
        command="changelog preview",
        result_type="changelog_preview",
        result={"kind": "changelog_preview", "format": format_name, "content": text},
        human=text,
        json_output=state.json_output,
    )


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
    if "release review" not in ctx.command_path:
        add_cli_warning(deprecated_command_warning("review", "release review"))
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
        emit_error(command="release review", error=exc, json_output=state.json_output)
        raise typer.Exit(launch_error_exit_code(exc)) from exc

    ok = bool(result.get("ok", False))
    emit_payload(
        command=(
            "review" if "release review" not in ctx.command_path else "release review"
        ),
        result_type="release_review",
        result=result,
        human=_render_review_human(version, result),
        json_output=state.json_output,
    )
    if strict and not ok:
        raise typer.Exit(1)


release_app.command("review")(review_command)


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


def _render_release_check_human(version: str, result: dict[str, object]) -> str:
    checks = result.get("checks", {})
    checks_dict = checks if isinstance(checks, dict) else {}
    git_block = result.get("git")
    audit_block = result.get("audit")
    lint = result.get("lint", {})
    lint_dict = lint if isinstance(lint, dict) else {}
    coverage = result.get("coverage", [])
    coverage_list = coverage if isinstance(coverage, list) else []
    release_block = result.get("release")
    release_dict = release_block if isinstance(release_block, dict) else {}
    audit_evidence_ok = True
    audit_complete_ok = True
    if isinstance(audit_block, dict):
        evidence = audit_block.get("evidence", {})
        complete = audit_block.get("complete", {})
        if isinstance(evidence, dict):
            audit_evidence_ok = bool(evidence.get("ok", False))
        if isinstance(complete, dict):
            audit_complete_ok = bool(complete.get("ok", False))
    lines = [f"RELEASE CHECK {version}", ""]
    lines.append(
        f"Snapshot        {'OK' if git_block else 'WARN'}  "
        + (
            str(git_block.get("range", "no stored snapshot"))
            if isinstance(git_block, dict)
            else "no stored snapshot"
        )
    )
    audit_evidence_text = (
        f"{audit_block.get('row_count', 0)}/{audit_block.get('row_count', 0)} inspected"
        if isinstance(audit_block, dict)
        else "no audit sheet"
    )
    evidence_status = "OK" if audit_evidence_ok else "FAIL"
    lines.append(f"Audit evidence  {evidence_status}  {audit_evidence_text}")
    covered_count = sum(
        row.get("status") == "covered" for row in coverage_list if isinstance(row, dict)
    )
    lines.append(
        f"Entry coverage  "
        f"{'OK' if bool(checks_dict.get('coverage_ok', False)) else 'FAIL'}  "
        f"{covered_count}/{len(coverage_list)} refs covered"
    )
    lines.append(
        f"Entry lint      "
        f"{'OK' if bool(checks_dict.get('lint_ok', False)) else 'FAIL'}  "
        f"{int(lint_dict.get('errors', 0))} errors, "
        f"{int(lint_dict.get('warnings', 0))} warnings"
    )
    lines.append(
        f"Release state   "
        f"{'OK' if bool(checks_dict.get('release_state_ok', False)) else 'FAIL'}  "
        f"status={release_dict.get('status', '')} "
        f"released_at={release_dict.get('released_at', '')}"
    )
    lines.append(
        f"Changelog       "
        f"{'OK' if bool(checks_dict.get('changelog_ok', False)) else 'FAIL'}  "
        "dry-run rendered"
    )
    lines.append(
        f"Audit complete  {'OK' if audit_complete_ok else 'FAIL'}  "
        + (
            "entry coverage and summary guard passed"
            if audit_complete_ok
            else "coverage or summary guard failed"
        )
    )
    lines.append(f"Result          {'OK' if result.get('ok') else 'FAIL'}")
    return "\n".join(lines)


def build_command(
    ctx: typer.Context,
    version: Annotated[
        str | None,
        typer.Argument(help="Release version string (omit for full rebuild)."),
    ] = None,
    target_file: Annotated[
        Path | None,
        typer.Option("--target-file", help="Deprecated CHANGELOG target file."),
    ] = None,
    output: Annotated[
        Path | None, typer.Option("--output", help="CHANGELOG target file.")
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
    include_canceled: Annotated[
        bool,
        typer.Option(
            "--include-canceled",
            help="Allow single-release archival/debug rendering of canceled releases.",
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
    unreleased_version: Annotated[
        str | None,
        typer.Option(
            "--unreleased-version",
            help=(
                "Fold a planned/draft/candidate release into "
                "## [Unreleased] (full build only)."
            ),
        ),
    ] = None,
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
    if target_file is not None and output is None:
        add_cli_warning(deprecated_option_warning("--target-file", "--output"))
    target_file = output or target_file
    if "changelog build" not in ctx.command_path:
        add_cli_warning(deprecated_command_warning("build", "changelog build"))
    if format_name not in {"markdown", "json"}:
        err = ReleaseledgerError(
            f"Unsupported --format: {format_name!r}",
            code="USAGE_ERROR",
            exit_code=2,
        )
        emit_error(command="changelog build", error=err, json_output=state.json_output)
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
        emit_error(command="changelog build", error=err, json_output=state.json_output)
        raise typer.Exit(launch_error_exit_code(err)) from err
    if unreleased_version is not None and not full_build:
        err = ReleaseledgerError(
            "--unreleased-version is valid only for full builds "
            "(build --all or build with no VERSION).",
            code="USAGE_ERROR",
            exit_code=2,
            remediation=[
                "Use `releaseledger build --all --unreleased-version VERSION`."
            ],
        )
        emit_error(command="changelog build", error=err, json_output=state.json_output)
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
                unreleased_version=unreleased_version,
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
                include_canceled=include_canceled,
                include_statuses=tuple(include_statuses or ("accepted",)),
                strict=strict,
                allow_empty=allow_empty,
            )
    except ReleaseledgerError as exc:
        emit_error(command="changelog build", error=exc, json_output=state.json_output)
        raise typer.Exit(launch_error_exit_code(exc)) from exc
    target = str(result.get("target_file", ""))
    if full_build:
        if dry_run:
            human = str(result.get("document", ""))
        else:
            release_count = _as_int(result.get("release_count", 0))
            human = f"wrote {target} ({release_count} release sections)"
        # Surface exclusion summaries.
        excluded_internal = _as_int(result.get("excluded_internal_count", 0))
        hidden_commits = _as_int(result.get("hidden_internal_git_commit_count", 0))
        if excluded_internal:
            human += f"\nExcluded internal entries: {excluded_internal}"
        if hidden_commits:
            human += f"\nInternal-only covered commits: {hidden_commits}"
        result_type = "changelog_full_build"
    else:
        if dry_run:
            human = str(result.get("section", ""))
        else:
            human = f"wrote {target}"
        excluded_internal = _as_int(result.get("excluded_internal_count", 0))
        hidden_commits = _as_int(result.get("hidden_internal_git_commit_count", 0))
        if excluded_internal:
            human += f"\nExcluded internal entries: {excluded_internal}"
        if hidden_commits:
            human += f"\nInternal-only covered commits: {hidden_commits}"
        result_type = "changelog_build"
    emit_payload(
        command=(
            "build"
            if state.legacy_cwd and "changelog build" not in ctx.command_path
            else "changelog build"
        ),
        result_type=result_type,
        result=result,
        human=human,
        json_output=state.json_output,
    )


changelog_app.command("build")(build_command)
app.command("build")(build_command)


changelog_section_app = typer.Typer(
    help="Correct release sections in an existing changelog file."
)
app.add_typer(changelog_section_app, name="changelog-section")
section_app = typer.Typer(help="Correct sections in a generated changelog.")
changelog_app.add_typer(section_app, name="section")


@changelog_section_app.command("remove-section")
def changelog_remove_section_command(
    ctx: typer.Context,
    version: Annotated[str, typer.Argument(help="Release section to remove.")],
    target_file: Annotated[
        Path | None,
        typer.Option("--target-file", help="Deprecated changelog output path."),
    ] = None,
    output: Annotated[
        Path | None, typer.Option("--output", help="Changelog file to update.")
    ] = None,
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
    if target_file is not None and output is None:
        add_cli_warning(deprecated_option_warning("--target-file", "--output"))
    effective_target = output or target_file

    def produce() -> CommandResult:
        if effective_target is None:
            raise LaunchError(
                "An output path is required; use --output PATH.",
                code=CODE_USAGE_ERROR,
                exit_code=2,
            )
        result = remove_changelog_section(
            _paths(ctx).workspace_root,
            version=version,
            target_file=effective_target,
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
        command="changelog section remove",
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
        Path | None,
        typer.Option("--target-file", help="Deprecated changelog output path."),
    ] = None,
    output: Annotated[
        Path | None, typer.Option("--output", help="Changelog file to update.")
    ] = None,
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
    if target_file is not None and output is None:
        add_cli_warning(deprecated_option_warning("--target-file", "--output"))
    effective_target = output or target_file

    def produce() -> CommandResult:
        if effective_target is None:
            raise LaunchError(
                "An output path is required; use --output PATH.",
                code=CODE_USAGE_ERROR,
                exit_code=2,
            )
        result = rename_changelog_section(
            _paths(ctx).workspace_root,
            old_version=old_version,
            new_version=new_version,
            target_file=effective_target,
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
        command="changelog section rename",
        result_type="changelog_section_rename",
        json_output=state.json_output,
        produce=produce,
    )


section_app.command("remove")(changelog_remove_section_command)
section_app.command("rename")(changelog_rename_section_command)


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
        typer.Option(
            "--head",
            help="Head ref; defaults to the stored release head, then HEAD.",
        ),
    ] = "",
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
            base_display=base,
            head_display=head or GIT_DEFAULT_HEAD,
            base_spec=base,
            head_spec=head or GIT_DEFAULT_HEAD,
            include_merges=include_merges,
            evidence=evidence,
        )
        return

    # Real release: use stored git_* fields when --base/--head not supplied.
    existing = load_release(workspace_root, version)
    try:
        snapshot = resolve_release_snapshot(
            workspace_root,
            existing,
            explicit_base=base or None,
            explicit_head=head or None,
        )
    except LaunchError as exc:
        emit_error(command="git.range", error=exc, json_output=state.json_output)
        raise typer.Exit(launch_error_exit_code(exc)) from exc
    _run_git_range(
        state,
        workspace_root,
        display_version=version,
        base_display=snapshot.base_ref,
        head_display=snapshot.head_ref,
        base_spec=snapshot.base_spec,
        head_spec=snapshot.head_spec,
        include_merges=include_merges,
        evidence=evidence,
        drift=release_snapshot_drift_report(workspace_root, existing),
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
    base_display: str,
    head_display: str,
    base_spec: str,
    head_spec: str,
    include_merges: str,
    evidence: bool = False,
    drift: dict[str, object] | None = None,
) -> None:
    """Render a git range scan (human + JSON)."""
    try:
        candidates = collect_git_candidates(
            workspace_root,
            base_ref=base_spec,
            head_ref=head_spec,
            include_merges=include_merges,
        )
        base_sha = resolve_base_sha(workspace_root, base_spec)
        head_sha = resolve_git_ref(workspace_root, head_spec)
    except LaunchError as exc:
        emit_error(command="git.range", error=exc, json_output=state.json_output)
        raise typer.Exit(launch_error_exit_code(exc)) from exc

    skipped = sum(
        1
        for _ in collect_git_candidates(
            workspace_root,
            base_ref=base_spec,
            head_ref=head_spec,
            include_merges="always",
        )
    ) - len(candidates)
    if skipped < 0:
        skipped = 0

    base_ref_display = ":root" if is_root_base_ref(base_display) else base_display
    range_str = (
        f":root..{head_sha}"
        if is_root_base_ref(base_spec)
        else f"{base_sha}..{head_sha}"
    )
    result: dict[str, object] = {
        "kind": "git_range",
        "version": display_version,
        "base_ref": base_ref_display,
        "base_sha": base_sha,
        "head_ref": head_display,
        "head_sha": head_sha,
        "range": range_str,
        "commit_count": len(candidates) + skipped,
        "merge_commits_skipped": skipped,
        "candidate_count": len(candidates),
        "include_merges": include_merges,
        "candidates": [_candidate_payload(c, evidence=evidence) for c in candidates],
    }
    if drift is not None:
        result["snapshot_drift"] = drift
    lines = [f"GIT RANGE {display_version}", ""]
    lines.append(f"  base: {base_ref_display} -> {base_sha[:7]}")
    lines.append(f"  head: {head_display} -> {head_sha[:7]}")
    if drift is not None:
        lines.append(f"  snapshot drift: {drift.get('status', 'unknown')}")
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
    emit_payload(
        command="git range",
        result_type="git_range",
        result=result,
        human="\n".join(lines),
        json_output=state.json_output,
    )


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
        typer.Option(
            "--head", help="Head ref (defaults to the stored release head, then HEAD)."
        ),
    ] = "",
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
    invoked_name = ctx.info_name or "import"
    command_name = f"git.{invoked_name}"
    human_name = "GIT SCAFFOLD" if invoked_name == "scaffold" else "GIT IMPORT"

    if version == "next":
        if not base:
            emit_error(
                command=command_name,
                error=LaunchError(
                    f"--base is required for 'git {invoked_name} next'.",
                    code=CODE_USAGE_ERROR,
                    exit_code=2,
                ),
                json_output=state.json_output,
            )
            raise typer.Exit(2)
        base_display = base
        head_display = head or GIT_DEFAULT_HEAD
        base_spec = base
        head_spec = head_display
        snapshot_source = "explicit"
    else:
        existing = load_release(workspace_root, version)
        try:
            snapshot = resolve_release_snapshot(
                workspace_root,
                existing,
                explicit_base=base or None,
                explicit_head=head or None,
            )
        except LaunchError as exc:
            emit_error(command=command_name, error=exc, json_output=state.json_output)
            raise typer.Exit(launch_error_exit_code(exc)) from exc
        base_display = snapshot.base_ref
        head_display = snapshot.head_ref
        base_spec = snapshot.base_spec
        head_spec = snapshot.head_spec
        snapshot_source = snapshot.source

    try:
        batch = generate_git_scaffold_batch(
            workspace_root,
            release_version=version,
            base_ref=base_spec,
            head_ref=head_spec,
            include_merges=include_merges,
            status=status,
        )
        candidates = collect_git_candidates(
            workspace_root,
            base_ref=base_spec,
            head_ref=head_spec,
            include_merges=include_merges,
        )
        base_sha = str(batch["git_base_sha"])
        head_sha = str(batch["git_head_sha"])
    except LaunchError as exc:
        emit_error(command=command_name, error=exc, json_output=state.json_output)
        raise typer.Exit(launch_error_exit_code(exc)) from exc

    # Write the YAML file.
    try:
        import yaml as _yaml

        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8") as f:
            _yaml.dump(batch, f, default_flow_style=False, sort_keys=False)
    except Exception as exc:
        emit_error(
            command=command_name,
            error=LaunchError(
                f"Failed to write output file {output}: {exc}",
                code=CODE_USAGE_ERROR,
                exit_code=2,
            ),
            json_output=state.json_output,
        )
        raise typer.Exit(2) from exc

    result: dict[str, object] = {
        "kind": "git_scaffold" if invoked_name == "scaffold" else "git_import",
        "version": version,
        "output": str(output),
        "base_ref": base_display,
        "base_sha": base_sha,
        "head_ref": head_display,
        "head_sha": head_sha,
        "snapshot_source": snapshot_source,
        "entry_count": len(candidates),
        "status": status,
        "entries": batch["entries"],
    }
    lines = [f"{human_name} {version}", ""]
    lines.append(f"  output: {output}")
    lines.append(f"  base: {base_display} -> {base_sha[:7]}")
    lines.append(f"  head: {head_display} -> {head_sha[:7]}")
    lines.append(f"  entries: {len(candidates)} (status={status})")
    lines.append("")
    lines.append("Next steps:")
    lines.append(
        "  This is an entry scaffold, not changelog prose. For a durable "
        "review worksheet run:"
    )
    if version == "next":
        lines.append(
            f"  releaseledger audit init {version}"
            f" --base {base_display} --head {head_display}"
        )
    else:
        lines.append(f"  releaseledger audit init {version}")
    lines.append(
        "  edit the YAML and write user-facing summaries from diffs/docs/tests"
    )
    lines.append("  do not copy or paraphrase git commit messages into summaries")
    lines.append(f"  releaseledger entry add-many {version} --file {output} --dry-run")
    lines.append(f"  releaseledger entry add-many {version} --file {output}")
    emit_payload(
        command=("git scaffold" if invoked_name == "scaffold" else "git import"),
        result_type=("git_scaffold" if invoked_name == "scaffold" else "git_import"),
        result=result,
        human="\n".join(lines),
        json_output=state.json_output,
    )


git_app.command("scaffold")(git_import_command)


@git_app.command("evidence")
def git_evidence_command(
    ctx: typer.Context,
    version: Annotated[str, typer.Argument(help="Release version string.")],
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="Directory for manifest and patch files."),
    ],
    base: Annotated[str, typer.Option("--base", help="Base ref override.")] = "",
    head: Annotated[str, typer.Option("--head", help="Head ref override.")] = "",
    include_merges: Annotated[
        str,
        typer.Option("--include-merges", help="Merge policy for evidence export."),
    ] = GIT_DEFAULT_INCLUDE_MERGES,
) -> None:
    """Export deterministic per-commit patch evidence for a release snapshot."""
    state = cli_state_from_context(ctx)
    try:
        workspace_root = _paths(ctx).workspace_root
        release = load_release(workspace_root, version)
        snapshot = resolve_release_snapshot(
            workspace_root,
            release,
            explicit_base=base or None,
            explicit_head=head or None,
        )
        result = export_git_evidence(
            workspace_root,
            release_version=version,
            base_ref=snapshot.base_spec,
            head_ref=snapshot.head_spec,
            include_merges=include_merges,
            output_dir=output_dir,
        )
    except LaunchError as exc:
        emit_error(command="git.evidence", error=exc, json_output=state.json_output)
        raise typer.Exit(launch_error_exit_code(exc)) from exc
    emit_payload(
        command="git.evidence",
        result_type="git_evidence",
        result=result,
        human=f"wrote git evidence for {version} to {output_dir}",
        json_output=state.json_output,
    )


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
        project = load_releaseledger_project(workspace_root)
        result = branch_status(
            workspace_root,
            ledger_ref=project.config.ledger_ref,
            branch_guard=project.config.ledger_branch_guard,
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
        project = load_releaseledger_project(workspace_root)
        result = branch_start(
            workspace_root,
            branch_ref=branch,
            parent_ref=parent,
            current_ledger_ref=project.config.ledger_ref,
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


storage_app = typer.Typer(help="Storage diagnostics and migration.")
app.add_typer(storage_app, name="storage")


@storage_app.command("where")
def storage_where_command(ctx: typer.Context) -> None:
    """Show the effective storage location, layout health, and config source."""
    state = cli_state_from_context(ctx)

    def produce() -> CommandResult:
        result = storage_where(state.cwd)
        lines = [
            f"Project root: {result.get('project_root', '')}",
            f"Project UUID: {result.get('project_uuid', '')}",
            f"Project name: {result.get('project_name', '')}",
            f"Manifest: {result.get('manifest_path', '')}",
            f"Local config: {result.get('local_config_path', '')}",
            f"Tool config: {result.get('tool_config_path', '')}",
            f"Data root: {result.get('data_root', '')}",
            f"Data storage: {result.get('data_storage', '')}",
            f"Data source: {result.get('data_source', '')}",
            f"External root: {result.get('external_root', '')}",
            f"Indexes root: {result.get('indexes_root', '')}",
            f"Active ledger: {result.get('active_ledger_ref', '')}",
            f"Active ledger dir: {result.get('active_ledger_dir', '')}",
            f"Layout valid: {result.get('layout_valid', False)}",
            f"Legacy detected: {result.get('legacy_detected', False)}",
            f"Migration state: {result.get('migration_state', '')}",
        ]
        human = "\n".join(lines)
        return result, [], human

    run_command(
        command="storage.where",
        result_type="storage_location",
        json_output=state.json_output,
        produce=produce,
    )


@storage_app.command("validate")
def storage_validate_command(
    ctx: typer.Context,
    strict: Annotated[
        bool,
        typer.Option(
            "--strict",
            help="Run domain-level validation in addition to binding checks.",
        ),
    ] = False,
) -> None:
    """Validate storage bindings and optionally domain records."""
    state = cli_state_from_context(ctx)

    def produce() -> CommandResult:
        from releaseledger.migration import validate_domain_records

        result = storage_where(state.cwd)
        validation: dict[str, object] = {
            "layout_valid": result.get("layout_valid", False),
            "bindings": result.get("bindings", {}),
        }
        if strict:
            data_root = Path(str(result.get("data_root", "")))
            if data_root.is_dir():
                domain = validate_domain_records(data_root)
                validation["domain"] = domain

        lines = [f"Layout valid: {validation.get('layout_valid', False)}"]
        bindings = validation.get("bindings", {})
        if isinstance(bindings, dict):
            for name, status in bindings.items():
                lines.append(f"  {name}: {status}")
        passed = bool(validation.get("layout_valid", False))
        if "domain" in validation:
            domain = validation["domain"]  # type: ignore[assignment]
            lines.append(f"Domain records valid: {domain.get('valid', False)}")
            lines.append(f"Domain failures: {domain.get('total_failures', 0)}")
            passed = passed and bool(domain.get("valid", False))
        validation["passed"] = passed
        human = "\n".join(lines)
        return validation, [], human

    run_command(
        command="storage.validate",
        result_type="storage_validate",
        json_output=state.json_output,
        produce=produce,
        check_passed=None,
    )


@storage_app.command("set")
def storage_set_command(
    ctx: typer.Context,
    mount: Annotated[
        str,
        typer.Argument(help="Mount to configure: data."),
    ] = "data",
    data_storage: Annotated[
        str,
        typer.Option(
            "--storage",
            help="Storage kind: project, external, or user-data.",
        ),
    ] = "project",
    root: Annotated[
        str | None,
        typer.Option("--root", hidden=True),
    ] = None,
    storage_root: Annotated[
        str | None,
        typer.Option("--storage-root", help="External root path."),
    ] = None,
    target: Annotated[
        str | None,
        typer.Option("--target", hidden=True),
    ] = None,
    scope: Annotated[
        str,
        typer.Option("--scope", help="Write scope: project or local."),
    ] = "project",
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Show the planned change without applying it."),
    ] = False,
    migrate_flag: Annotated[
        bool,
        typer.Option("--migrate", help="Migrate existing data after changing storage."),
    ] = False,
) -> None:
    """Set the data mount storage kind."""
    state = cli_state_from_context(ctx)
    if root is not None:
        add_cli_warning(
            deprecated_option_warning("storage set --root", "--storage-root")
        )
    if target is not None:
        add_cli_warning(deprecated_option_warning("storage set --target", "--scope"))
    effective_root = storage_root if storage_root is not None else root
    effective_scope = target if target is not None else scope

    def produce() -> CommandResult:
        if mount != "data":
            raise LaunchError(
                f"Only the 'data' mount is user-configurable; got {mount!r}.",
                code=CODE_USAGE_ERROR,
                exit_code=2,
            )
        if migrate_flag:
            raise LaunchError(
                "--migrate is no longer a storage-set shortcut; use explicit migration commands.",
                code=CODE_USAGE_ERROR,
                exit_code=2,
                remediation=[
                    "Run `migrate plan storage-layout` then `migrate apply storage-layout`."
                ],
            )
        if data_storage == "cache":
            raise LaunchError(
                "Authoritative Releaseledger data cannot use cache storage.",
                code=CODE_USAGE_ERROR,
                exit_code=2,
                remediation=["Choose project, external, or user-data storage."],
            )
        from releaseledger.ledgercore_backend import set_releaseledger_data_target

        if not dry_run:
            set_releaseledger_data_target(
                state.cwd,
                storage=data_storage,
                external_root=effective_root,
                target=effective_scope,
            )
        human = (
            f"data storage set to {data_storage}"
            + (f" (external root: {effective_root})" if effective_root else "")
            + f" via {effective_scope}"
        )
        if dry_run:
            return (
                {
                    "dry_run": True,
                    "storage": data_storage,
                    "storage_root": effective_root,
                    "scope": effective_scope,
                },
                [],
                human,
            )
        return (
            {
                "storage": data_storage,
                "storage_root": effective_root,
                "scope": effective_scope,
            },
            [],
            human,
        )

    run_command(
        command="storage.set",
        result_type="storage_set",
        json_output=state.json_output,
        produce=produce,
    )


@storage_app.command("clear-override")
def storage_clear_override_command(
    ctx: typer.Context,
    mount: Annotated[
        str,
        typer.Argument(help="Mount to clear: data."),
    ] = "data",
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    reason: Annotated[str | None, typer.Option("--reason")] = None,
) -> None:
    """Remove a local data mount override."""
    state = cli_state_from_context(ctx)

    def produce() -> CommandResult:
        if mount != "data":
            raise LaunchError(
                f"Only the 'data' mount override can be cleared; got {mount!r}.",
                code=CODE_USAGE_ERROR,
                exit_code=2,
            )
        from releaseledger.ledgercore_backend import clear_releaseledger_data_override

        if not dry_run:
            clear_releaseledger_data_override(state.cwd)
        effective = storage_where(state.cwd)
        human = "data override clear previewed" if dry_run else "data override cleared"
        return (
            {
                "cleared": not dry_run,
                "dry_run": dry_run,
                "mount": mount,
                "effective": effective,
            },
            [],
            human,
        )

    run_command(
        command="storage.clear-override",
        result_type="storage_clear_override",
        json_output=state.json_output,
        produce=produce,
        mutating=not dry_run,
    )


@storage_app.command("migrate")
def storage_migrate_command(
    ctx: typer.Context,
    subcommand: Annotated[
        str,
        typer.Argument(help="Migration subcommand: plan, apply, status, or recover."),
    ] = "status",
    data_storage: Annotated[
        str,
        typer.Option(
            "--storage",
            help="Target data storage: project, external, or user-data.",
        ),
    ] = "project",
    root: Annotated[
        str | None,
        typer.Option("--root", help="External root for target storage."),
    ] = None,
    target: Annotated[
        str,
        typer.Option("--target", help="Write target: project or local."),
    ] = "project",
    mode: Annotated[
        str,
        typer.Option("--mode", help="Migration mode: copy or move."),
    ] = "copy",
    preserve_legacy_config: Annotated[
        bool,
        typer.Option(
            "--preserve-legacy-config",
            help="Keep the legacy config file after move migration.",
        ),
    ] = False,
) -> None:
    """Plan or execute storage migration from legacy to schema-3."""
    state = cli_state_from_context(ctx)
    if "storage migrate" in ctx.command_path:
        add_cli_warning(
            deprecated_command_warning("storage migrate", f"migrate {subcommand}")
        )

    def produce() -> CommandResult:
        from releaseledger.migration import (
            ReleaseledgerMigrationRequest,
            execute_migration,
            plan_migration,
            recover_migration,
        )
        from releaseledger.migration import (
            migration_status as mig_status,
        )

        if mode == "move":
            raise LaunchError(
                "Move mode is disabled; use copy-only migration and explicit cleanup.",
                code="migration_move_disabled",
                exit_code=2,
            )

        if subcommand == "status":
            result = mig_status(state.cwd)
            human = f"Migration state: {result.get('state', 'unknown')}"
            return result, [], human

        if subcommand == "plan":
            request = ReleaseledgerMigrationRequest(
                start=state.cwd,
                data_storage=cast(
                    Literal["project", "external", "user-data"], data_storage
                ),
                external_root=root,
                target=cast(Literal["project", "local"], target),
                mode="copy",
                preserve_legacy_config=preserve_legacy_config,
            )
            result = plan_migration(request)
            human = (
                f"Migration plan for {result.get('legacy_data_root', '')} "
                f"-> {data_storage} ({mode})"
            )
            return result, [], human

        if subcommand == "apply":
            from releaseledger.storage.locking import (
                acquire_write_lock,
                quiescence_callback,
            )

            request = ReleaseledgerMigrationRequest(
                start=state.cwd,
                data_storage=cast(
                    Literal["project", "external", "user-data"], data_storage
                ),
                external_root=root,
                target=cast(Literal["project", "local"], target),
                mode="copy",
                preserve_legacy_config=preserve_legacy_config,
            )
            with acquire_write_lock(state.cwd) as lock:
                result = execute_migration(
                    request, quiescence_check=lambda: quiescence_callback(lock)
                )
            migrated_count = result.get("inventory", {}).get("total_releases", 0)  # type: ignore[attr-defined]
            human = (
                f"Migration {mode} completed to {data_storage} "
                f"({migrated_count} releases migrated)"
            )
            return result, [], human

        if subcommand == "recover":
            result = recover_migration(state.cwd, policy="auto")
            human = result.get("message", "Recovery attempted.")  # type: ignore[assignment]
            return result, [], human

        raise LaunchError(
            f"Unknown migration subcommand: {subcommand!r}.",
            code=CODE_USAGE_ERROR,
            exit_code=2,
            remediation=[
                "Use: plan, apply, status, or recover.",
            ],
        )

    run_command(
        command=f"migrate {subcommand}",
        result_type="storage_migrate",
        json_output=state.json_output,
        produce=produce,
        branch_guard_policy="if-canonical-project"
        if subcommand == "apply"
        else "default",
    )


def _migration_command_result(
    ctx: typer.Context,
    operation: str,
    *,
    migration: str,
    data_storage: str = "project",
    storage_root: str | None = None,
    scope: str = "project",
    output: Path | None = None,
    plan_file: Path | None = None,
    dry_run: bool = False,
    journal: Path | None = None,
    policy: str = "auto",
    yes: bool = False,
    reason: str | None = None,
) -> CommandResult:
    """Execute one canonical migration operation through the CLI boundary."""
    from releaseledger.migration import (
        ReleaseledgerMigrationRequest,
        cleanup_migration,
        execute_migration,
        load_migration_plan,
        migration_status,
        plan_migration,
        recover_migration,
        validate_migration_plan,
    )

    root = cli_state_from_context(ctx).root
    if migration != "storage-layout":
        raise LaunchError(
            f"Unknown migration: {migration!r}.",
            code=CODE_USAGE_ERROR,
            exit_code=2,
            remediation=["Use `storage-layout`."],
        )
    if operation == "status":
        result = migration_status(root)
        return result, [], f"Migration state: {result.get('state', 'unknown')}"
    if operation == "recover":
        if policy not in {"auto", "resume", "rollback"}:
            raise LaunchError(
                "Recovery policy must be auto, resume, or rollback.",
                code=CODE_USAGE_ERROR,
                exit_code=2,
            )
        if not dry_run and (not reason or not reason.strip()):
            raise LaunchError(
                "Migration recovery writes require a reason.",
                code=CODE_USAGE_ERROR,
                exit_code=2,
            )
        result = recover_migration(
            root,
            journal=journal,
            dry_run=dry_run,
            policy=cast(Literal["auto", "resume", "rollback"], policy),
            yes=yes,
            reason=reason,
        )
        return result, [], str(result.get("message", "Recovery attempted."))
    if operation == "cleanup":
        result = cleanup_migration(root, yes=yes, dry_run=dry_run, reason=reason)
        return (
            result,
            [],
            (
                "Migration cleanup previewed"
                if dry_run
                else f"Migration cleanup removed {len(result.get('removed', []))} path(s)"  # type: ignore[arg-type]
            ),
        )

    if operation == "apply" and not dry_run and (not reason or not reason.strip()):
        raise LaunchError(
            "Migration apply requires a reason.",
            code=CODE_USAGE_ERROR,
            exit_code=2,
            remediation=["Provide --reason describing the migration decision."],
        )

    if operation == "apply" and plan_file is not None:
        plan = load_migration_plan(plan_file)
        validation = validate_migration_plan(plan, root)
        if dry_run:
            return (
                {
                    "kind": "migration_apply_preview",
                    "migration": migration,
                    "plan": plan,
                    "validation": validation,
                    "dry_run": True,
                },
                [],
                "Migration apply validated (dry-run)",
            )
        destination = plan.get("target", plan.get("destination", {}))
        assert isinstance(destination, dict)
        # Use the plan's authoritative project UUID.
        plan_project = plan.get("project", {})
        plan_uuid = (
            plan_project.get("uuid")
            if isinstance(plan_project, dict)
            else plan.get("project_uuid")
        )
        request = ReleaseledgerMigrationRequest(
            start=root,
            data_storage=str(destination.get("storage", "project")),  # type: ignore[arg-type]
            external_root=(
                str(destination.get("external_root"))
                if destination.get("external_root")
                else None
            ),
            target=str(destination.get("scope", "project")),  # type: ignore[arg-type]
            mode="copy",
            project_uuid=str(plan_uuid) if plan_uuid else None,
            reason=reason,
        )
        from releaseledger.storage.locking import (
            acquire_write_lock,
            quiescence_callback,
        )

        with acquire_write_lock(root) as lock:
            result = execute_migration(
                request,
                migration_plan=plan,
                quiescence_check=lambda: quiescence_callback(lock),
            )
        return result, [], "Migration applied from verified plan"

    request = ReleaseledgerMigrationRequest(
        start=root,
        data_storage=data_storage,  # type: ignore[arg-type]
        external_root=storage_root,
        target=scope,  # type: ignore[arg-type]
        mode="copy",
        reason=reason,
    )
    plan = plan_migration(request)
    if operation == "plan" or dry_run:
        result = dict(plan)
        result["dry_run"] = dry_run
        if output is not None:
            if str(output) == "-" and cli_state_from_context(ctx).json_output:
                raise LaunchError(
                    "--output - cannot be combined with global --json; choose raw plan output or an envelope.",
                    code=CODE_USAGE_ERROR,
                    exit_code=2,
                )
            # Write the plan itself (without extra metadata) to preserve hash.
            write_text_output(output, render_json(plan))
            result["output"] = str(output)
        return (
            result,
            [],
            (
                f"wrote migration plan to {output}"
                if output is not None
                else "Migration plan ready"
            ),
        )
    from releaseledger.storage.locking import acquire_write_lock, quiescence_callback

    with acquire_write_lock(root) as lock:
        result = execute_migration(
            request,
            quiescence_check=lambda: quiescence_callback(lock),
        )
    return result, [], "Migration applied"


migrate_app = typer.Typer(help="Plan and execute named migrations.")
app.add_typer(migrate_app, name="migrate")


@migrate_app.command("status")
def migrate_status_command(ctx: typer.Context) -> None:
    """Show migration status."""
    state = cli_state_from_context(ctx)
    run_command(
        command="migrate status",
        result_type="migration_status",
        json_output=state.json_output,
        produce=lambda: _migration_command_result(
            ctx, "status", migration="storage-layout"
        ),
    )


def _migrate_common_options(
    ctx: typer.Context,
    operation: str,
    migration: str,
    data_storage: str,
    storage_root: str | None,
    scope: str,
    mode: str,
    preserve_legacy_config: bool,
) -> None:
    if migration != "storage-layout":
        error = LaunchError(
            f"Unknown migration: {migration!r}.",
            code=CODE_USAGE_ERROR,
            exit_code=2,
            remediation=["Use `storage-layout`."],
        )
        state = cli_state_from_context(ctx)
        emit_error(
            command=f"migrate {operation}", error=error, json_output=state.json_output
        )
        raise typer.Exit(2)
    ctx.invoke(
        storage_migrate_command,
        subcommand=operation,
        data_storage=data_storage,
        root=storage_root,
        target=scope,
        mode=mode,
        preserve_legacy_config=preserve_legacy_config,
    )


@migrate_app.command("plan")
def migrate_plan_command(
    ctx: typer.Context,
    migration: Annotated[str, typer.Argument()] = "storage-layout",
    data_storage: Annotated[str, typer.Option("--storage")] = "project",
    storage_root: Annotated[str | None, typer.Option("--storage-root")] = None,
    scope: Annotated[str, typer.Option("--scope")] = "project",
    mode: Annotated[str, typer.Option("--mode", hidden=True)] = "copy",
    preserve_legacy_config: Annotated[
        bool, typer.Option("--preserve-legacy-config", hidden=True)
    ] = False,
    output: Annotated[Path | None, typer.Option("--output")] = None,
) -> None:
    """Plan the named storage-layout migration."""
    state = cli_state_from_context(ctx)
    run_command(
        command="migrate plan",
        result_type="migration_plan",
        json_output=state.json_output,
        produce=lambda: _migration_command_result(
            ctx,
            "plan",
            migration=migration,
            data_storage=data_storage,
            storage_root=storage_root,
            scope=scope,
            output=output,
        ),
    )


@migrate_app.command("apply")
def migrate_apply_command(
    ctx: typer.Context,
    migration: Annotated[str, typer.Argument()] = "storage-layout",
    data_storage: Annotated[str, typer.Option("--storage")] = "project",
    storage_root: Annotated[str | None, typer.Option("--storage-root")] = None,
    scope: Annotated[str, typer.Option("--scope")] = "project",
    mode: Annotated[str, typer.Option("--mode", hidden=True)] = "copy",
    preserve_legacy_config: Annotated[
        bool, typer.Option("--preserve-legacy-config", hidden=True)
    ] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    plan_file: Annotated[Path | None, typer.Option("--plan-file")] = None,
    reason: Annotated[str | None, typer.Option("--reason")] = None,
) -> None:
    """Apply the named storage-layout migration."""
    state = cli_state_from_context(ctx)
    run_command(
        command="migrate apply",
        result_type="migration_apply",
        json_output=state.json_output,
        produce=lambda: _migration_command_result(
            ctx,
            "apply",
            migration=migration,
            data_storage=data_storage,
            storage_root=storage_root,
            scope=scope,
            plan_file=plan_file,
            dry_run=dry_run,
            reason=reason,
        ),
        workspace_root=state.root,
        mutating=not dry_run,
        branch_guard_policy="if-canonical-project",
    )


@migrate_app.command("recover")
def migrate_recover_command(
    ctx: typer.Context,
    journal: Annotated[Path | None, typer.Option("--journal")] = None,
    policy: Annotated[
        str,
        typer.Option("--policy", help="Recovery policy: auto, resume, or rollback."),
    ] = "auto",
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    yes: Annotated[bool, typer.Option("--yes")] = False,
    reason: Annotated[str | None, typer.Option("--reason")] = None,
) -> None:
    """Recover an interrupted storage-layout migration."""
    state = cli_state_from_context(ctx)
    run_command(
        command="migrate recover",
        result_type="migration_recovery",
        json_output=state.json_output,
        produce=lambda: _migration_command_result(
            ctx,
            "recover",
            migration="storage-layout",
            journal=journal,
            policy=policy,
            dry_run=dry_run,
            reason=reason,
            yes=yes,
        ),
        workspace_root=state.root,
        mutating=not dry_run,
        branch_guard_policy="if-canonical-project",
    )


@migrate_app.command("cleanup")
def migrate_cleanup_command(
    ctx: typer.Context,
    migration: Annotated[str, typer.Argument()] = "storage-layout",
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    yes: Annotated[bool, typer.Option("--yes")] = False,
    reason: Annotated[str | None, typer.Option("--reason")] = None,
) -> None:
    """Explicitly clean verified legacy state after migration."""
    state = cli_state_from_context(ctx)
    run_command(
        command="migrate cleanup",
        result_type="migration_cleanup",
        json_output=state.json_output,
        produce=lambda: _migration_command_result(
            ctx,
            "cleanup",
            migration=migration,
            dry_run=dry_run,
            yes=yes,
            reason=reason,
        ),
        workspace_root=state.root,
        mutating=not dry_run,
    )


# ---------------------------------------------------------------------------
# Config commands
# ---------------------------------------------------------------------------


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
            f"Project: {result.get('project_name', '')}",
            f"Config path: {result.get('config_path', '')}",
            f"Config version: {cfg.get('config_version', '')}",
            f"Ledger ref: {cfg.get('ledger_ref', '')}",
            f"Ledger parent: {cfg.get('ledger_parent_ref', '')}",
            f"Ledger code: {cfg.get('ledger_code', '')}",
            f"Branch guard: {cfg.get('ledger_branch_guard', 'off')}",
        ]
        human = "\n".join(lines)
        return result, [], human

    run_command(
        command="config.show",
        result_type="config_show",
        json_output=state.json_output,
        produce=produce,
    )


@config_app.command("validate")
def config_validate_command(
    ctx: typer.Context,
    strict: Annotated[bool, typer.Option("--strict")] = False,
) -> None:
    """Validate configuration and storage without writing state."""
    state = cli_state_from_context(ctx)

    def produce() -> CommandResult:
        result = config_validate(state.root, strict=strict)
        issues = result.get("issues", [])
        human = (
            "config validation passed"
            if result.get("valid")
            else "config validation failed\n"
            + "\n".join(
                f"- {item.get('code')}: {item.get('message')}"
                for item in issues  # type: ignore[attr-defined]
                if isinstance(item, dict)
            )
        )
        return result, [], human

    run_command(
        command="config validate",
        result_type="config_validation",
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
    """Atomically set a config key. Storage keys are no longer supported."""
    state = cli_state_from_context(ctx)
    if key == "releaseledger_dir":
        err = LaunchError(
            "config set releaseledger_dir is no longer supported; "
            "storage topology is owned by the canonical Ledger project. "
            "Use `releaseledger storage set data --storage ...` instead.",
            code=CODE_USAGE_ERROR,
            exit_code=2,
            remediation=[
                "Use `releaseledger storage set data --storage ...` "
                "to change data storage.",
            ],
        )
        emit_error(command="config.set", error=err, json_output=state.json_output)
        raise typer.Exit(launch_error_exit_code(err)) from err
    else:
        err = LaunchError(
            f"Unrecognized config key: {key!r}. ",
            code=CODE_USAGE_ERROR,
            exit_code=2,
            remediation=[
                "Valid keys include: ledger_ref, ledger_code, "
                "default_status, changelog_output, etc. "
                "Use `releaseledger config show` to see current values."
            ],
        )
        emit_error(command="config.set", error=err, json_output=state.json_output)
        raise typer.Exit(launch_error_exit_code(err)) from err


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
        typer.Option("--format", help="Output format: markdown, json, or yaml."),
    ] = "markdown",
) -> None:
    """Create the canonical commit audit sheet from the git range."""
    state = cli_state_from_context(ctx)
    if format_name not in {"markdown", "json", "yaml"}:
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
        str | None,
        typer.Option("--format", help="Output format: markdown, json, or yaml."),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option("--output", help="Write rendered output to a file."),
    ] = None,
) -> None:
    """Render the commit audit sheet for display or export."""
    state = cli_state_from_context(ctx)
    effective_format = format_name or ("json" if state.json_output else "markdown")
    if effective_format not in {"markdown", "json", "yaml"}:
        err = ReleaseledgerError(
            f"Unsupported --format: {effective_format!r}",
            code="USAGE_ERROR",
            exit_code=2,
        )
        emit_error(command="audit.show", error=err, json_output=state.json_output)
        raise typer.Exit(launch_error_exit_code(err)) from err
    try:
        workspace_root = _paths(ctx).workspace_root
        rendered = render_commit_audit_sheet(
            workspace_root, version=version, format_name=effective_format
        )
    except ReleaseledgerError as exc:
        emit_error(command="audit.show", error=exc, json_output=state.json_output)
        raise typer.Exit(launch_error_exit_code(exc)) from exc
    if output is not None:
        text = render_json(rendered) if isinstance(rendered, dict) else str(rendered)
        try:
            write_text_output(output, text)
        except ReleaseledgerError as exc:
            emit_error(command="audit.show", error=exc, json_output=state.json_output)
            raise typer.Exit(launch_error_exit_code(exc)) from exc
        human = f"wrote {output}"
    elif effective_format == "json":
        human = ""
    else:
        human = str(rendered)
    payload: dict[str, object] = {"version": version, "format": effective_format}
    if isinstance(rendered, dict):
        payload["sheet"] = rendered
    elif effective_format == "yaml":
        payload["yaml"] = rendered
    else:
        payload["document"] = rendered
    emit_payload(
        command="audit.show",
        result_type="commit_audit_sheet",
        result=payload,
        human=human,
        json_output=state.json_output,
    )


@audit_app.command("apply")
def audit_apply_command(
    ctx: typer.Context,
    version: Annotated[str, typer.Argument(help="Release version string.")],
    file: Annotated[Path, typer.Option("--file", help="Row-annotation YAML file.")],
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Merge row-annotation updates into the canonical commit audit sheet."""
    state = cli_state_from_context(ctx)

    def produce() -> CommandResult:
        workspace_root = _paths(ctx).workspace_root
        result = apply_commit_audit_annotations(
            workspace_root,
            version=version,
            file=file,
            dry_run=dry_run,
        )
        action = "previewed" if dry_run or not result.get("written") else "applied"
        human = (
            f"{action} audit annotations for {version}: "
            f"{_as_int(result['updated_rows'])} row(s) updated "
            f"(revision {_as_int(result['revision'])})"
        )
        return result, [], human

    run_command(
        command="audit.apply",
        result_type="commit_audit_apply",
        json_output=state.json_output,
        produce=produce,
    )


@audit_app.command("refresh")
def audit_refresh_command(
    ctx: typer.Context,
    version: Annotated[str, typer.Argument(help="Release version string.")],
    base: Annotated[
        str,
        typer.Option("--base", help="Git base ref override."),
    ] = "",
    head: Annotated[
        str,
        typer.Option("--head", help="Git head ref override."),
    ] = "",
    allow_remove: Annotated[
        bool,
        typer.Option(
            "--allow-remove",
            help="Allow commits to disappear from the refreshed audit range.",
        ),
    ] = False,
) -> None:
    """Reconcile an existing audit sheet with a refreshed git snapshot."""
    state = cli_state_from_context(ctx)

    def produce() -> CommandResult:
        result = refresh_commit_audit_sheet(
            _paths(ctx).workspace_root,
            version=version,
            git_base=base or None,
            git_head=head or None,
            allow_remove=allow_remove,
        )
        action = "refreshed" if result.get("written") else "checked"
        human = (
            f"{action} audit sheet for {version}: "
            f"preserved={_as_int(result['preserved_reviewed_rows'])} "
            f"new={_as_int(result['new_rows'])} "
            f"removed={_as_int(result['removed_rows'])} "
            f"(revision {_as_int(result['revision'])})"
        )
        return result, [], human

    run_command(
        command="audit.refresh",
        result_type="commit_audit_refresh",
        json_output=state.json_output,
        produce=produce,
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
    phase: Annotated[
        str,
        typer.Option("--phase", help="Validation phase: evidence or complete."),
    ] = "complete",
    strict: Annotated[bool, typer.Option("--strict")] = False,
    include_internal: Annotated[
        bool,
        typer.Option("--include-internal", help="Check internal row coverage."),
    ] = False,
    record_event: Annotated[
        bool,
        typer.Option(
            "--record-event",
            help="Deprecated compatibility path; use audit record-validation.",
        ),
    ] = False,
) -> None:
    """Validate the audit sheet against release entries and git coverage."""
    state = cli_state_from_context(ctx)
    if record_event:
        add_cli_warning(
            deprecated_option_warning(
                "audit validate --record-event", "audit record-validation"
            )
        )
    try:
        workspace_root = _paths(ctx).workspace_root
        result = validate_commit_audit_sheet(
            workspace_root,
            version=version,
            phase=phase,
            strict=strict,
            include_internal=include_internal,
            record_event=record_event,
        )
    except ReleaseledgerError as exc:
        emit_error(command="audit validate", error=exc, json_output=state.json_output)
        raise typer.Exit(launch_error_exit_code(exc)) from exc
    ok = bool(result.get("ok"))
    if ok:
        human = f"audit {phase} validation passed for {version}"
    else:
        needs = _as_int(result.get("needs_review_count", 0))
        uninsp = _as_int(result.get("uninspected_count", 0))
        missing = len(result.get("missing_entry_coverage", []))  # type: ignore[arg-type]
        human = (
            f"audit {phase} validation for {version}: ok=false "
            f"(needs_review={needs}, uninspected={uninsp}, "
            f"missing_coverage={missing})"
        )
    emit_payload(
        command="audit validate",
        result_type="commit_audit_validation",
        result=result,
        human=human,
        json_output=state.json_output,
    )
    if not ok:
        raise typer.Exit(1)


@audit_app.command("record-validation")
def audit_record_validation_command(
    ctx: typer.Context,
    version: Annotated[str, typer.Argument(help="Release version string.")],
    reason: Annotated[str, typer.Option("--reason")],
    phase: Annotated[str, typer.Option("--phase")] = "complete",
    strict: Annotated[bool, typer.Option("--strict")] = False,
    include_internal: Annotated[bool, typer.Option("--include-internal")] = False,
) -> None:
    """Validate and explicitly append an audit validation event."""
    state = cli_state_from_context(ctx)

    def produce() -> CommandResult:
        if not reason or not reason.strip():
            raise LaunchError(
                "--reason is required when recording validation.",
                code=CODE_USAGE_ERROR,
                exit_code=2,
            )
        result = validate_commit_audit_sheet(
            _paths(ctx).workspace_root,
            version=version,
            phase=phase,
            strict=strict,
            include_internal=include_internal,
            record_event=True,
            reason=reason,
        )
        result["reason"] = reason
        return result, [], f"recorded audit validation for {version}"

    run_command(
        command="audit record-validation",
        result_type="commit_audit_validation",
        json_output=state.json_output,
        produce=produce,
        workspace_root=_paths(ctx).workspace_root,
        mutating=True,
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
