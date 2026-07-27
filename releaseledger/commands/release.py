"""Release command domain seam.

Mutation and rendering implementations remain service-backed while the CLI
registration is migrated incrementally behind this stable module boundary.
"""

from releaseledger.services.releases import set_release_status

__all__ = ["set_release_status"]
