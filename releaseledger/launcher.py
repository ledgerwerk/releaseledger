"""Console entry point shim for ``releaseledger``."""

from __future__ import annotations

import sys

from releaseledger.cli import app
from releaseledger.cli_common import normalize_legacy_global_argv


def main() -> None:
    """Entry point referenced by ``project.scripts``."""
    app(args=normalize_legacy_global_argv(sys.argv[1:]))
