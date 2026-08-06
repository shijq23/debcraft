"""Architecture compliance tests using AST-based import scanning.

These tests enforce layer boundaries defined in the DebCraft architecture:
- Domain layer must not depend on infrastructure
- Plugins must not cross-import other plugins
- Platform contracts must have no implementation dependencies
- Key modules must not contain mutable module-level global state
"""

import ast
from pathlib import Path

import pytest

# Root of the source tree
SRC_ROOT = Path(__file__).resolve().parent.parent.parent / "src" / "debcraft"


def _get_python_files(directory: Path) -> list[Path]:
    """Collect all .py files in a directory tree."""
    if not directory.exists():
        return []
    return list(directory.rglob("*.py"))


def _get_imports(filepath: Path) -> list[str]:
    """Parse a Python file and return all imported module names."""
    source = filepath.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError:
        return []

    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    return imports


@pytest.mark.architecture
def test_domain_does_not_import_infrastructure():
    """Domain modules must not import from debcraft.infrastructure.

    The domain layer contains pure business logic and entities. It must remain
    independent of infrastructure concerns (databases, external services, etc.).
    """
    domain_dir = SRC_ROOT / "domain"
    violations: list[str] = []

    for py_file in _get_python_files(domain_dir):
        imports = _get_imports(py_file)
        for imp in imports:
            if imp.startswith("debcraft.infrastructure"):
                relative = py_file.relative_to(SRC_ROOT.parent)
                violations.append(f"{relative}: imports {imp}")

    assert violations == [], "Domain layer must not import from infrastructure:\n" + "\n".join(
        f"  - {v}" for v in violations
    )


@pytest.mark.architecture
def test_plugins_do_not_cross_import():
    """Plugin modules must not import from other plugin packages.

    Each plugin should be independent and only depend on platform/sdk
    or platform/contracts, never on sibling plugins.
    """
    plugins_dir = SRC_ROOT / "plugins"
    if not plugins_dir.exists():
        return

    # Discover plugin subpackages (direct subdirectories with __init__.py)
    plugin_packages: list[str] = []
    for item in plugins_dir.iterdir():
        if item.is_dir() and (item / "__init__.py").exists():
            plugin_packages.append(item.name)

    violations: list[str] = []

    for plugin_name in plugin_packages:
        plugin_dir = plugins_dir / plugin_name
        for py_file in _get_python_files(plugin_dir):
            imports = _get_imports(py_file)
            for imp in imports:
                # Check if it imports from another plugin
                if imp.startswith("debcraft.plugins."):
                    # Extract the plugin name from the import
                    parts = imp.split(".")
                    if len(parts) >= 3:
                        imported_plugin = parts[2]
                        if imported_plugin != plugin_name:
                            relative = py_file.relative_to(SRC_ROOT.parent)
                            violations.append(
                                f"{relative}: plugin '{plugin_name}' imports "
                                f"from plugin '{imported_plugin}' ({imp})"
                            )

    # Also check the top-level plugin files (not in a subpackage)
    for py_file in plugins_dir.glob("*.py"):
        imports = _get_imports(py_file)
        for imp in imports:
            if imp.startswith("debcraft.plugins."):
                parts = imp.split(".")
                if len(parts) >= 3:
                    # Top-level plugin file importing a specific plugin is okay
                    # (e.g., __init__.py registering plugins), skip this case
                    pass

    assert violations == [], "Plugins must not cross-import other plugins:\n" + "\n".join(
        f"  - {v}" for v in violations
    )


