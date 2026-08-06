"""Platform kernel providing core runtime services and orchestration.

This package re-exports the bootstrap function for application startup.
"""

from debcraft.platform.kernel.bootstrap import bootstrap

__all__ = ["bootstrap"]
