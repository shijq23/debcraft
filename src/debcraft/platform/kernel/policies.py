"""Kernel execution policy implementation.

Re-exports the ExecutionPolicy frozen dataclass from the contracts layer.
For value objects, the contract and implementation are identical.
"""

from debcraft.platform.contracts.policies import ExecutionPolicy

__all__ = ["ExecutionPolicy"]
