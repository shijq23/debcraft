# Bugfix Requirements Document

## Introduction

The `SnapshotPublisher.publish_snapshot` method fails with `sqlite3.OperationalError: no such table: _migration_history` when publishing a snapshot. The `_get_schema_version` method executes a query against the `_migration_history` table without verifying that the table exists. This table is created by `MigrationRunner.ensure_history_table()`, but when migrations have never been run against the database (e.g., fresh database, CLI context, or when the migration runner hasn't been invoked yet), the table does not exist and the query crashes.

The fix should handle the missing table gracefully by returning a default schema version of 0, which is consistent with the method's existing fallback when no rows are found.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN `publish_snapshot` is called and the `_migration_history` table does not exist in the metadata database THEN the system raises `sqlite3.OperationalError: no such table: _migration_history` and the snapshot publication fails entirely

1.2 WHEN `_get_schema_version` executes `SELECT MAX(version) FROM _migration_history` on a database where the migration runner has never been invoked THEN the system crashes with an unhandled OperationalError instead of returning a safe default

### Expected Behavior (Correct)

2.1 WHEN `publish_snapshot` is called and the `_migration_history` table does not exist in the metadata database THEN the system SHALL return a schema version of 0 and continue snapshot publication without error

2.2 WHEN `_get_schema_version` executes on a database where the migration runner has never been invoked THEN the system SHALL handle the missing table gracefully and return 0 as the schema version

### Unchanged Behavior (Regression Prevention)

3.1 WHEN `publish_snapshot` is called and the `_migration_history` table exists with migration records THEN the system SHALL CONTINUE TO return the highest version number from the table

3.2 WHEN `publish_snapshot` is called and the `_migration_history` table exists but contains no rows THEN the system SHALL CONTINUE TO return 0 as the schema version

3.3 WHEN `publish_snapshot` is called with zero verified files THEN the system SHALL CONTINUE TO return None and publish a failure event without querying the schema version

3.4 WHEN a database error other than "no such table" occurs during `_get_schema_version` THEN the system SHALL CONTINUE TO propagate the exception and roll back the transaction
