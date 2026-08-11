# Bugfix Requirements Document

## Introduction

The `derive_mirror_root` function in `src/debcraft/infrastructure/mirror/paths.py` produces path collisions when a URL path contains only dot segments (e.g., `.` or `..`). Python's `pathlib.Path` normalizes dot segments away, so `Path("/a/b") / "."` resolves to `Path("/a/b")`. This means URLs like `http://0.0` and `http://0.0/.` both resolve to the same local path, violating the uniqueness requirement for mirror paths.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN the URL path consists entirely of dot segments (e.g., `http://host/.`) THEN the system produces the same local path as a URL with no path (e.g., `http://host`), because `Path` normalizes the `.` segment away

1.2 WHEN the URL path contains mixed dot and non-dot segments where all non-dot segments cancel out via `..` normalization (e.g., `http://host/a/..`) THEN the system may produce the same local path as a URL with no path, because `Path` normalizes `a/..` away

### Expected Behavior (Correct)

2.1 WHEN the URL path consists entirely of dot segments (e.g., `http://host/.`) THEN the system SHALL produce a local path that is distinct from the path derived for the same host with no URL path

2.2 WHEN the URL path contains segments that `Path` would normalize away (e.g., `.`, `..`, `a/..`) THEN the system SHALL encode or escape such segments so that every distinct URL path (as a string after stripping slashes) produces a distinct local filesystem path

### Unchanged Behavior (Regression Prevention)

3.1 WHEN the URL path contains only normal segments (no `.` or `..` components) THEN the system SHALL CONTINUE TO derive the path as `{mirror_base}/{hostname}/{url_path}`

3.2 WHEN the URL has no path (e.g., `http://host`) THEN the system SHALL CONTINUE TO derive the path as `{mirror_base}/{hostname}`

3.3 WHEN the URL path contains normal segments that include dots within names (e.g., `dists/elxr3.0/InRelease`) THEN the system SHALL CONTINUE TO preserve those segments literally in the derived path
