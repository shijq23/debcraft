# Bugfix Requirements Document

## Introduction

The indexer command (`debcraft --verbose index`) returns 0 packages indexed for repositories where metadata files (Packages.gz, Sources, Contents, Release) were previously indexed. After the first successful index run, `mark_indexed()` transitions metadata files from VERIFIED → INDEXED state. On subsequent mirror syncs, when the mirror engine's `_stage_indexes()` determines an index file is unchanged (already cached), it skips the download and does NOT call `_upsert_repository_file()` to reset the file's state. Since `get_verified_files()` only queries files in VERIFIED state, the indexer never sees the metadata files again — only newly-synced `.deb` artifacts appear in VERIFIED state, and these are correctly skipped as "unknown" file type, resulting in 0 packages indexed.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN the mirror engine skips an index file in `_stage_indexes()` because it is already cached (checksum unchanged) THEN the system does not call `_upsert_repository_file()` and the file's state remains INDEXED from the previous indexer run

1.2 WHEN `get_verified_files()` is called for a repository whose metadata files are all in INDEXED state THEN the system returns only `.deb` artifact files (which are in VERIFIED state) and no metadata files

1.3 WHEN the indexer processes a list containing only `.deb` artifact files THEN the system skips all files as "unknown" file type and reports "0 packages, 0 source packages, 0 file ownerships" indexed

1.4 WHEN a user re-runs `debcraft index` after a prior successful index without any metadata file changes in the upstream repository THEN the system produces an empty index result despite valid metadata being locally cached

### Expected Behavior (Correct)

2.1 WHEN the mirror engine skips an index file in `_stage_indexes()` because it is already cached THEN the system SHALL reset the file's state to VERIFIED so that the indexer can re-evaluate it on the next run

2.2 WHEN `get_verified_files()` is called for a repository that has locally-cached metadata files THEN the system SHALL return those metadata files regardless of whether they were previously indexed

2.3 WHEN the indexer processes metadata files that have already been indexed with the same parser version and checksum THEN the system SHALL skip them via incremental indexing logic (`_should_skip`) without re-parsing, and report them as "files skipped"

2.4 WHEN a user re-runs `debcraft index` after a prior successful index THEN the system SHALL correctly report the skipped file count (not 0 packages with 0 skipped) indicating it evaluated the metadata files

### Unchanged Behavior (Regression Prevention)

3.1 WHEN a metadata file has genuinely changed (new checksum from upstream) THEN the system SHALL CONTINUE TO download the new version, set its state to VERIFIED, and re-index it with the updated content

3.2 WHEN a `.deb` artifact file is in VERIFIED state THEN the system SHALL CONTINUE TO skip it as "unknown" file type during indexing without attempting to read or parse it

3.3 WHEN a metadata file's checksum matches the previously indexed checksum and the parser version is unchanged THEN the system SHALL CONTINUE TO skip re-parsing via the incremental indexing check (`_should_skip`)

3.4 WHEN a metadata file's parser version has increased since last indexing THEN the system SHALL CONTINUE TO re-parse it even if the file checksum is unchanged

3.5 WHEN the mirror engine downloads a new index file successfully THEN the system SHALL CONTINUE TO call `_upsert_repository_file()` with state=VERIFIED and persist the file record
