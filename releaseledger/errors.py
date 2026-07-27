"""Typed exceptions and JSON error envelopes for releaseledger.

All releaseledger errors flow through :class:`ReleaseledgerError` (and the
:class:`LaunchError` subclass that services raise). The CLI catches these at the
command boundary and turns them into the deterministic JSON error envelope.
"""

from __future__ import annotations

__all__ = [
    "CODE_CONFIG_ERROR",
    "CODE_CONFLICT",
    "CODE_NOT_FOUND",
    "CODE_USAGE_ERROR",
    "CODE_VALIDATION_ERROR",
    "EXIT_RUNTIME",
    "EXIT_USAGE",
    "EXIT_UNAVAILABLE",
    "EXIT_CONFLICT",
    "EXIT_EXTERNAL",
    "LaunchError",
    "ReleaseledgerError",
    "to_error_payload",
]

# Stable machine codes referenced across the CLI envelope.
CODE_USAGE_ERROR = "USAGE_ERROR"
CODE_NOT_FOUND = "NOT_FOUND"
CODE_CONFIG_ERROR = "CONFIG_ERROR"
CODE_VALIDATION_ERROR = "VALIDATION_ERROR"
CODE_CONFLICT = "CONFLICT"

# Exit codes follow the shared Ledgerwerk CLI contract. Existing domain code
# constants remain uppercase for service compatibility; the public envelope
# maps them to lower-snake-case names in ``to_error_payload``.
EXIT_USAGE = 2
EXIT_RUNTIME = 1
EXIT_UNAVAILABLE = 3
EXIT_CONFLICT = 4
EXIT_EXTERNAL = 5

_PUBLIC_CODES = {
    "USAGE_ERROR": "usage_error",
    "CONFIG_ERROR": "invalid_input",
    "INVALID_INPUT": "invalid_input",
    "NOT_INITIALIZED": "not_initialized",
    "NOT_FOUND": "not_found",
    "VALIDATION_ERROR": "validation_failed",
    "VALIDATION_FAILED": "validation_failed",
    "CONFLICT": "conflict",
    "LOCKED": "locked",
    "UNSAFE_OVERWRITE": "unsafe_overwrite",
    "EXTERNAL_FAILURE": "external_failure",
    "RELEASELEDGER_ERROR": "internal_error",
}


class ReleaseledgerError(Exception):
    """Base error for all releaseledger failures.

    Attributes:
        message: Human readable message.
        code: Stable machine code (see module-level constants).
        exit_code: Process exit code (2 for usage/config/validation, 1 runtime).
        data: Optional structured detail merged into the error payload.
        remediation: Optional ordered remediation hints.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "RELEASELEDGER_ERROR",
        exit_code: int = EXIT_RUNTIME,
        data: dict[str, object] | None = None,
        remediation: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.exit_code = exit_code
        self.data: dict[str, object] = dict(data) if data else {}
        self.remediation: list[str] = list(remediation) if remediation else []

    def to_payload(self) -> dict[str, object]:
        """Render a deterministic JSON-serializable error payload."""
        return to_error_payload(self)


class LaunchError(ReleaseledgerError):
    """Raised by services/cli helpers for actionable, user-facing failures.

    Services raise ``LaunchError`` (never ``typer.Exit`` and never print) so the
    CLI boundary can render either a human line or a JSON envelope.
    """


class MigrationConflictError(LaunchError):
    """Raised when migration encounters a conflicting destination.

    Includes structured fields for component, path, state, and remediation
    so the CLI can render actionable diagnostics instead of generic errors.
    """

    def __init__(
        self,
        message: str,
        *,
        component: str,
        path: str,
        state: str,
        expected_binding: dict[str, object] | None = None,
        actual_binding: dict[str, object] | None = None,
        expected_hash: str | None = None,
        actual_hash: str | None = None,
        retry_safe: bool = False,
        remediation_command: str | None = None,
        **kwargs: object,
    ) -> None:
        data = dict(kwargs.get("data", {}))  # type: ignore[call-overload]
        data.update(
            {
                "component": component,
                "path": path,
                "destination_state": state,
                "retry_safe": retry_safe,
            }
        )
        if expected_binding:
            data["expected_binding"] = expected_binding
        if actual_binding:
            data["actual_binding"] = actual_binding
        if expected_hash:
            data["expected_hash"] = expected_hash
        if actual_hash:
            data["actual_hash"] = actual_hash
        if remediation_command:
            data["remediation_command"] = remediation_command
        kwargs["data"] = data
        super().__init__(message, **kwargs)  # type: ignore[arg-type]


def to_error_payload(error: ReleaseledgerError) -> dict[str, object]:
    """Return the stable public error mapping for an internal domain error."""
    public_code = _PUBLIC_CODES.get(error.code, error.code.lower())
    details = dict(error.data)
    if error.code != public_code and "legacy_code" not in details:
        details["legacy_code"] = error.code
    return {
        "code": public_code,
        "message": error.message,
        "details": details,
        "remediation": list(error.remediation),
    }
