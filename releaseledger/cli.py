"""Releaseledger command-line interface.

The root :data:`app` exposes ``--cwd``, ``--json`` and ``--version`` and stores
a :class:`~releaseledger.cli_common.CLIState` on the typer context for
subcommands. Subcommand groups are registered progressively (``init``,
``release``, ``entry``, ``changelog``) at the bottom of this module.
"""

from __future__ import annotations

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
from releaseledger.errors import ReleaseledgerError
from releaseledger.services.changelog import build_changelog_context
from releaseledger.services.changelog_build import build_changelog_file
from releaseledger.services.entries import (
    add_release_entry,
    list_release_entries,
)
from releaseledger.services.releases import (
    create_release,
    finalize_release,
    list_release_records,
    show_release,
    tag_release,
)
from releaseledger.storage.paths import (
    ProjectPaths,
    initialize_project,
    require_project,
)

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
        )
        rel_dir = Path(str(result["releaseledger_dir"]))
        try:
            display = rel_dir.relative_to(workspace_root.resolve())
            display_str = str(display)
        except ValueError:
            display_str = str(rel_dir)
        human = (
            f"initialized releaseledger in {display_str}\n"
            f"wrote .releaseledger.toml"
        )
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
    date_value = record.get("released_at") or record.get("created_at") or ""
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
        )
        return result, _event_ids(result), f"created release {version}"

    run_command(
        command="release.create",
        result_type="release",
        json_output=state.json_output,
        produce=produce,
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
        )
        return result, _event_ids(result), f"tagged release {version}"

    run_command(
        command="release.tag",
        result_type="release",
        json_output=state.json_output,
        produce=produce,
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


def _event_ids(result: dict[str, object]) -> list[str]:
    events = result.get("events")
    if isinstance(events, list):
        return [str(item) for item in events]
    return []


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
    breaking: Annotated[
        bool,
        typer.Option("--breaking", help="Mark as a breaking change."),
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
            breaking=breaking,
            internal=internal,
        )
        entry_raw = result.get("entry", {})
        entry = dict(entry_raw) if isinstance(entry_raw, dict) else {}
        entry_id = str(entry.get("entry_id", ""))
        human = f"added entry {entry_id} to release {version}"
        return result, _event_ids(result), human

    run_command(
        command="entry.add",
        result_type="release_entry",
        json_output=state.json_output,
        produce=produce,
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
            target_changelog=target_changelog,
            release_date=release_date,
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


@app.command("build")
def build_command(
    ctx: typer.Context,
    version: Annotated[str, typer.Argument(help="Release version string.")],
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
        typer.Option("--dry-run", help="Print rendered section; do not write."),
    ] = False,
    replace_existing: Annotated[
        bool,
        typer.Option(
            "--replace-existing",
            help="Replace an existing section for VERSION.",
        ),
    ] = False,
    format_name: Annotated[
        str,
        typer.Option("--format", help="Output format: markdown or json."),
    ] = "markdown",
) -> None:
    """Build or update CHANGELOG.md for a release."""
    state = cli_state_from_context(ctx)
    if format_name not in {"markdown", "json"}:
        err = ReleaseledgerError(
            f"Unsupported --format: {format_name!r}",
            code="USAGE_ERROR",
            exit_code=2,
        )
        emit_error(command="build", error=err, json_output=state.json_output)
        raise typer.Exit(launch_error_exit_code(err)) from err
    try:
        workspace_root = _paths(ctx).workspace_root
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
        )
    except ReleaseledgerError as exc:
        emit_error(command="build", error=exc, json_output=state.json_output)
        raise typer.Exit(launch_error_exit_code(exc)) from exc
    target = str(result.get("target_file", ""))
    if dry_run:
        human = str(result.get("section", ""))
    else:
        human = f"wrote {target}"
    emit_payload(
        command="build",
        result_type="changelog_build",
        result=result,
        human=human,
        json_output=state.json_output,
    )
