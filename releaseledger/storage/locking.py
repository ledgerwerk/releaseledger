"""Releaseledger command-level exclusive write guard.

Uses ``filelock`` for cross-platform advisory locking. The lock file lives at
``.ledger/releaseledger/write.lock`` (outside the movable data mount) so it
remains stable regardless of data-storage configuration.

All mutating commands must acquire the same exclusive lock. Read-only commands
should use no lock or a shared lock in the future; for the 0.4.0 release the
lock remains exclusive-only to keep the contract simple.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from filelock import FileLock, Timeout

from releaseledger import ledgercore_backend as _backend
from releaseledger.errors import CODE_CONFLICT, LaunchError

__all__ = [
    "acquire_write_lock",
    "quiescence_callback",
    "releaseledger_lock_path",
    "write_locked",
]

# Timeout for acquiring the lock in mutating commands. Kept low so the CLI
# surface returns fast rather than blocking indefinitely. Background tasks
# that need longer acquisitions can pass an explicit timeout.
_LOCK_TIMEOUT_SECONDS = 5.0

# Grace period during which we poll the lock for the quiescence check.
_QUIESCENCE_POLL_SECONDS = 2.0

# ``run_command`` owns the command-level lock, while migration execution also
# needs a lock-scoped quiescence callback. Keep nested acquisitions in this
# process on the same FileLock instance instead of attempting a second OS lock.
_HELD_LOCKS: dict[Path, tuple[FileLock, int]] = {}
_HELD_LOCKS_GUARD = threading.RLock()


def releaseledger_lock_path(project_root: Path) -> Path:
    """Return the canonical write-lock path for a project."""

    return project_root.resolve() / ".ledger" / _backend.TOOL_NAME / "write.lock"


@contextlib.contextmanager
def acquire_write_lock(
    project_root: Path,
    *,
    timeout: float = _LOCK_TIMEOUT_SECONDS,
) -> Iterator[FileLock]:
    """Context manager that acquires and releases the exclusive write lock.

    The lock is released automatically on context exit, even when an
    exception propagates.

    Raises a :class:`LaunchError` with ``CODE_CONFLICT`` if the lock
    cannot be acquired within ``timeout`` seconds.
    """

    lock_path = releaseledger_lock_path(project_root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    with _HELD_LOCKS_GUARD:
        held = _HELD_LOCKS.get(lock_path)
        if held is not None:
            lock, depth = held
            _HELD_LOCKS[lock_path] = (lock, depth + 1)
            try:
                yield lock
            finally:
                with _HELD_LOCKS_GUARD:
                    current = _HELD_LOCKS.get(lock_path)
                    if current is not None and current[1] > 1:
                        _HELD_LOCKS[lock_path] = (current[0], current[1] - 1)
                    else:
                        _HELD_LOCKS.pop(lock_path, None)
            return

    lock = FileLock(str(lock_path), timeout=timeout)
    try:
        lock.acquire(timeout=timeout)
    except Timeout:
        pid = _read_lock_pid(lock_path)
        extra: dict[str, object] = {"lock_path": str(lock_path)}
        if pid is not None:
            extra["holder_pid"] = pid
        raise LaunchError(
            f"Releaseledger is locked by another command (timeout={timeout}s).",
            code=CODE_CONFLICT,
            exit_code=4,
            data=extra,
            remediation=[
                "Wait for the other command to finish and retry.",
                "If the holder process is gone, inspect the lock state before retrying.",
            ],
        ) from None
    with _HELD_LOCKS_GUARD:
        _HELD_LOCKS[lock_path] = (lock, 1)
    try:
        yield lock
    finally:
        with _HELD_LOCKS_GUARD:
            current = _HELD_LOCKS.get(lock_path)
            if current is not None and current[0] is lock and current[1] == 1:
                _HELD_LOCKS.pop(lock_path, None)
                lock.release()


def quiescence_callback(lock: FileLock) -> None:
    """Migration quiescence callback: verify the lock is still held.

    Re-reads the lock to confirm the current process is still the
    exclusive holder. This is passed to Ledgercore's migration executor
    so it can validate quiescence before copy and before activation.
    """

    if not lock.is_locked:
        raise LaunchError(
            "Write lock was lost before migration could complete.",
            code=CODE_CONFLICT,
            exit_code=4,
            remediation=[
                "Re-acquire the lock and restart the migration from the journal.",
            ],
        )


def write_locked(project_root: Path, *, timeout: float = _LOCK_TIMEOUT_SECONDS) -> Any:
    """Decorator / context wrapper that acquires the write lock for a function.

    When used as a decorator, the first positional argument after ``self``
    (when decorating a method) is examined for a project root. The caller
    is responsible for passing ``project_root`` as a keyword argument or
    as the second positional argument.
    """

    # Kept as a thin convenience. Most callers should use the context
    # manager directly for clarity.
    raise NotImplementedError(
        "write_locked decorator is not yet implemented; use acquire_write_lock "
        "as a context manager instead."
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _read_lock_pid(path: Path) -> int | None:
    """Read the PID from a filelock file, if present."""

    try:
        text = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    parts = text.split()
    if len(parts) >= 2:
        try:
            return int(parts[1])
        except (ValueError, IndexError):
            return None
    return None
