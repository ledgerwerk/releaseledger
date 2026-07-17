"""Shared CLI plumbing: state, deterministic JSON envelopes, and text output.

Services never print or call ``typer.Exit``; they raise :class:`LaunchError` and
return dict payloads. The command boundary in :mod:`releaseledger.cli` uses these
helpers to render either a human line or a JSON envelope, and to write files
atomically when ``--output`` is requested.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeAlias

import ledgercore
import typer

from releaseledger.errors import (
    CODE_USAGE_ERROR,
    LaunchError,
    ReleaseledgerError,
    to_error_payload,
)

__all__ = [
    "CLIState",
    "cli_state_from_context",
    "emit_error",
    "emit_payload",
    "launch_error_exit_code",
    "render_json",
    "resolve_workspace_root",
    "run_command",
    "store_cli_state",
    "write_text_output",
]


# A command body returns (result_dict, event_ids, optional human text).
CommandResult: TypeAlias = tuple[dict[str, object], list[str], str | None]


@dataclass(slots=True)
class CLIState:
    """Resolved per-invocation CLI options shared across subcommands.

    Attributes:
        cwd: Effective working directory (root for config discovery).
        json_output: When true, subcommands emit JSON envelopes.
    """

    cwd: Path
    json_output: bool


def resolve_workspace_root(cwd: Path | None) -> Path:
    """Resolve the effective workspace root from an optional ``--cwd`` value."""
    if cwd is None:
        return Path.cwd()
    resolved = Path(cwd).expanduser()
    return resolved.resolve()


def render_json(payload: object) -> str:
    """Render deterministic JSON (sorted keys, final newline)."""
    return ledgercore.dumps_json(payload)


def store_cli_state(ctx: typer.Context, state: CLIState) -> None:
    """Persist the resolved :class:`CLIState` on the typer context object."""
    ctx.ensure_object(dict)
    obj: dict[str, Any] = ctx.obj
    obj["state"] = state


def cli_state_from_context(ctx: typer.Context) -> CLIState:
    """Return the :class:`CLIState` stored by the root callback.

    The root callback always stores state before subcommands run, so a missing
    state here indicates a programming error in the CLI wiring.
    """
    obj: dict[str, Any] | None = getattr(ctx, "obj", None)
    state: object | None = obj.get("state") if isinstance(obj, dict) else None
    if isinstance(state, CLIState):
        return state
    # Defensive fallback for direct command invocation without the callback.
    return CLIState(cwd=resolve_workspace_root(None), json_output=False)


def emit_payload(
    *,
    command: str,
    result_type: str,
    result: dict[str, object],
    events: list[str] | None = None,
    human: str | None = None,
    json_output: bool,
) -> None:
    """Render a success payload as JSON or a human line.

    ``human`` is shown verbatim for human mode; JSON mode emits the full
    envelope with sorted keys and a trailing newline.
    """
    if json_output:
        payload: dict[str, object] = {
            "ok": True,
            "command": command,
            "result_type": result_type,
            "result": result,
        }
        if events is not None:
            payload["events"] = list(events)
        typer.echo(render_json(payload))
        return
    if human is not None:
        typer.echo(human)


def emit_error(
    *,
    command: str,
    error: ReleaseledgerError,
    json_output: bool,
    human: str | None = None,
) -> None:
    """Render an error payload as JSON (stdout) or a human line (stderr)."""
    if json_output:
        payload: dict[str, object] = {
            "ok": False,
            "command": command,
            "error": to_error_payload(error),
        }
        typer.echo(render_json(payload))
        return
    message = human if human is not None else error.message
    typer.echo(message, err=True)


def launch_error_exit_code(error: ReleaseledgerError) -> int:
    """Return the process exit code associated with an error (never zero)."""
    code = error.exit_code
    if code == 0:
        # Defensive: a zero exit code for an error would mask failures.
        return 1
    return code


def write_text_output(path: Path, text: str) -> Path:
    """Write rendered text to ``path`` atomically and return the path.

    Used for ``--output`` file rendering (changelogs, JSON dumps).
    """
    try:
        ledgercore.atomic_write_text(path, text)
    except ledgercore.AtomicWriteError as exc:  # pragma: no cover - fs failure
        raise LaunchError(
            f"Failed to write output file {path}: {exc}",
            code=CODE_USAGE_ERROR,
            exit_code=2,
        ) from exc
    return path


def run_command(
    *,
    command: str,
    result_type: str,
    json_output: bool,
    produce: Callable[[], CommandResult],
    workspace_root: Path | None = None,
    mutating: bool = False,
) -> None:
    """Run a command body, emitting a success or error envelope.

    ``produce`` returns ``(result_dict, events, human)``. A
    :class:`ReleaseledgerError` raised by the service layer is turned into the
    error envelope and a non-zero typer exit.
    """
    if mutating and workspace_root is not None:
        check_mutating_branch_guard(
            workspace_root, json_output=json_output, command=command
        )
    try:
        result, events, human = produce()
    except ReleaseledgerError as exc:
        emit_error(command=command, error=exc, json_output=json_output)
        raise typer.Exit(launch_error_exit_code(exc)) from exc
    emit_payload(
        command=command,
        result_type=result_type,
        result=result,
        events=events,
        human=human,
        json_output=json_output,
    )


def check_mutating_branch_guard(
    workspace_root: Path,
    *,
    json_output: bool,
    command: str,
) -> None:
    """Enforce ledger_branch_guard for mutating commands (design §9.6).

    When the guard is 'warn', prints a warning to stderr. When 'on', raises
    a BranchGuardViolation that the caller should catch as a ReleaseledgerError.
    Read-only commands do not call this.
    """
    try:
        from releaseledger.services.branch import check_branch_guard
        from releaseledger.storage.paths import load_releaseledger_project

        project = load_releaseledger_project(workspace_root)
        warning = check_branch_guard(
            workspace_root,
            ledger_ref=project.config.ledger_ref,
            branch_guard=project.config.ledger_branch_guard,
            mutating=True,
        )
        if warning:
            # In warn mode, print to stderr (typer.echo with err=True).
            typer.echo(f"warning: {warning}", err=True)
    except ReleaseledgerError:
        raise
    except Exception:
        # Config not found or not in git: guard is a no-op.
        pass
