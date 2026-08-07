"""Configuration models for repository mirroring.

Frozen dataclasses representing repository and mirror configuration
with validation logic enforcing the constraints from Requirements 8.1 and 8.6.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse


@dataclass(frozen=True)
class RepositoryConfig:
    """Configuration for a single repository to mirror.

    Attributes:
        name: Unique identifier, 1-128 characters.
        base_url: HTTP or HTTPS URL of the repository root.
        suites: Distribution suites to mirror, 1-20 entries.
        components: Repository components to mirror, 1-50 entries.
        architectures: CPU architectures to mirror, 1-20 entries.
    """

    name: str
    base_url: str
    suites: list[str]
    components: list[str]
    architectures: list[str]


@dataclass(frozen=True)
class MirrorConfig:
    """Top-level mirror configuration.

    Attributes:
        repositories: List of repository configurations to mirror.
        download_timeout: Per-file download timeout in seconds (30-3600).
        max_connections_per_repo: Max concurrent HTTP connections per repository.
        max_total_connections: Max concurrent HTTP connections across all repositories.
        rate_limit_rps: Maximum requests per second for outgoing HTTP requests (1-1000).
            Controls the token replenishment rate in the token bucket rate limiter.
            Default is 50 requests per second.
        rate_limit_burst: Maximum burst size for the rate limiter (1-200).
            Controls the token bucket capacity, allowing short bursts of requests
            up to this limit. None means "use max_connections_per_repo" at runtime.
    """

    repositories: list[RepositoryConfig] = field(default_factory=list)
    download_timeout: int = 300
    max_connections_per_repo: int = 20
    max_total_connections: int = 60
    rate_limit_rps: float = 50.0
    rate_limit_burst: int | None = None


def validate_repository_config(config: RepositoryConfig) -> list[str]:
    """Validate a single repository configuration entry.

    Args:
        config: The repository configuration to validate.

    Returns:
        A list of error messages. Empty if the configuration is valid.
    """
    errors: list[str] = []

    # Name: 1-128 characters
    if not config.name:
        errors.append("Repository name must not be empty")
    elif len(config.name) > 128:
        errors.append(f"Repository name must be at most 128 characters, got {len(config.name)}")

    # Base URL: valid HTTP or HTTPS URL
    errors.extend(_validate_url(config.base_url))

    # Suites: 1-20 non-empty strings
    errors.extend(_validate_string_list(config.suites, "suites", min_count=1, max_count=20))

    # Components: 1-50 non-empty strings
    errors.extend(_validate_string_list(config.components, "components", min_count=1, max_count=50))

    # Architectures: 1-20 non-empty strings
    errors.extend(_validate_string_list(config.architectures, "architectures", min_count=1, max_count=20))

    return errors


def validate_mirror_config(config: MirrorConfig) -> list[str]:
    """Validate the complete mirror configuration.

    Checks all repository entries individually and enforces cross-entry
    constraints (name uniqueness, timeout bounds).

    Args:
        config: The mirror configuration to validate.

    Returns:
        A list of error messages. Empty if the configuration is valid.
    """
    errors: list[str] = []

    # Download timeout: 30-3600 seconds
    if config.download_timeout < 30:
        errors.append(f"download_timeout must be at least 30 seconds, got {config.download_timeout}")
    elif config.download_timeout > 3600:
        errors.append(f"download_timeout must be at most 3600 seconds, got {config.download_timeout}")

    # Rate limit RPS: 1-1000
    if config.rate_limit_rps < 1:
        errors.append(f"rate_limit_rps must be at least 1 request per second, got {config.rate_limit_rps}")
    elif config.rate_limit_rps > 1000:
        errors.append(f"rate_limit_rps must be at most 1000 requests per second, got {config.rate_limit_rps}")

    # Rate limit burst: 1-200 (only validate if explicitly set, None is valid)
    if config.rate_limit_burst is not None:
        if config.rate_limit_burst < 1:
            errors.append(f"rate_limit_burst must be at least 1, got {config.rate_limit_burst}")
        elif config.rate_limit_burst > 200:
            errors.append(f"rate_limit_burst must be at most 200, got {config.rate_limit_burst}")

    # Validate each repository entry
    for repo in config.repositories:
        repo_errors = validate_repository_config(repo)
        for err in repo_errors:
            errors.append(f"Repository '{repo.name}': {err}")

    # Name uniqueness across all entries
    names: list[str] = [repo.name for repo in config.repositories]
    seen: set[str] = set()
    for name in names:
        if name in seen:
            errors.append(f"Duplicate repository name: '{name}'")
        seen.add(name)

    return errors


def _validate_url(url: str) -> list[str]:
    """Validate that a URL is a valid HTTP or HTTPS URL.

    Args:
        url: The URL string to validate.

    Returns:
        A list of error messages. Empty if the URL is valid.
    """
    errors: list[str] = []

    if not url:
        errors.append("base_url must not be empty")
        return errors

    try:
        parsed = urlparse(url)
    except ValueError:
        errors.append(f"base_url is not a valid URL: '{url}'")
        return errors

    if parsed.scheme not in ("http", "https"):
        errors.append(f"base_url must use http or https scheme, got '{parsed.scheme}'")

    if not parsed.netloc:
        errors.append(f"base_url must have a valid host: '{url}'")

    return errors


def _validate_string_list(
    items: list[str],
    field_name: str,
    *,
    min_count: int,
    max_count: int,
) -> list[str]:
    """Validate a list of non-empty strings with count bounds.

    Args:
        items: The list of strings to validate.
        field_name: Name of the field for error messages.
        min_count: Minimum required number of entries.
        max_count: Maximum allowed number of entries.

    Returns:
        A list of error messages. Empty if the list is valid.
    """
    errors: list[str] = []

    if len(items) < min_count:
        errors.append(f"{field_name} must have at least {min_count} entry, got {len(items)}")
    elif len(items) > max_count:
        errors.append(f"{field_name} must have at most {max_count} entries, got {len(items)}")

    for i, item in enumerate(items):
        if not item:
            errors.append(f"{field_name}[{i}] must not be empty")

    return errors
