"""Bug condition exploration test for SQLAlchemy mapper resolution.

**Validates: Requirements 1.1, 1.2**

Property 1: Bug Condition — SQLAlchemy Mapper Resolution Failure

This test demonstrates that importing RepositorySnapshot via the models package
(without the explicit `import debcraft.infrastructure.models.scan` workaround)
causes SQLAlchemy's mapper to fail resolving the string-based relationship
reference "ScanSession".

The bug condition: `models/__init__.py` is empty so `scan.py` never loads,
and the string reference "ScanSession" in RepositorySnapshot.scan_sessions
cannot be resolved by SQLAlchemy's mapper.

This test MUST FAIL on unfixed code — failure confirms the bug exists.
"""

from __future__ import annotations

import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


@pytest.mark.unit
@pytest.mark.database
class TestMapperResolutionBugCondition:
    """Bug condition: mapper fails when models/__init__.py doesn't import all modules.

    **Validates: Requirements 1.1, 1.2**

    When importing RepositorySnapshot via the models package without explicitly
    importing the scan module, SQLAlchemy's mapper cannot resolve the string-based
    relationship reference "ScanSession", raising InvalidRequestError.
    """

    def test_mapper_resolves_scan_session_relationship_without_workaround(self) -> None:
        """Importing via models package should resolve all relationships.

        **Validates: Requirements 1.1, 1.2**

        This test imports RepositorySnapshot through the models package
        WITHOUT the `import debcraft.infrastructure.models.scan  # noqa: F401`
        workaround. It then attempts to use the mapper by creating a
        SQLAlchemy engine and configuring the registry.

        On UNFIXED code, this will raise:
            InvalidRequestError: When initializing mapper
            Mapper[RepositorySnapshot(repository_snapshots)],
            expression 'ScanSession' failed to locate a name

        The test asserts success (no error), so it FAILS on unfixed code,
        confirming the bug exists.
        """
        # Remove any previously cached imports of the scan module to simulate
        # a fresh import that only goes through the models package.
        # This ensures we test the real bug condition: models/__init__.py
        # doesn't import scan.py, so ScanSession is never registered.
        modules_to_remove = [key for key in sys.modules if key.startswith("debcraft.infrastructure.models")]
        removed_modules = {}
        for mod_key in modules_to_remove:
            removed_modules[mod_key] = sys.modules.pop(mod_key)

        try:
            # Import the models package — since __init__.py is empty,
            # only the package itself is loaded, not scan.py
            import debcraft.infrastructure.models  # noqa: F401

            # Import Base for creating tables
            from debcraft.infrastructure.models.base import Base

            # Now import RepositorySnapshot through the metadata module
            # This loads metadata.py and base.py, but NOT scan.py
            from debcraft.infrastructure.models.metadata import RepositorySnapshot

            # Create an in-memory SQLite engine and attempt to configure
            # the mapper by creating all tables — this triggers relationship
            # resolution
            engine = create_engine("sqlite:///:memory:")
            Base.metadata.create_all(engine)

            # Attempt to use the relationship — this is where the mapper
            # tries to resolve "ScanSession" and fails on unfixed code
            with Session(engine) as session:
                # Query that touches the scan_sessions relationship
                # This forces SQLAlchemy to resolve the "ScanSession" string
                from sqlalchemy import select
                from sqlalchemy.orm import selectinload

                stmt = select(RepositorySnapshot).options(selectinload(RepositorySnapshot.scan_sessions))
                # Execute the query — mapper resolution happens here
                session.execute(stmt).scalars().all()

        finally:
            # Restore original module state to avoid polluting other tests
            for mod_key in modules_to_remove:
                if mod_key in sys.modules:
                    del sys.modules[mod_key]
            sys.modules.update(removed_modules)
