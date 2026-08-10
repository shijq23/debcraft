# Bugfix Requirements Document

## Introduction

The `_infer_file_type()` function in `src/debcraft/domain/indexer/service.py` uses naive substring matching on the full URL to classify repository files. This causes `.deb` binary package files to be misclassified as metadata file types when their URL path contains substrings like "packages", "sources", or "release" in directory names. Misclassified `.deb` files are then read by the `LocalFileReader` as if they were UTF-8 metadata, resulting in `UnicodeDecodeError` and `OSError("Failed to decompress file: ...")`.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN a `.deb` file URL contains the substring "packages" in a directory name (e.g., `pool/main/l/lxde-metapackages/lxde-core_11_all.deb`) THEN the system incorrectly classifies it as file type "packages"

1.2 WHEN a `.deb` file URL contains the substring "sources" in a directory name (e.g., `pool/main/t/testresources/python3-testresources_2.0.1-4_all.deb`) THEN the system incorrectly classifies it as file type "sources"

1.3 WHEN a `.deb` file URL contains the substring "release" in a directory name (e.g., `pool/main/l/lsb-release-minimal/lsb-release-minimal_12.0-1_all.deb`) THEN the system incorrectly classifies it as file type "release"

1.4 WHEN a `.deb` file URL contains "sources" as part of a longer word in the path (e.g., "resources" in `pool/main/i/importlib-resources/python3-importlib-resources_5.1.2-2_all.deb`) THEN the system incorrectly classifies it as file type "sources"

### Expected Behavior (Correct)

2.1 WHEN the filename (last path segment, after stripping compression extensions) starts with or equals "Packages" (case-insensitive) THEN the system SHALL classify the file as type "packages"

2.2 WHEN the filename (last path segment, after stripping compression extensions) starts with or equals "Sources" (case-insensitive) THEN the system SHALL classify the file as type "sources"

2.3 WHEN the filename (last path segment, after stripping compression extensions) starts with "Contents" (case-insensitive) THEN the system SHALL classify the file as type "contents"

2.4 WHEN the filename (last path segment, after stripping compression extensions) equals "Release" or "InRelease" (case-insensitive) THEN the system SHALL classify the file as type "release"

2.5 WHEN a file has an extension of `.deb`, `.udeb`, `.dsc`, or any other non-metadata extension THEN the system SHALL classify the file as type "unknown" regardless of directory names in the path

### Unchanged Behavior (Regression Prevention)

3.1 WHEN a legitimate metadata file URL like `dists/bookworm/main/binary-amd64/Packages.gz` is provided THEN the system SHALL CONTINUE TO classify it as type "packages"

3.2 WHEN a legitimate metadata file URL like `dists/bookworm/main/source/Sources.xz` is provided THEN the system SHALL CONTINUE TO classify it as type "sources"

3.3 WHEN a legitimate metadata file URL like `dists/bookworm/main/Contents-amd64.gz` is provided THEN the system SHALL CONTINUE TO classify it as type "contents"

3.4 WHEN a legitimate metadata file URL like `dists/bookworm/Release` or `dists/bookworm/InRelease` is provided THEN the system SHALL CONTINUE TO classify it as type "release"

3.5 WHEN a file with an unrecognized filename pattern is provided THEN the system SHALL CONTINUE TO classify it as type "unknown"

3.6 WHEN any URL or path is provided THEN the `_infer_file_type()` function SHALL CONTINUE TO accept a single string argument and return one of: "packages", "sources", "contents", "release", or "unknown"
