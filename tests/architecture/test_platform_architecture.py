"""Architecture tests for the platform layer.

Validates:
- Contract purity: platform/contracts/ imports no kernel, infrastructure, or plugin modules
- ABC-to-implementation mapping: every ABC in contracts has a kernel implementation
- No module-level mutable global state in platform/kernel/
"""

import ast
import importlib
import inspect
import pkgutil
from abc import ABC
from pathlib import Path

import pytest

SRC_ROOT = Path(__file__).resolve().parent.parent.parent / "src" / "debcraft"

CONTRACTS_DIR = SRC_ROOT / "platform" / "contracts"
KERNEL_DIR = SRC_ROOT / "platform" / "kernel"


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
class TestContractPurity:
    """Verify platform/contracts/ has no implementation dependencies."""

    def test_contracts_do_not_import_kernel(self):
        violations: list[str] = []

        for py_file in _get_python_files(CONTRACTS_DIR):
            imports = _get_imports(py_file)
            for imp in imports:
                if "platform.kernel" in imp or imp.startswith("debcraft.platform.kernel"):
                    relative = py_file.relative_to(SRC_ROOT.parent)
                    violations.append(f"{relative}: imports {imp}")

        assert violations == [], "Contracts must not import from platform.kernel:\n" + "\n".join(
            f"  - {v}" for v in violations
        )

    def test_contracts_do_not_import_infrastructure(self):
        violations: list[str] = []

        for py_file in _get_python_files(CONTRACTS_DIR):
            imports = _get_imports(py_file)
            for imp in imports:
                if imp.startswith("debcraft.infrastructure"):
                    relative = py_file.relative_to(SRC_ROOT.parent)
                    violations.append(f"{relative}: imports {imp}")

        assert violations == [], "Contracts must not import from infrastructure:\n" + "\n".join(
            f"  - {v}" for v in violations
        )

    def test_contracts_do_not_import_plugins(self):
        violations: list[str] = []

        for py_file in _get_python_files(CONTRACTS_DIR):
            imports = _get_imports(py_file)
            for imp in imports:
                if imp.startswith("debcraft.plugins"):
                    relative = py_file.relative_to(SRC_ROOT.parent)
                    violations.append(f"{relative}: imports {imp}")

        assert violations == [], "Contracts must not import from plugins:\n" + "\n".join(f"  - {v}" for v in violations)


@pytest.mark.architecture
class TestABCImplementationMapping:
    """Verify all ABCs in contracts have corresponding implementations in kernel."""

    def _discover_contract_abcs(self) -> dict[str, type]:
        """Discover all ABC classes defined in the contracts package."""
        abcs: dict[str, type] = {}
        contracts_pkg = "debcraft.platform.contracts"

        for module_info in pkgutil.walk_packages(
            [str(CONTRACTS_DIR)],
            prefix=f"{contracts_pkg}.",
        ):
            try:
                module = importlib.import_module(module_info.name)
            except ImportError:
                continue

            for name, obj in inspect.getmembers(module, inspect.isclass):
                if (
                    issubclass(obj, ABC)
                    and obj is not ABC
                    and obj.__module__.startswith(contracts_pkg)
                    and any(getattr(method, "__isabstractmethod__", False) for method in vars(obj).values())
                ):
                    abcs[name] = obj

        return abcs

    def _discover_kernel_classes(self) -> dict[str, type]:
        """Discover all classes defined in the kernel package."""
        classes: dict[str, type] = {}
        kernel_pkg = "debcraft.platform.kernel"

        for module_info in pkgutil.walk_packages(
            [str(KERNEL_DIR)],
            prefix=f"{kernel_pkg}.",
        ):
            try:
                module = importlib.import_module(module_info.name)
            except ImportError:
                continue

            for name, obj in inspect.getmembers(module, inspect.isclass):
                if obj.__module__.startswith(kernel_pkg):
                    classes[name] = obj

        return classes

    # ABCs that are intentionally designed for user/plugin extension,
    # not for kernel implementation.
    _USER_FACING_ABCS = frozenset(
        {
            "Workflow",  # Users implement concrete workflows; kernel provides engine/factory
        }
    )

    def test_all_abcs_have_kernel_implementations(self):
        contract_abcs = self._discover_contract_abcs()
        kernel_classes = self._discover_kernel_classes()

        missing: list[str] = []

        for abc_name, abc_type in contract_abcs.items():
            if abc_name in self._USER_FACING_ABCS:
                continue

            # Check if any kernel class is a concrete subclass of this ABC
            has_implementation = any(
                issubclass(cls, abc_type) and cls is not abc_type for cls in kernel_classes.values()
            )
            if not has_implementation:
                missing.append(abc_name)

        assert missing == [], "ABCs without kernel implementations:\n" + "\n".join(
            f"  - {name}" for name in sorted(missing)
        )


