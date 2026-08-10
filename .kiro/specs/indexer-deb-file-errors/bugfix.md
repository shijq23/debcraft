# Bugfix Requirements Document

## Introduction

The indexer command (`uv run debcraft --verbose index`) fails when processing `.deb` binary package files retrieved from the mirror database. Two related bugs cause this: (1) the indexer attempts to read and parse `.deb` files that it cannot handle, and (2) when errors occur, the custom log formatter drops exception tracebacks, making debugging difficult. Together these bugs produce noisy error output with no actionable diagnostic information.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN a `.deb` file (or other non-metadata binary file) is in VERIFIED state THEN the system attempts to read and UTF-8 decode its binary content, resulting in an `OSError("Failed to decode file as UTF-8: ...")` exception

1.2 WHEN the indexer encounters a file whose URL does not contain "packages", "sources", "contents", or "release" THEN the system returns file type "unknown" and logs a warning, but still reads the file from disk before reaching the type check

1.3 WHEN `logger.exception()` is called with `exc_info=True` and the `_StructuredFormatter` formats the log record THEN the system discards the exception traceback because the formatter never calls `self.formatException(record.exc_info)`

1.4 WHEN an error occurs during file processing with `--verbose` enabled THEN the system displays only `ERROR debcraft.domain.indexer.service: Error processing file: <url>` with no stack trace or underlying exception details

### Expected Behavior (Correct)

2.1 WHEN a `.deb` file (or other non-metadata binary file) is encountered during indexing THEN the system SHALL skip it early without attempting to read the file from disk, and log a debug message indicating the file was skipped

2.2 WHEN the indexer determines a file's type is "unknown" THEN the system SHALL skip the file before calling `file_reader.read_file()`, avoiding unnecessary I/O and potential decode errors

2.3 WHEN `logger.exception()` is called with `exc_info=True` and `_StructuredFormatter` formats the log record THEN the system SHALL include the full exception traceback in the formatted output

2.4 WHEN an error occurs during file processing with `--verbose` enabled THEN the system SHALL display the ERROR line followed by the complete exception chain and traceback

### Unchanged Behavior (Regression Prevention)

3.1 WHEN a Packages file (`.gz`, `.xz`, or uncompressed) is in VERIFIED state THEN the system SHALL CONTINUE TO read, decompress, parse, and index it successfully

3.2 WHEN a Sources file is in VERIFIED state THEN the system SHALL CONTINUE TO read, parse, and index it successfully

3.3 WHEN a Contents file is in VERIFIED state THEN the system SHALL CONTINUE TO read, parse, and index it successfully

3.4 WHEN a Release or InRelease file is in VERIFIED state THEN the system SHALL CONTINUE TO parse it and log a debug message

3.5 WHEN a log record without `exc_info` is formatted by `_StructuredFormatter` THEN the system SHALL CONTINUE TO produce the same `LEVELNAME logger.name: message key=value` output format

3.6 WHEN structured extra fields are present on a log record THEN the system SHALL CONTINUE TO append them as `key=value` pairs in the formatted output
