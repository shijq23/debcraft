"""SPDX AST serializer.

Formats an SPDX AST back into a canonical SPDX expression string,
inserting parentheses where needed to preserve correct precedence.
"""

from __future__ import annotations

from debcraft.domain.package_intelligence.values import (
    AndNode,
    OrNode,
    SimpleNode,
    SPDXNode,
    WithNode,
)


class SPDXPrinter:
    """Serializes SPDX AST to canonical expression strings."""

    def print(self, node: SPDXNode) -> str:
        """Format an SPDX AST as a canonical expression string."""
        if isinstance(node, SimpleNode):
            return self._print_simple(node)
        if isinstance(node, WithNode):
            return self._print_with(node)
        if isinstance(node, AndNode):
            return self._print_and(node)
        if isinstance(node, OrNode):
            return self._print_or(node)
        msg = f"Unknown node type: {type(node)}"
        raise TypeError(msg)

    def _print_simple(self, node: SimpleNode) -> str:
        if node.or_later:
            return f"{node.identifier}+"
        return node.identifier

    def _print_with(self, node: WithNode) -> str:
        license_str = self.print(node.license)
        return f"{license_str} WITH {node.exception}"

    def _print_and(self, node: AndNode) -> str:
        left_str = self._wrap_if_or(node.left)
        right_str = self._wrap_if_or(node.right)
        return f"{left_str} AND {right_str}"

    def _print_or(self, node: OrNode) -> str:
        left_str = self.print(node.left)
        right_str = self.print(node.right)
        return f"{left_str} OR {right_str}"

    def _wrap_if_or(self, node: SPDXNode) -> str:
        """Wrap a node in parentheses if it is an OrNode.

        AND binds tighter than OR, so Or children of And nodes
        need parentheses to preserve correct precedence.
        """
        result = self.print(node)
        if isinstance(node, OrNode):
            return f"({result})"
        return result