@pytest.mark.architecture
class TestNoMutableGlobalState:
    """Verify no module-level mutable global state in platform/kernel/."""

    # Names that are acceptable as module-level "mutable" looking objects
    _ALLOWED_NAMES = frozenset(
        {
            "__all__",
        }
    )

    # Known immutable constructor calls (not mutable state)
    _IMMUTABLE_CALLS = frozenset(
        {
            "frozenset",
            "tuple",
            "namedtuple",
            "Final",
            "TypeVar",
            "ParamSpec",
            "TypeVarTuple",
            "NewType",
            "ContextVar",
            "getLogger",
        }
    )

    # Known immutable attribute calls
    _IMMUTABLE_ATTR_CALLS = frozenset(
        {
            "field",
            "dataclass",
            "ContextVar",
            "getLogger",
            "get_event_loop",
        }
    )

    def _is_mutable_value(self, node: ast.expr) -> bool:
        """Check if an AST expression represents a likely mutable value."""
        if isinstance(node, (ast.List, ast.Dict, ast.Set)):
            return True
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                if func.id in self._IMMUTABLE_CALLS:
                    return False
                if func.id in {"list", "dict", "set"}:
                    return True
            elif isinstance(func, ast.Attribute):
                if func.attr in self._IMMUTABLE_ATTR_CALLS:
                    return False
        return False

    def _is_final_annotation(self, annotation: ast.expr) -> bool:
        """Check if an annotation is typing.Final or Final[...]."""
        if isinstance(annotation, ast.Name) and annotation.id == "Final":
            return True
        if isinstance(annotation, ast.Attribute) and annotation.attr == "Final":
            return True
        if isinstance(annotation, ast.Subscript):
            value = annotation.value
            if isinstance(value, ast.Name) and value.id == "Final":
                return True
            if isinstance(value, ast.Attribute) and value.attr == "Final":
                return True
        return False

    def _is_allowed_name(self, name: str) -> bool:
        """Check if the variable name is allowed at module level."""
        if name in self._ALLOWED_NAMES:
            return True
        # ALL_CAPS convention indicates a constant
        if name.isupper():
            return True
        # Leading underscore indicates internal use (logger instances, etc.)
        return bool(name.startswith("_"))

    def test_kernel_modules_have_no_mutable_global_state(self):
        violations: list[str] = []

        for py_file in _get_python_files(KERNEL_DIR):
            source = py_file.read_text(encoding="utf-8")
            try:
                tree = ast.parse(source, filename=str(py_file))
            except SyntaxError:
                continue

            for node in ast.iter_child_nodes(tree):
                if isinstance(node, ast.Assign) and self._is_mutable_value(node.value):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and not self._is_allowed_name(target.id):
                            relative = py_file.relative_to(SRC_ROOT.parent)
                            violations.append(f"{relative}: mutable global '{target.id}' (line {node.lineno})")

                elif isinstance(node, ast.AnnAssign) and node.value and self._is_mutable_value(node.value):
                    if self._is_final_annotation(node.annotation):
                        continue
                    if isinstance(node.target, ast.Name) and not self._is_allowed_name(node.target.id):
                        relative = py_file.relative_to(SRC_ROOT.parent)
                        violations.append(f"{relative}: mutable global '{node.target.id}' (line {node.lineno})")

        assert violations == [], "Kernel modules must not contain mutable module-level global state:\n" + "\n".join(
            f"  - {v}" for v in violations
        )
