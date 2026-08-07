"""Property-based tests for Debian test repository fixture scripts.

**Validates: Requirements 1.1, 3.6, 4.5, 8.2**

This module provides:
- Hypothesis strategies for generating valid Debian package/repository inputs
- Python helper functions replicating script logic (for testing without dpkg tools)
- Pytest markers for unit and integration test classification

The strategies and helpers are used by property tests in this file and
can be imported by other test modules.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from debcraft.domain.mirror.release_parser import ReleaseParser

# =============================================================================
# Hypothesis Strategies
# =============================================================================

# Valid Debian package names: start with lowercase letter, followed by
# lowercase alphanumeric, dots, plus signs, or hyphens. Length 2-31.
package_names = st.from_regex(r"[a-z][a-z0-9.+\-]{1,30}", fullmatch=True)

# Valid lib* package names: start with "lib" followed by a lowercase letter,
# then lowercase alphanumeric, dots, plus signs, or hyphens. Length 5-30.
lib_package_names = st.from_regex(r"lib[a-z][a-z0-9.+\-]{1,26}", fullmatch=True)

# Valid Debian version strings: epoch-less, upstream-debian format
versions = st.from_regex(r"[0-9]+\.[0-9]+\-[0-9]+", fullmatch=True)

# Valid architectures
architectures = st.sampled_from(["all", "any", "amd64", "arm64", "i386", "armhf"])

# Architecture sets (for multi-arch repos)
arch_sets = st.lists(architectures, min_size=1, max_size=4, unique=True)

# Suite names
suites = st.sampled_from(["stable", "unstable", "testing", "bookworm", "trixie"])

# Dependency strings: comma-separated list of package names
dependencies = st.lists(
    st.from_regex(r"[a-z][a-z0-9\-]{1,20}", fullmatch=True),
    min_size=0,
    max_size=3,
).map(", ".join)

# Descriptions (safe ASCII letters, numbers, and spaces)
descriptions = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "Z")),
    min_size=1,
    max_size=80,
).filter(lambda s: s.strip())


# =============================================================================
# Helper Functions
# =============================================================================


def generate_control_content(
    name: str,
    arch: str = "all",
    depends: str = "",
    description: str = "Test package",
) -> str:
    """Generate debian/control file content replicating create-package.sh logic.

    Args:
        name: Debian package name.
        arch: Architecture field value.
        depends: Comma-separated dependency list.
        description: Package description.

    Returns:
        The full content of a debian/control file.
    """
    depends_line = f"Depends: {depends}\n" if depends else ""
    return (
        f"Source: {name}\n"
        f"Section: misc\n"
        f"Priority: optional\n"
        f"Maintainer: Test <test@example.com>\n"
        f"Build-Depends: debhelper (>= 7)\n"
        f"Standards-Version: 3.9.8\n"
        f"\n"
        f"Package: {name}\n"
        f"Architecture: {arch}\n"
        f"{depends_line}"
        f"Description: {description}\n"
    )


def generate_changelog_content(
    name: str,
    version: str = "1.0-1",
) -> str:
    """Generate debian/changelog file content replicating create-package.sh logic.

    Args:
        name: Debian package name.
        version: Package version string.

    Returns:
        The full content of a debian/changelog file.
    """
    return (
        f"{name} ({version}) unstable; urgency=low\n"
        f"\n"
        f"  * Test package\n"
        f"\n"
        f" -- Test <test@example.com>  Mon, 01 Jan 2024 00:00:00 +0000\n"
    )


def generate_rules_content() -> str:
    """Generate debian/rules file content replicating create-package.sh logic.

    Returns:
        The full content of a debian/rules file (debhelper 7 minimal).
    """
    return "#!/usr/bin/make -f\n%:\n\tdh $@\n"


def generate_compat_content() -> str:
    """Generate debian/compat file content.

    Returns:
        The compat level string.
    """
    return "7\n"


def get_skeleton_files(
    name: str,
    version: str = "1.0-1",
    arch: str = "all",
    depends: str = "",
    description: str = "Test package",
) -> dict[str, str]:
    """Generate all debian/ skeleton files as a dictionary.

    Replicates the full output of create-package.sh skeleton generation.

    Args:
        name: Debian package name.
        version: Package version string.
        arch: Architecture field value.
        depends: Comma-separated dependency list.
        description: Package description.

    Returns:
        Dictionary mapping relative file paths to their content.
    """
    return {
        "debian/control": generate_control_content(name, arch, depends, description),
        "debian/changelog": generate_changelog_content(name, version),
        "debian/rules": generate_rules_content(),
        "debian/compat": generate_compat_content(),
    }


def compute_pool_prefix(package_name: str) -> str:
    """Compute the pool directory prefix for a package name.

    Follows Debian pool naming conventions:
    - lib* packages use first 4 characters (e.g. "libfoo" -> "libf")
    - All other packages use first character (e.g. "hello" -> "h")

    Args:
        package_name: A valid Debian package name.

    Returns:
        The pool prefix string.
    """
    if package_name.startswith("lib"):
        return package_name[:4]
    return package_name[0]


def compute_pool_path(
    package_name: str,
    version: str,
    arch: str,
    component: str = "main",
) -> str:
    """Compute the full pool path for a .deb file.

    Args:
        package_name: A valid Debian package name.
        version: Package version string.
        arch: Architecture string.
        component: Repository component (default: "main").

    Returns:
        The relative pool path where the .deb should be stored.
    """
    prefix = compute_pool_prefix(package_name)
    filename = f"{package_name}_{version}_{arch}.deb"
    return f"pool/{component}/{prefix}/{package_name}/{filename}"


def get_repo_directory_paths(
    repo_name: str,
    suite: str = "stable",
    archs: list[str] | None = None,
    component: str = "main",
) -> list[str]:
    """Compute the expected directory paths for a repository structure.

    Replicates the directory creation logic of create-repo.sh.

    Args:
        repo_name: Name of the repository.
        suite: Suite name (e.g. "stable", "unstable").
        archs: List of architectures. Defaults to ["amd64"].
        component: Component name (default: "main").

    Returns:
        Sorted list of directory paths that should exist.
    """
    if archs is None:
        archs = ["amd64"]

    paths = [
        f"{repo_name}/pool/{component}",
    ]
    for arch in archs:
        paths.append(f"{repo_name}/dists/{suite}/{component}/binary-{arch}")

    return sorted(paths)


# =============================================================================
# Property 2: Control file reflects arguments
# =============================================================================


@pytest.mark.unit
class TestProperty2ControlFileReflectsArguments:
    """Property 2: Control file reflects arguments.

    **Validates: Requirements 1.3, 1.5, 6.1, 6.2**

    For any valid combination of package name, architecture, version,
    dependencies, and description, the generated debian/control file
    SHALL contain those exact values in the corresponding fields, and
    the debian/changelog SHALL contain the version.
    """

    @settings(max_examples=100)
    @given(
        name=package_names,
        arch=architectures,
        version=versions,
        deps=dependencies,
        desc=descriptions,
    )
    def test_control_contains_package_name(self, name, arch, version, deps, desc):
        """Generated control file contains the exact Package field value."""
        control = generate_control_content(name=name, arch=arch, depends=deps, description=desc)
        assert f"Package: {name}" in control

    @settings(max_examples=100)
    @given(
        name=package_names,
        arch=architectures,
        version=versions,
        deps=dependencies,
        desc=descriptions,
    )
    def test_control_contains_architecture(self, name, arch, version, deps, desc):
        """Generated control file contains the exact Architecture field value."""
        control = generate_control_content(name=name, arch=arch, depends=deps, description=desc)
        assert f"Architecture: {arch}" in control

    @settings(max_examples=100)
    @given(
        name=package_names,
        arch=architectures,
        version=versions,
        deps=dependencies,
        desc=descriptions,
    )
    def test_control_contains_depends_when_nonempty(self, name, arch, version, deps, desc):
        """Generated control file contains the Depends field when dependencies are non-empty."""
        control = generate_control_content(name=name, arch=arch, depends=deps, description=desc)
        if deps:
            assert f"Depends: {deps}" in control
        else:
            # The binary package stanza should not have a Depends line.
            # Note: "Build-Depends:" exists in the source stanza, so check
            # that no line starts with exactly "Depends:".
            lines = control.splitlines()
            assert not any(line.startswith("Depends:") for line in lines)

    @settings(max_examples=100)
    @given(
        name=package_names,
        arch=architectures,
        version=versions,
        deps=dependencies,
        desc=descriptions,
    )
    def test_control_contains_description(self, name, arch, version, deps, desc):
        """Generated control file contains the exact Description field value."""
        control = generate_control_content(name=name, arch=arch, depends=deps, description=desc)
        assert f"Description: {desc}" in control

    @settings(max_examples=100)
    @given(
        name=package_names,
        arch=architectures,
        version=versions,
        deps=dependencies,
        desc=descriptions,
    )
    def test_changelog_contains_version(self, name, arch, version, deps, desc):
        """Generated changelog contains the name and version string."""
        changelog = generate_changelog_content(name=name, version=version)
        assert f"{name} ({version})" in changelog


# =============================================================================
# Property 1: Package skeleton completeness
# =============================================================================


@pytest.mark.unit
class TestProperty3RepositoryDirectoryStructure:
    """Property 3: Repository directory structure.

    **Validates: Requirements 3.1, 3.2, 3.5**

    For any valid combination of repository name, suite name, and set of
    architectures, the directory structure SHALL contain pool/main/ and
    dists/{suite}/main/binary-{arch}/ for each specified architecture.
    """

    @settings(max_examples=100)
    @given(
        repo_name=package_names,
        suite=suites,
        archs=arch_sets,
    )
    def test_pool_directory_exists_in_structure(self, repo_name, suite, archs):
        """Repository structure always contains pool/main directory."""
        paths = get_repo_directory_paths(repo_name=repo_name, suite=suite, archs=archs)
        pool_paths = [p for p in paths if "pool/main" in p]
        assert pool_paths, (
            f"For repo '{repo_name}', suite '{suite}', archs {archs}: "
            f"expected a path containing 'pool/main' but got {paths}"
        )

    @settings(max_examples=100)
    @given(
        repo_name=package_names,
        suite=suites,
        archs=arch_sets,
    )
    def test_binary_arch_directories_exist_for_each_architecture(self, repo_name, suite, archs):
        """Repository structure contains dists/{suite}/main/binary-{arch}/ for each architecture."""
        paths = get_repo_directory_paths(repo_name=repo_name, suite=suite, archs=archs)
        for arch in archs:
            expected_fragment = f"dists/{suite}/main/binary-{arch}"
            matching = [p for p in paths if expected_fragment in p]
            assert matching, (
                f"For repo '{repo_name}', suite '{suite}', arch '{arch}': "
                f"expected a path containing '{expected_fragment}' but got {paths}"
            )


@pytest.mark.unit
class TestProperty1PackageSkeletonCompleteness:
    """Property 1: Package skeleton completeness.

    **Validates: Requirements 1.1**

    For any valid Debian package name, the skeleton generation SHALL produce
    files for debian/control, debian/changelog, debian/rules, and debian/compat.
    """

    @settings(max_examples=100)
    @given(name=package_names)
    def test_skeleton_contains_all_required_files(self, name: str) -> None:
        """Skeleton always contains debian/control, changelog, rules, and compat."""
        skeleton = get_skeleton_files(name)

        required_files = {"debian/control", "debian/changelog", "debian/rules", "debian/compat"}
        assert set(skeleton.keys()) == required_files, (
            f"For package '{name}': expected keys {required_files}, got {set(skeleton.keys())}"
        )


# =============================================================================
# Property 4: Pool naming convention
# =============================================================================


# =============================================================================
# Unit Tests for Defaults and Validation (Task 5.4)
# =============================================================================


@pytest.mark.unit
class TestUnitDefaultsAndValidation:
    """Unit tests for default values and input validation.

    **Validates: Requirements 1.4, 1.6, 3.3, 3.4**
    """

    def test_default_architecture_is_all(self):
        """Default architecture in generate_control_content is 'all'."""
        control = generate_control_content(name="test-pkg")
        assert "Architecture: all" in control

    def test_default_version_is_1_0_1(self):
        """Default version in generate_changelog_content is '1.0-1'."""
        changelog = generate_changelog_content(name="test-pkg")
        assert "test-pkg (1.0-1)" in changelog

    def test_default_suite_is_stable(self):
        """Default suite in get_repo_directory_paths is 'stable'."""
        paths = get_repo_directory_paths(repo_name="test-repo")
        assert any("dists/stable/" in p for p in paths)

    def test_create_package_script_exists(self):
        """create-package.sh exists at the expected fixture path."""
        import pathlib

        script = pathlib.Path(__file__).parent.parent / "fixtures" / "create-package.sh"
        assert script.exists()
        if sys.platform == "win32":
            pytest.skip("Unix executable permissions not available on Windows")
        assert script.stat().st_mode & 0o111  # executable

    def test_create_repo_script_exists(self):
        """create-repo.sh exists at the expected fixture path."""
        import pathlib

        script = pathlib.Path(__file__).parent.parent / "fixtures" / "create-repo.sh"
        assert script.exists()
        if sys.platform == "win32":
            pytest.skip("Unix executable permissions not available on Windows")
        assert script.stat().st_mode & 0o111  # executable

    @pytest.mark.skipif(sys.platform == "win32", reason="Shell scripts cannot run natively on Windows")
    def test_invalid_package_names_rejected_by_script(self):
        """create-package.sh rejects invalid package names."""
        import pathlib
        import subprocess

        script = pathlib.Path(__file__).parent.parent / "fixtures" / "create-package.sh"
        # Names that fail regex ^[a-z0-9][a-z0-9.+\-]+$:
        # - uppercase letters not allowed
        # - starting with hyphen not allowed
        # - spaces not allowed
        # - single char doesn't satisfy the + quantifier (needs 2+ total chars)
        invalid_names = ["INVALID", "-bad", "has space", "a"]
        for name in invalid_names:
            result = subprocess.run(
                [str(script), name],
                capture_output=True,
                text=True,
            )
            assert result.returncode == 1, f"Expected rejection for '{name}'"


@pytest.mark.unit
class TestProperty4PoolNamingConvention:
    """Property 4: Pool naming convention.

    **Validates: Requirements 3.6**

    For any valid .deb filename with a package name, the file SHALL be
    placed in the pool at pool/{component}/{prefix}/{package-name}/ where
    prefix is the first four characters for packages starting with "lib"
    and the first character otherwise.
    """

    @settings(max_examples=100)
    @given(name=package_names)
    def test_regular_packages_use_first_character_prefix(self, name):
        """Regular packages (not starting with 'lib') use first character as prefix."""
        from hypothesis import assume

        assume(not name.startswith("lib"))
        prefix = compute_pool_prefix(name)
        assert prefix == name[0], f"Expected prefix '{name[0]}' for package '{name}', got '{prefix}'"

    @settings(max_examples=100)
    @given(name=lib_package_names)
    def test_lib_packages_use_first_four_character_prefix(self, name):
        """Library packages (starting with 'lib') use first four characters as prefix."""
        prefix = compute_pool_prefix(name)
        assert prefix == name[:4], f"Expected prefix '{name[:4]}' for package '{name}', got '{prefix}'"

    @settings(max_examples=100)
    @given(name=package_names, version=versions, arch=architectures)
    def test_pool_path_contains_correct_prefix(self, name, version, arch):
        """Pool path contains the correct prefix directory for any package."""
        pool_path = compute_pool_path(name, version, arch)
        prefix = compute_pool_prefix(name)

        # Verify pool path structure: pool/{component}/{prefix}/{package-name}/{filename}
        assert pool_path.startswith(f"pool/main/{prefix}/{name}/"), (
            f"Expected pool path to start with 'pool/main/{prefix}/{name}/', got '{pool_path}'"
        )

        # Verify the filename at the end
        expected_filename = f"{name}_{version}_{arch}.deb"
        assert pool_path.endswith(expected_filename), (
            f"Expected pool path to end with '{expected_filename}', got '{pool_path}'"
        )


# =============================================================================
# Property 5: Metadata regeneration idempotence
# =============================================================================

# Skip if required Debian tools are not available
requires_debian_tools = pytest.mark.skipif(
    not all(shutil.which(t) for t in ["dpkg-buildpackage", "dpkg-scanpackages", "apt-ftparchive", "gzip"]),
    reason="Requires dpkg-dev and apt-utils",
)


@pytest.mark.integration
@requires_debian_tools
class TestProperty5MetadataRegenerationIdempotence:
    """Property 5: Metadata regeneration idempotence.

    **Validates: Requirements 4.5**

    For any repository with a fixed set of .deb files in the pool,
    running metadata generation twice in succession SHALL produce
    identical Packages and Packages.gz file contents.
    """

    @staticmethod
    def _project_root() -> Path:
        """Return the project root directory."""
        return Path(__file__).parent.parent

    def test_metadata_regeneration_produces_identical_packages_files(self, tmp_path: Path) -> None:
        """Running metadata generation twice produces identical Packages files."""
        project_root = self._project_root()
        create_package = project_root / "fixtures" / "create-package.sh"
        create_repo = project_root / "fixtures" / "create-repo.sh"

        pkg_output_dir = tmp_path / "packages"
        pkg_output_dir.mkdir()

        # Step 1: Build a test .deb package
        result = subprocess.run(
            [
                str(create_package),
                "--build",
                "--version",
                "1.0-1",
                "--arch",
                "all",
                "--output-dir",
                str(pkg_output_dir) + "/",
                "testpkg",
            ],
            capture_output=True,
            text=True,
            cwd=str(project_root),
        )
        assert result.returncode == 0, f"create-package.sh failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"

        # Find the built .deb file (dpkg-buildpackage places it in the parent of the source tree)
        deb_files = list(pkg_output_dir.glob("*.deb"))
        if not deb_files:
            # Also check inside the package directory structure
            deb_files = list(pkg_output_dir.rglob("*.deb"))
        assert deb_files, (
            f"No .deb files found in {pkg_output_dir}. Directory contents: {list(pkg_output_dir.rglob('*'))}"
        )
        deb_file = deb_files[0]

        repo_output_dir = tmp_path / "repositories"
        repo_output_dir.mkdir()

        # Step 2: Create repository and add the package with metadata generation
        result = subprocess.run(
            [
                str(create_repo),
                "--add-package",
                str(deb_file),
                "--generate-metadata",
                "--output-dir",
                str(repo_output_dir) + "/",
                "test-repo",
            ],
            capture_output=True,
            text=True,
            cwd=str(project_root),
        )
        assert result.returncode == 0, (
            f"create-repo.sh (first run) failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        repo_dir = repo_output_dir / "test-repo"

        # Step 3: Read Packages file after first generation
        packages_file = repo_dir / "dists" / "stable" / "main" / "binary-amd64" / "Packages"
        packages_gz_file = repo_dir / "dists" / "stable" / "main" / "binary-amd64" / "Packages.gz"

        assert packages_file.exists(), f"Packages file not found at {packages_file}"
        assert packages_gz_file.exists(), f"Packages.gz file not found at {packages_gz_file}"

        first_packages_content = packages_file.read_bytes()
        first_packages_gz_content = packages_gz_file.read_bytes()

        # Step 4: Regenerate metadata (run again without --add-package)
        result = subprocess.run(
            [
                str(create_repo),
                "--generate-metadata",
                "--output-dir",
                str(repo_output_dir) + "/",
                "test-repo",
            ],
            capture_output=True,
            text=True,
            cwd=str(project_root),
        )
        assert result.returncode == 0, (
            f"create-repo.sh (second run) failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # Step 5: Read Packages file after second generation
        second_packages_content = packages_file.read_bytes()
        second_packages_gz_content = packages_gz_file.read_bytes()

        # Step 6: Assert idempotence - Packages files should be identical
        assert first_packages_content == second_packages_content, (
            "Packages file content changed between consecutive metadata generations.\n"
            f"First run ({len(first_packages_content)} bytes) != "
            f"Second run ({len(second_packages_content)} bytes)"
        )
        assert first_packages_gz_content == second_packages_gz_content, (
            "Packages.gz file content changed between consecutive metadata generations.\n"
            f"First run ({len(first_packages_gz_content)} bytes) != "
            f"Second run ({len(second_packages_gz_content)} bytes)"
        )

    def test_release_file_stable_fields_are_idempotent(self, tmp_path: Path) -> None:
        """Running metadata generation twice produces Release files with identical stable fields.

        The Date field changes between runs (expected). The checksum sections may
        include a self-referential Release entry on subsequent runs (apt-ftparchive
        includes existing files). We verify that the header fields and Packages file
        checksums remain identical.
        """
        project_root = self._project_root()
        create_package = project_root / "fixtures" / "create-package.sh"
        create_repo = project_root / "fixtures" / "create-repo.sh"

        pkg_output_dir = tmp_path / "packages"
        pkg_output_dir.mkdir()

        # Build a test .deb package
        result = subprocess.run(
            [
                str(create_package),
                "--build",
                "--version",
                "1.0-1",
                "--arch",
                "all",
                "--output-dir",
                str(pkg_output_dir) + "/",
                "testpkg",
            ],
            capture_output=True,
            text=True,
            cwd=str(project_root),
        )
        assert result.returncode == 0, f"create-package.sh failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"

        deb_files = list(pkg_output_dir.glob("*.deb"))
        if not deb_files:
            deb_files = list(pkg_output_dir.rglob("*.deb"))
        assert deb_files, f"No .deb files found in {pkg_output_dir}"
        deb_file = deb_files[0]

        repo_output_dir = tmp_path / "repositories"
        repo_output_dir.mkdir()

        # Create repository with metadata
        result = subprocess.run(
            [
                str(create_repo),
                "--add-package",
                str(deb_file),
                "--generate-metadata",
                "--output-dir",
                str(repo_output_dir) + "/",
                "test-repo",
            ],
            capture_output=True,
            text=True,
            cwd=str(project_root),
        )
        assert result.returncode == 0, (
            f"create-repo.sh (first run) failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        repo_dir = repo_output_dir / "test-repo"
        release_file = repo_dir / "dists" / "stable" / "Release"
        assert release_file.exists(), f"Release file not found at {release_file}"

        first_release_content = release_file.read_text()

        # Regenerate metadata
        result = subprocess.run(
            [
                str(create_repo),
                "--generate-metadata",
                "--output-dir",
                str(repo_output_dir) + "/",
                "test-repo",
            ],
            capture_output=True,
            text=True,
            cwd=str(project_root),
        )
        assert result.returncode == 0, (
            f"create-repo.sh (second run) failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        second_release_content = release_file.read_text()

        def extract_stable_fields(content: str) -> str:
            """Extract header fields and Packages checksum entries, excluding Date and Release self-ref."""
            lines = content.splitlines()
            stable_lines = []
            for line in lines:
                # Skip the Date field (changes between runs)
                if line.startswith("Date:"):
                    continue
                # Skip lines referencing the Release file itself (self-referential on re-runs)
                if "Release" in line and line.startswith(" "):
                    continue
                stable_lines.append(line)
            return "\n".join(stable_lines)

        first_stable = extract_stable_fields(first_release_content)
        second_stable = extract_stable_fields(second_release_content)

        assert first_stable == second_stable, (
            "Release file stable content changed between consecutive metadata generations.\n"
            f"First:\n{first_stable}\n\nSecond:\n{second_stable}"
        )


# =============================================================================
# Property 6: Release file parsability
# =============================================================================


@pytest.mark.integration
@requires_debian_tools
class TestProperty6ReleaseFileParsability:
    """Property 6: Release file parsability.

    **Validates: Requirements 8.2**

    For any generated repository, the Release file SHALL parse successfully
    with debcraft's ReleaseParser and the resulting ReleaseMetadata SHALL
    have non-null date, architectures, and a non-empty files list.
    """

    @staticmethod
    def _project_root() -> Path:
        """Return the project root directory."""
        return Path(__file__).parent.parent

    def test_release_file_parses_with_release_parser(self, tmp_path: Path) -> None:
        """Generated Release file parses successfully with ReleaseParser."""
        project_root = self._project_root()
        create_package = project_root / "fixtures" / "create-package.sh"
        create_repo = project_root / "fixtures" / "create-repo.sh"

        pkg_output_dir = tmp_path / "packages"
        pkg_output_dir.mkdir()

        # Step 1: Build a test .deb package
        result = subprocess.run(
            [
                str(create_package),
                "--build",
                "--version",
                "1.0-1",
                "--arch",
                "all",
                "--output-dir",
                str(pkg_output_dir) + "/",
                "testpkg",
            ],
            capture_output=True,
            text=True,
            cwd=str(project_root),
        )
        assert result.returncode == 0, f"create-package.sh failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"

        # Find the built .deb file
        deb_files = list(pkg_output_dir.glob("*.deb"))
        if not deb_files:
            deb_files = list(pkg_output_dir.rglob("*.deb"))
        assert deb_files, (
            f"No .deb files found in {pkg_output_dir}. Directory contents: {list(pkg_output_dir.rglob('*'))}"
        )
        deb_file = deb_files[0]

        repo_output_dir = tmp_path / "repositories"
        repo_output_dir.mkdir()

        # Step 2: Create repository with the package and generate metadata
        result = subprocess.run(
            [
                str(create_repo),
                "--add-package",
                str(deb_file),
                "--generate-metadata",
                "--output-dir",
                str(repo_output_dir) + "/",
                "test-repo",
            ],
            capture_output=True,
            text=True,
            cwd=str(project_root),
        )
        assert result.returncode == 0, f"create-repo.sh failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"

        repo_dir = repo_output_dir / "test-repo"
        release_file = repo_dir / "dists" / "stable" / "Release"
        assert release_file.exists(), f"Release file not found at {release_file}"

        # Step 3: Read the generated Release file content
        content = release_file.read_text()

        # Step 4: Parse with ReleaseParser
        parser = ReleaseParser()
        metadata = parser.parse(content, url="file://test")

        # Step 5: Assert non-null date, architectures, and non-empty files list
        assert metadata.date is not None, "ReleaseMetadata.date is None; expected a non-null Date field."
        assert metadata.architectures is not None, (
            "ReleaseMetadata.architectures is None; expected a non-null Architectures field."
        )
        assert len(metadata.files) > 0, "ReleaseMetadata.files is empty; expected at least one SHA256 entry."


# =============================================================================
# End-to-End Flow and Size Constraints (Task 6.1)
# =============================================================================


def _get_dir_size(path: Path) -> int:
    """Calculate total size of all files in a directory tree.

    Args:
        path: Root directory to measure.

    Returns:
        Total size in bytes of all files under path.
    """
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            total += os.path.getsize(fp)
    return total


@pytest.mark.integration
@requires_debian_tools
class TestEndToEndFlowAndSizeConstraints:
    """Integration tests for end-to-end flow and size constraints.

    **Validates: Requirements 5.1, 5.2, 5.3, 8.1, 8.3**
    """

    @staticmethod
    def _project_root() -> Path:
        """Return the project root directory."""
        return Path(__file__).parent.parent

    def test_built_deb_is_under_5kb(self, tmp_path: Path) -> None:
        """Built .deb for empty package is under 5KB (Req 5.1)."""
        project_root = self._project_root()
        create_package = project_root / "fixtures" / "create-package.sh"

        pkg_output_dir = tmp_path / "packages"
        pkg_output_dir.mkdir()

        # Build a minimal test .deb package
        result = subprocess.run(
            [
                str(create_package),
                "--build",
                "--version",
                "1.0-1",
                "--arch",
                "all",
                "--output-dir",
                str(pkg_output_dir) + "/",
                "testpkg",
            ],
            capture_output=True,
            text=True,
            cwd=str(project_root),
        )
        assert result.returncode == 0, f"create-package.sh failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"

        # Find the built .deb file
        deb_files = list(pkg_output_dir.glob("*.deb"))
        if not deb_files:
            deb_files = list(pkg_output_dir.rglob("*.deb"))
        assert deb_files, (
            f"No .deb files found in {pkg_output_dir}. Directory contents: {list(pkg_output_dir.rglob('*'))}"
        )

        deb_file = deb_files[0]
        deb_size = deb_file.stat().st_size

        # Assert .deb is under 5KB (5120 bytes)
        assert deb_size < 5120, f"Built .deb file is {deb_size} bytes, expected under 5KB (5120 bytes)"

    def test_single_package_repo_is_under_20kb(self, tmp_path: Path) -> None:
        """Complete single-package repository is under 20KB (Req 5.2)."""
        project_root = self._project_root()
        create_package = project_root / "fixtures" / "create-package.sh"
        create_repo = project_root / "fixtures" / "create-repo.sh"

        pkg_output_dir = tmp_path / "packages"
        pkg_output_dir.mkdir()

        # Build a minimal test .deb package
        result = subprocess.run(
            [
                str(create_package),
                "--build",
                "--version",
                "1.0-1",
                "--arch",
                "all",
                "--output-dir",
                str(pkg_output_dir) + "/",
                "testpkg",
            ],
            capture_output=True,
            text=True,
            cwd=str(project_root),
        )
        assert result.returncode == 0, f"create-package.sh failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"

        deb_files = list(pkg_output_dir.glob("*.deb"))
        if not deb_files:
            deb_files = list(pkg_output_dir.rglob("*.deb"))
        assert deb_files, f"No .deb files found in {pkg_output_dir}"
        deb_file = deb_files[0]

        repo_output_dir = tmp_path / "repositories"
        repo_output_dir.mkdir()

        # Create repository with the package and generate metadata
        result = subprocess.run(
            [
                str(create_repo),
                "--add-package",
                str(deb_file),
                "--generate-metadata",
                "--output-dir",
                str(repo_output_dir) + "/",
                "test-repo",
            ],
            capture_output=True,
            text=True,
            cwd=str(project_root),
        )
        assert result.returncode == 0, f"create-repo.sh failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"

        repo_dir = repo_output_dir / "test-repo"
        total_size = _get_dir_size(repo_dir)

        # Assert total repository size is under 20KB (20480 bytes)
        assert total_size < 20480, f"Repository total size is {total_size} bytes, expected under 20KB (20480 bytes)"

    def test_end_to_end_package_to_repo_flow(self, tmp_path: Path) -> None:
        """Package built by create-package.sh can be added to repo by create-repo.sh (Req 8.1)."""
        project_root = self._project_root()
        create_package = project_root / "fixtures" / "create-package.sh"
        create_repo = project_root / "fixtures" / "create-repo.sh"

        pkg_output_dir = tmp_path / "packages"
        pkg_output_dir.mkdir()

        # Step 1: Build a test .deb package
        result = subprocess.run(
            [
                str(create_package),
                "--build",
                "--version",
                "1.0-1",
                "--arch",
                "all",
                "--output-dir",
                str(pkg_output_dir) + "/",
                "testpkg",
            ],
            capture_output=True,
            text=True,
            cwd=str(project_root),
        )
        assert result.returncode == 0, f"create-package.sh failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"

        deb_files = list(pkg_output_dir.glob("*.deb"))
        if not deb_files:
            deb_files = list(pkg_output_dir.rglob("*.deb"))
        assert deb_files, f"No .deb files found in {pkg_output_dir}"
        deb_file = deb_files[0]

        repo_output_dir = tmp_path / "repositories"
        repo_output_dir.mkdir()

        # Step 2: Create a repo and add the package with metadata generation
        result = subprocess.run(
            [
                str(create_repo),
                "--add-package",
                str(deb_file),
                "--generate-metadata",
                "--output-dir",
                str(repo_output_dir) + "/",
                "test-repo",
            ],
            capture_output=True,
            text=True,
            cwd=str(project_root),
        )
        assert result.returncode == 0, f"create-repo.sh failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"

        repo_dir = repo_output_dir / "test-repo"

        # Step 3: Verify Packages file lists the package
        packages_file = repo_dir / "dists" / "stable" / "main" / "binary-amd64" / "Packages"
        assert packages_file.exists(), f"Packages file not found at {packages_file}"

        packages_content = packages_file.read_text()
        assert "Package: testpkg" in packages_content, (
            f"Package 'testpkg' not found in Packages file.\nContent:\n{packages_content}"
        )

        # Step 4: Verify Release file exists and has content
        release_file = repo_dir / "dists" / "stable" / "Release"
        assert release_file.exists(), f"Release file not found at {release_file}"

        release_content = release_file.read_text()
        assert len(release_content) > 0, "Release file is empty"
        assert "Suite: stable" in release_content, f"Release file missing 'Suite: stable'.\nContent:\n{release_content}"
        assert "Architectures:" in release_content, (
            f"Release file missing 'Architectures:' field.\nContent:\n{release_content}"
        )
