# Bugfix Requirements Document

## Introduction

The `mirror sync` CLI command fails to persist mirror state between runs. The `_CliDatabaseProvider` in `src/debcraft/cli/mirror.py` uses an ephemeral in-memory SQLite database (`sqlite+aiosqlite:///` with empty path). Every invocation starts with a blank database, causing `MirrorEngine._get_local_checksums()` to return empty results. This forces `FileComparator.compute_sync_decisions()` to mark all files as "download" (reason: "file not cached"), re-downloading everything on every run.

A secondary issue compounds this: the `check_conditional` call in `engine.py` (line ~270) passes no `etag` or `last_modified` values from the previously downloaded InRelease file, making HTTP 304 conditional requests ineffective even if the Release file is already on disk.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN `mirror sync` is run a second time after a successful first sync THEN the system re-downloads all files because `_CliDatabaseProvider` creates a fresh in-memory database with no prior state

1.2 WHEN `MirrorEngine._get_local_checksums()` queries the database for previously synced files THEN the system returns an empty dictionary because the in-memory database is discarded between process invocations

1.3 WHEN `FileComparator.compute_sync_decisions()` receives an empty `local_checksums` map THEN the system marks all remote entries as action="download" with reason="file not cached"

1.4 WHEN `MirrorEngine._stage_release()` calls `check_conditional(inrelease_url)` without etag or last_modified parameters THEN the system sends a conditional HTTP request with no If-None-Match or If-Modified-Since headers, making the 304 optimization ineffective

### Expected Behavior (Correct)

2.1 WHEN `mirror sync` is run a second time after a successful first sync THEN the system SHALL read previously stored checksum records from a persistent database at `~/.local/share/debcraft/mirror.db` and skip files whose checksums match

2.2 WHEN `MirrorEngine._get_local_checksums()` queries the persistent database THEN the system SHALL return a populated dictionary of relative_path → sha256 for all VERIFIED entries from prior syncs

2.3 WHEN `FileComparator.compute_sync_decisions()` receives a populated `local_checksums` map with matching checksums THEN the system SHALL mark those entries as action="skip" with reason="checksum matches"

2.4 WHEN `MirrorEngine._stage_release()` calls `check_conditional` for a previously downloaded InRelease file THEN the system SHALL pass the stored etag and/or last_modified values from the database to enable HTTP 304 responses

### Unchanged Behavior (Regression Prevention)

3.1 WHEN `mirror sync` is run for the first time with no prior database THEN the system SHALL CONTINUE TO download all files and create the database with checksum records

3.2 WHEN a remote file's SHA256 checksum differs from the locally stored checksum THEN the system SHALL CONTINUE TO re-download the file (action="download", reason="checksum differs")

3.3 WHEN `mirror verify` is run THEN the system SHALL CONTINUE TO read from the persistent mirror.db at the XDG data path and verify file checksums correctly

3.4 WHEN `mirror status` is run THEN the system SHALL CONTINUE TO display cached file counts and last sync timestamp from the persistent database

3.5 WHEN `mirror clean` is run THEN the system SHALL CONTINUE TO identify unreferenced files by querying the persistent mirror.db
