"""Schema version constants and the controlled vocabularies for records."""

from __future__ import annotations

__all__ = [
    "ENTRY_KINDS",
    "ENTRY_KIND_ALIASES",
    "ENTRY_KIND_TITLES",
    "ENTRY_STATUSES",
    "KEEPACHANGELOG_GROUP_ORDER",
    "KEEPACHANGELOG_GROUP_TITLES",
    "DEFAULT_KEEPACHANGELOG_KIND_MAP",
    "RELEASE_STATUSES",
    "RELEASELEDGER_FILE_VERSION",
    "RELEASELEDGER_SCHEMA_VERSION",
    "SUPPORTED_SCHEMA_VERSIONS",
]

RELEASELEDGER_SCHEMA_VERSION = 1
RELEASELEDGER_FILE_VERSION = "releaseledger.v1"

SUPPORTED_SCHEMA_VERSIONS = frozenset({1})

RELEASE_STATUSES = frozenset(
    {
        "planned",
        "draft",
        "candidate",
        "released",
        "yanked",
        "canceled",
    }
)

ENTRY_KINDS = frozenset(
    {
        "added",
        "changed",
        "fixed",
        "removed",
        "deprecated",
        "security",
        "docs",
        "quality",
        "internal",
    }
)

ENTRY_KIND_ALIASES = {
    "documentation": "docs",
    "doc": "docs",
}

ENTRY_STATUSES = frozenset({"draft", "accepted", "rejected"})

# Human-readable changelog group titles keyed by entry kind.
ENTRY_KIND_TITLES = {
    "added": "Added",
    "changed": "Changed",
    "fixed": "Fixed",
    "removed": "Removed",
    "deprecated": "Deprecated",
    "security": "Security",
    "docs": "Documentation",
    "quality": "Quality",
    "internal": "Internal",
}

# Keep a Changelog 1.1.0 canonical group order.
KEEPACHANGELOG_GROUP_ORDER = (
    "added",
    "changed",
    "deprecated",
    "removed",
    "fixed",
    "security",
)

# Keep a Changelog 1.1.0 canonical group titles.
KEEPACHANGELOG_GROUP_TITLES = {
    "added": "Added",
    "changed": "Changed",
    "deprecated": "Deprecated",
    "removed": "Removed",
    "fixed": "Fixed",
    "security": "Security",
}

# Default mapping from extended entry kinds to Keep a Changelog groups.
# Extended kinds (docs, quality, internal) map to 'changed' by default.
DEFAULT_KEEPACHANGELOG_KIND_MAP = {
    "added": "added",
    "changed": "changed",
    "deprecated": "deprecated",
    "removed": "removed",
    "fixed": "fixed",
    "security": "security",
    "docs": "changed",
    "quality": "changed",
    "internal": "changed",
}