@pytest.mark.architecture
def test_contracts_have_no_implementation_dependencies():
    """Platform contracts must not import from infrastructure or plugins.

    Contracts define abstract interfaces and must remain pure of any
    concrete implementation dependencies.
    """
    contracts_dir = SRC_ROOT / "platform" / "contracts"
    violations: list[str] = []

    for py_file in _get_python_files(contracts_dir):
        imports = _get_imports(py_file)
        for imp in imports:
            if imp.startswith("debcraft.infrastructure"):
                relative = py_file.relative_to(SRC_ROOT.parent)
                violations.append(f"{relative}: imports infrastructure ({imp})")
            elif imp.startswith("debcraft.plugins"):
                relative = py_file.relative_to(SRC_ROOT.parent)
                violations.append(f"{relative}: imports plugins ({imp})")

    assert violations == [], (
        "Contracts must not depend on infrastructure or plugins:\n"
        + "\n".join(f"  - {v}" for v in violations)
    )


@pytest.mark.architecture
def test_no_mutable_module_level_global_state():
    """Key modules must not contain mutable module-level global state.

    Module-level assignments of list, dict, set, or mutable Call expressions
    are prohibited unless annotated with Final or using an all-caps name
    (convention for constants).
    """
    # Directories to scan for mutable globals
    scan_dirs = [
        SRC_ROOT / "domain",
        SRC_ROOT / "platform",
        SRC_ROOT / "infrastructure",
        SRC_ROOT / "plugins",
    ]

    violations: list[str] = []

    for scan_dir in scan_dirs:
        for py_file in _get_python_files(scan_dir):
            source = py_file.read_text(encoding="utf-8")
            try:
                tree = ast.parse(source, filename=str(py_file))
            except SyntaxError:
                continue

            for node in ast.iter_child_nodes(tree):
                # Check bare assignments at module level (not inside functions/classes)
                if isinstance(node, ast.Assign) and _is_mutable_value(node.value):
                    for target in node.targets:
                        if (
                            isinstance(target, ast.Name)
                            and not target.id.isupper()
                            and target.id != "__all__"
                        ):
                            relative = py_file.relative_to(SRC_ROOT.parent)
                            violations.append(
                                f"{relative}: mutable global '{target.id}' (line {node.lineno})"
                            )

                # Check annotated assignments (x: list = [])
                elif (
                    isinstance(node, ast.AnnAssign) and node.value and _is_mutable_value(node.value)
                ):
                    # Allow if annotated with Final
                    if _is_final_annotation(node.annotation):
                        continue
                    if node.target and isinstance(node.target, ast.Name):
                        name = node.target.id
                        if not name.isupper() and name != "__all__":
                            relative = py_file.relative_to(SRC_ROOT.parent)
                            violations.append(
                                f"{relative}: mutable global '{name}' (line {node.lineno})"
                            )

    assert violations == [], (
        "Modules must not contain mutable module-level global state:\n"
        + "\n".join(f"  - {v}" for v in violations)
    )


def _is_mutable_value(node: ast.expr) -> bool:
    """Check if an AST expression represents a mutable value (list, dict, set, or call)."""
    if isinstance(node, (ast.List, ast.Dict, ast.Set)):
        return True
    if isinstance(node, ast.Call):
        # Allow frozen dataclass calls and other known immutable constructors
        func = node.func
        if isinstance(func, ast.Name):
            # Common immutable constructors
            immutable_names = {"frozenset", "tuple", "namedtuple", "Final"}
            if func.id in immutable_names:
                return False
        elif isinstance(func, ast.Attribute):
            # Allow dataclass(frozen=True) and similar patterns
            if func.attr in {"field", "dataclass"}:
                return False
        # General calls (list(), dict(), set()) are mutable
        if isinstance(func, ast.Name) and func.id in {"list", "dict", "set"}:
            return True
    return False


def _is_final_annotation(annotation: ast.expr) -> bool:
    """Check if an annotation is typing.Final or Final[...]."""
    if isinstance(annotation, ast.Name) and annotation.id == "Final":
        return True
    if isinstance(annotation, ast.Attribute) and annotation.attr == "Final":
        return True
    if isinstance(annotation, ast.Subscript):
        # Final[SomeType]
        if isinstance(annotation.value, ast.Name) and annotation.value.id == "Final":
            return True
        if isinstance(annotation.value, ast.Attribute) and annotation.value.attr == "Final":
            return True
    return False
