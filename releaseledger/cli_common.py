"""Shared CLI plumbing: state, deterministic JSON envelopes, and text output.

Services never print or call ``typer.Exit``; they raise :class:`LaunchError` and
return dict payloads. The command boundary in :mod:`releaseledger.cli` uses these
helpers to render either a human line or a JSON envelope, and to write files
atomically when ``--output`` is requested.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeAlias

import ledgercore
import typer
from ledgercore.cli import CLIWarning, ErrorEnvelope, SuccessEnvelope

from releaseledger.errors import (
    CODE_USAGE_ERROR,
    LaunchError,
    ReleaseledgerError,
    to_error_payload,
)

__all__ = [
    "CLIState",
    "add_cli_warning",
    "canonical_command_path",
    "cli_state_from_context",
    "emit_error",
    "emit_payload",
    "launch_error_exit_code",
    "normalize_legacy_global_argv",
    "render_json",
    "resolve_workspace_root",
    "run_command",
    "store_cli_state",
    "write_text_output",
]


# A command body returns (result_dict, event_ids, optional human text).
CommandResult: TypeAlias = tuple[dict[str, object], Sequence[str], str | None]

_warnings: ContextVar[list[CLIWarning] | None] = ContextVar(
    "releaseledger_cli_warnings", default=None
)


@dataclass(slots=True)
class CLIState:
    """Resolved per-invocation CLI options shared across subcommands.

    ``cwd`` remains a read-only compatibility property while ``root`` is the
    canonical project selector.
    """

    root: Path
    json_output: bool = False
    legacy_cwd: bool = False
    warnings: list[CLIWarning] = field(default_factory=list)

    @property
    def cwd(self) -> Path:
        """Compatibility view for code not yet renamed from ``cwd``."""
        return self.root


def resolve_workspace_root(root: Path | None) -> Path:
    """Resolve a project root without changing the process working directory."""
    if root is None:
        return Path.cwd().resolve()
    resolved = Path(root).expanduser()
    return resolved.resolve()


def render_json(payload: object) -> str:
    """Render deterministic JSON (sorted keys, final newline)."""
    return ledgercore.dumps_json(payload)


def store_cli_state(ctx: typer.Context, state: CLIState) -> None:
    """Persist the resolved :class:`CLIState` on the typer context object."""
    ctx.ensure_object(dict)
    obj: dict[str, Any] = ctx.obj
    obj["state"] = state
    _warnings.set(state.warnings)


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
    return CLIState(root=resolve_workspace_root(None), json_output=False)


def canonical_command_path(command: str) -> str:
    """Return the public space-separated command path."""
    return command.replace(".", " ").strip()


def normalize_legacy_global_argv(argv: Sequence[str]) -> list[str]:
    """Hoist only the supported legacy post-command global ``--json`` flag.

    Click/Typer normally require group options before a subcommand. This
    narrow compatibility pass deliberately leaves values and all content after
    ``--`` untouched, and never reinterprets command-local options.
    """
    args = list(argv)
    try:
        end = args.index("--")
    except ValueError:
        end = len(args)
    before = args[:end]
    after = args[end:]
    if "--json" not in before:
        return args
    # Already canonical: preserving order avoids duplicate options.
    if before and before[0] == "--json":
        return args
    removed = False
    remaining: list[str] = []
    for item in before:
        if item == "--json" and not removed:
            removed = True
            continue
        remaining.append(item)
    return ["--json", *remaining, *after]


def add_cli_warning(warning: CLIWarning) -> None:
    """Collect a warning for the current invocation."""
    current = _warnings.get()
    if current is None:
        current = []
        _warnings.set(current)
    if not any(
        item.code == warning.code
        and item.message == warning.message
        and item.replacement == warning.replacement
        for item in current
    ):
        current.append(warning)


def _current_warnings(extra: Sequence[CLIWarning] = ()) -> list[CLIWarning]:
    """Return invocation warnings with deterministic de-duplication."""
    combined = [*(_warnings.get() or []), *extra]
    result: list[CLIWarning] = []
    seen: set[tuple[str, str, str | None]] = set()
    for warning in combined:
        key = (warning.code, warning.message, warning.replacement)
        if key not in seen:
            seen.add(key)
            result.append(warning)
    return result


def emit_payload(
    *,
    command: str,
    result_type: str,
    result: dict[str, object],
    events: Sequence[str] | None = None,
    human: str | None = None,
    json_output: bool,
    warnings: Sequence[CLIWarning] = (),
) -> None:
    """Render a success payload as JSON or a human line.

    ``human`` is shown verbatim for human mode; JSON mode emits the full
    envelope with sorted keys and a trailing newline.
    """
    canonical = canonical_command_path(command)
    collected = _current_warnings(warnings)
    if json_output:
        payload = SuccessEnvelope(
            tool="releaseledger",
            command=canonical,
            result=result,
            events=tuple({"id": str(event)} for event in (events or [])),
            warnings=tuple(collected),
        ).as_mapping()
        # ``--cwd`` is a deprecated compatibility surface.  Keep its legacy
        # top-level fields available while canonical ``--root`` callers use
        # the Ledgerwerk v1 envelope exclusively.
        if any(
            warning.code == "deprecated_option" and warning.replacement == "--root"
            for warning in collected
        ):
            payload["result_type"] = result_type
            payload["command"] = command.replace(" ", ".")
            legacy_events = result.get("events")
            if isinstance(legacy_events, list):
                payload["events"] = legacy_events
        if isinstance(result.get("ok"), bool):
            payload["ok"] = result["ok"]
        typer.echo(render_json(payload))
        return
    for warning in collected:
        typer.echo(f"warning: {warning.message}", err=True)
    if human is not None:
        typer.echo(human)


def emit_error(
    *,
    command: str,
    error: ReleaseledgerError,
    json_output: bool,
    human: str | None = None,
    result: dict[str, object] | None = None,
    result_type: str | None = None,
    events: list[object] | None = None,
    warnings: Sequence[CLIWarning] = (),
) -> None:
    """Render an error payload as JSON (stdout) or a human line (stderr)."""
    canonical = canonical_command_path(command)
    collected = _current_warnings(warnings)
    if json_output:
        payload = ErrorEnvelope(
            tool="releaseledger",
            command=canonical,
            error=to_error_payload(error),
            events=tuple(
                event if isinstance(event, dict) else {"id": str(event)}
                for event in (events or [])
            ),
            warnings=tuple(collected),
        ).as_mapping()
        if result is None:
            embedded = error.data.get("result")
            if isinstance(embedded, dict):
                result = embedded
        if result is not None:
            payload["result"] = result
        if any(
            warning.code == "deprecated_option" and warning.replacement == "--root"
            for warning in collected
        ):
            # Preserve the pre-v1 machine code spelling for deprecated
            # ``--cwd`` callers.  Canonical callers receive the normalized
            # public code from ``to_error_payload``.
            error_payload = payload.get("error")
            if isinstance(error_payload, dict):
                error_payload["code"] = error.code
            payload["command"] = command.replace(" ", ".")
            if result_type is not None:
                payload["result_type"] = result_type
        typer.echo(render_json(payload))
        return
    for warning in collected:
        typer.echo(f"warning: {warning.message}", err=True)
    if human is None:
        embedded_human = error.data.get("human")
        human = embedded_human if isinstance(embedded_human, str) else None
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
    if str(path) == "-":
        import sys

        sys.stdout.write(text)
        if not text.endswith("\n"):
            sys.stdout.write("\n")
        return path
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
    check_passed: bool | None = None,
    branch_guard_policy: str = "default",
) -> None:
    """Run a command body, emitting a success or error envelope.

    ``produce`` returns ``(result_dict, events, human)``. A
    :class:`ReleaseledgerError` raised by the service layer is turned into the
    error envelope and a non-zero typer exit.

    ``branch_guard_policy`` controls when the branch guard runs:
    - ``"default"``: always run for mutating commands with workspace_root
    - ``"if-canonical-project"``: skip guard when no canonical project exists
      (used by migration commands that can bootstrap from legacy)
    """
    try:
        if mutating and workspace_root is not None:
            if branch_guard_policy == "if-canonical-project":
                _check_migration_branch_guard(workspace_root, command=command)
            else:
                check_mutating_branch_guard(workspace_root, command=command)
        if mutating and workspace_root is not None:
            from releaseledger.storage.locking import acquire_write_lock

            with acquire_write_lock(workspace_root):
                result, events, human = produce()
        else:
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
    if check_passed is False or (
        check_passed is None and result.get("passed") is False
    ):
        raise typer.Exit(1)


def check_mutating_branch_guard(
    workspace_root: Path,
    *,
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
            add_cli_warning(
                CLIWarning(
                    code="branch_guard",
                    message=str(warning),
                )
            )
    except ReleaseledgerError:
        raise
    except Exception:
        # Config not found or not in git: guard is a no-op.
        pass


def _check_migration_branch_guard(
    workspace_root: Path,
    *,
    command: str,
) -> None:
    """Run branch guard only when a canonical Releaseledger project exists.

    Migration commands use this policy so that bootstrap from a pure-legacy
    project is not blocked by the branch guard. When a canonical project
    already exists, the normal guard behavior is preserved.
    """
    try:
        from releaseledger.storage.paths import load_releaseledger_project

        load_releaseledger_project(workspace_root)
    except Exception:
        # No canonical project: migration is bootstrapping. Skip guard.
        return
    # Canonical project exists: run the normal guard.
    check_mutating_branch_guard(workspace_root, command=command)
