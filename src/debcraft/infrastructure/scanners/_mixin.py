"""Shared boilerplate mixin for scanner implementations.

Provides common cancellation-check, filesystem-analysis dispatch,
progress-reporting, and scan-result construction methods to eliminate
duplication across the seven scanner modules.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from debcraft.domain.scanner.errors import ScannerError
from debcraft.domain.scanner.filesystem_analyzer import analyze_filesystem
from debcraft.domain.scanner.values import ScanningStrategy, ScanResult

if TYPE_CHECKING:
    from debcraft.domain.scanner.ports import ContentsIndexPort, PackageLookupPort
    from debcraft.domain.scanner.values import IdentifiedPackage
    from debcraft.platform.contracts.workflow import WorkflowContext


class ScannerMixin:
    """Shared boilerplate for scanner implementations.

    This is a plain mixin class (not an ABC) providing common methods
    for cancellation checking, filesystem analysis dispatch, and
    progress reporting. Scanner classes include this via multiple
    inheritance alongside their primary definition.
    """

    def _check_cancellation(self, context: WorkflowContext, artifact_path: str, step: str) -> None:
        """Check cancellation token and raise if cancelled.

        Args:
            context: Workflow context providing the cancellation token.
            artifact_path: Path to the artifact being scanned (for diagnostics).
            step: Description of the current scanning step (for diagnostics).

        Raises:
            ScannerError: If the cancellation token indicates cancellation,
                with a message containing both the artifact path and step.
        """
        if context.cancellation_token.is_cancelled:
            raise ScannerError(f"Scan cancelled for '{artifact_path}' during {step}")

    async def _run_filesystem_analysis(
        self,
        file_paths: list[str],
        contents_port: ContentsIndexPort,
        package_port: PackageLookupPort,
        snapshot_id: int,
        context: WorkflowContext,
    ) -> tuple[list[IdentifiedPackage], list[str]]:
        """Run filesystem analysis with cancellation check and progress report.

        Invokes analyze_filesystem, checks cancellation after completion,
        reports progress at 100%, and returns the identified packages
        and diagnostics.

        Args:
            file_paths: Filesystem paths to analyze.
            contents_port: Port for Contents index lookups.
            package_port: Port for package metadata lookups.
            snapshot_id: Repository snapshot ID for consistent queries.
            context: Workflow context for cancellation and progress.

        Returns:
            Tuple of (packages, diagnostics) from the analysis.

        Raises:
            ScannerError: If cancelled after analysis completes.
        """
        result = await analyze_filesystem(
            file_paths=file_paths,
            contents_port=contents_port,
            package_port=package_port,
            snapshot_id=snapshot_id,
        )

        self._check_cancellation(context, "filesystem-analysis", "post-analysis")
        self._report_progress(
            context, 100.0, f"Filesystem analysis complete: {len(result.packages)} packages identified"
        )

        return result.packages, result.diagnostics

    def _report_progress(self, context: WorkflowContext, percentage: float, message: str) -> None:
        """Delegate progress reporting to the workflow context.

        Args:
            context: Workflow context providing the progress reporter.
            percentage: Progress percentage from 0.0 to 100.0.
            message: Human-readable progress description (max 256 characters).
        """
        context.progress.report(percentage, message)

    def _build_cancellation_result(
        self,
        *,
        step: str,
        start_time: float,
        strategy: str,
        artifact_path: str,
        diagnostics: list[str],
    ) -> ScanResult:
        """Build a ScanResult for early-exit on cancellation.

        Constructs a result with empty packages, a cancellation diagnostic
        recording the step name, and elapsed duration from start_time.

        Args:
            step: Description of the scanning step where cancellation occurred.
            start_time: perf_counter value at scan start.
            strategy: The scanning strategy string for the result.
            artifact_path: Path to the artifact being scanned.
            diagnostics: Accumulated diagnostics list (will be extended with
                cancellation message).

        Returns:
            ScanResult with empty packages and cancellation diagnostic.
        """
        duration = time.perf_counter() - start_time
        diagnostics.append(f"Scan cancelled during {step}")
        return ScanResult(
            packages=[],
            strategy=strategy,
            diagnostics=diagnostics,
            duration_seconds=duration,
            artifact_path=artifact_path,
        )

    def _iterate_packages_with_cancellation(
        self,
        packages: list[IdentifiedPackage],
        context: WorkflowContext,
        start_time: float,
        strategy: str,
        *,
        artifact_path: str,
        diagnostics: list[str],
    ) -> ScanResult:
        """Iterate packages checking cancellation between entries.

        Returns a ScanResult containing all packages if not cancelled,
        or partial packages plus a cancellation diagnostic if cancelled
        mid-iteration.

        Args:
            packages: Full list of identified packages to iterate.
            context: Workflow context providing the cancellation token.
            start_time: perf_counter value at scan start.
            strategy: The scanning strategy string for the result.
            artifact_path: Path to the artifact being scanned.
            diagnostics: Accumulated diagnostics list (may be extended with
                cancellation message).

        Returns:
            ScanResult with all packages (if not cancelled) or partial
            packages plus cancellation diagnostic (if cancelled).
        """
        accepted: list[IdentifiedPackage] = []
        for pkg in packages:
            if context.cancellation_token.is_cancelled:
                duration = time.perf_counter() - start_time
                diagnostics.append(f"Scan cancelled after processing {len(accepted)} of {len(packages)} packages")
                return ScanResult(
                    packages=accepted,
                    strategy=strategy,
                    diagnostics=diagnostics,
                    duration_seconds=duration,
                    artifact_path=artifact_path,
                )
            accepted.append(pkg)

        duration = time.perf_counter() - start_time
        return ScanResult(
            packages=accepted,
            strategy=strategy,
            diagnostics=diagnostics,
            duration_seconds=duration,
            artifact_path=artifact_path,
        )

    def _build_success_result(
        self,
        *,
        packages: list[IdentifiedPackage],
        strategy: str,
        diagnostics: list[str],
        start_time: float,
        artifact_path: str,
    ) -> ScanResult:
        """Build a ScanResult for successful scan completion.

        Constructs the final result with the full package list, strategy,
        accumulated diagnostics, elapsed duration, and artifact path.

        Args:
            packages: Complete list of identified packages.
            strategy: The scanning strategy string for the result.
            diagnostics: Accumulated diagnostics list.
            start_time: perf_counter value at scan start.
            artifact_path: Path to the artifact being scanned.

        Returns:
            ScanResult with all fields populated for a successful scan.
        """
        duration = time.perf_counter() - start_time
        return ScanResult(
            packages=packages,
            strategy=strategy,
            diagnostics=diagnostics,
            duration_seconds=duration,
            artifact_path=artifact_path,
        )

    def _build_empty_result(
        self,
        *,
        strategy: str,
        diagnostics: list[str],
        start_time: float,
        artifact_path: str,
    ) -> ScanResult:
        """Build a ScanResult with empty packages for error/edge cases.

        Constructs a result with no packages, the given strategy,
        accumulated diagnostics, elapsed duration, and artifact path.

        Args:
            strategy: The scanning strategy string for the result.
            diagnostics: Accumulated diagnostics list.
            start_time: perf_counter value at scan start.
            artifact_path: Path to the artifact being scanned.

        Returns:
            ScanResult with empty packages.
        """
        duration = time.perf_counter() - start_time
        return ScanResult(
            packages=[],
            strategy=strategy,
            diagnostics=diagnostics,
            duration_seconds=duration,
            artifact_path=artifact_path,
        )

    async def _analyze_and_build_filesystem_result(
        self,
        *,
        file_paths: list[str],
        contents_port: ContentsIndexPort,
        package_port: PackageLookupPort,
        artifact: object,
        context: WorkflowContext,
        start_time: float,
        artifact_path: str,
        diagnostics: list[str],
        use_cancellation_iteration: bool = True,
        pre_cancellation_step: str = "",
    ) -> ScanResult:
        """Run filesystem analysis and build a ScanResult.

        Consolidates the common pattern of checking pre-cancellation,
        extracting snapshot_id, invoking analyze_filesystem, extending
        diagnostics, and building the final result. Optionally uses
        cancellation-aware iteration.

        Args:
            file_paths: Filesystem paths to analyze.
            contents_port: Port for Contents index lookups.
            package_port: Port for package metadata lookups.
            artifact: The artifact descriptor (must have .options dict).
            context: Workflow context for cancellation and progress.
            start_time: perf_counter value at scan start.
            artifact_path: Path to the artifact being scanned.
            diagnostics: Accumulated diagnostics list.
            use_cancellation_iteration: If True, use _iterate_packages_with_cancellation;
                otherwise use _build_success_result directly.
            pre_cancellation_step: If non-empty, check cancellation before analysis
                and return a cancellation result with this step name if cancelled.

        Returns:
            ScanResult with filesystem_analysis strategy.
        """
        if pre_cancellation_step and context.cancellation_token.is_cancelled:
            self._report_progress(context, 100.0, "Scan cancelled")
            return self._build_cancellation_result(
                step=pre_cancellation_step,
                start_time=start_time,
                strategy=ScanningStrategy.FILESYSTEM_ANALYSIS.value,
                artifact_path=artifact_path,
                diagnostics=diagnostics,
            )

        snapshot_id = int(artifact.options.get("snapshot_id", "0"))  # type: ignore[attr-defined]

        result = await analyze_filesystem(
            file_paths=file_paths,
            contents_port=contents_port,
            package_port=package_port,
            snapshot_id=snapshot_id,
        )

        diagnostics.extend(result.diagnostics)

        if use_cancellation_iteration:
            scan_result = self._iterate_packages_with_cancellation(
                result.packages,
                context,
                start_time,
                ScanningStrategy.FILESYSTEM_ANALYSIS.value,
                artifact_path=artifact_path,
                diagnostics=diagnostics,
            )
        else:
            scan_result = self._build_success_result(
                packages=result.packages,
                strategy=ScanningStrategy.FILESYSTEM_ANALYSIS.value,
                diagnostics=diagnostics,
                start_time=start_time,
                artifact_path=artifact_path,
            )

        self._report_progress(
            context,
            100.0,
            f"Scan complete: identified {len(scan_result.packages)} packages via filesystem analysis",
        )
        return scan_result

    def _build_dpkg_success_result(
        self,
        *,
        parse_result: object,
        context: WorkflowContext,
        start_time: float,
        artifact_path: str,
        diagnostics: list[str],
    ) -> ScanResult:
        """Build a success ScanResult from dpkg parse output.

        Consolidates the common pattern of extending diagnostics from
        parse_result, reporting 100% progress, and building the final result.

        Args:
            parse_result: The parse result object (must have .packages and .diagnostics).
            context: Workflow context for progress reporting.
            start_time: perf_counter value at scan start.
            artifact_path: Path to the artifact being scanned.
            diagnostics: Accumulated diagnostics list.

        Returns:
            ScanResult with dpkg_metadata strategy.
        """
        diagnostics.extend(parse_result.diagnostics)  # type: ignore[attr-defined]

        self._report_progress(
            context,
            100.0,
            f"Scan complete: identified {len(parse_result.packages)} packages",  # type: ignore[attr-defined]
        )
        return self._build_success_result(
            packages=parse_result.packages,  # type: ignore[attr-defined]
            strategy=ScanningStrategy.DPKG_METADATA.value,
            diagnostics=diagnostics,
            start_time=start_time,
            artifact_path=artifact_path,
        )
