"""Domain command modules used by the Releaseledger CLI.

The public ``releaseledger.cli.app`` remains the compatibility entry point.
Modules in this package provide the domain seams for registration, metadata,
and future handler extraction; they intentionally depend on services rather
than on Ledgercore's detailed storage implementation.
"""

COMMAND_DOMAINS = (
    "common",
    "release",
    "entry",
    "changelog",
    "git",
    "branch",
    "audit",
    "storage",
    "migrate",
    "config",
)

__all__ = ["COMMAND_DOMAINS"]
