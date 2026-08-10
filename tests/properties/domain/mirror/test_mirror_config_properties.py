"""Property-based tests for mirror configuration validation.

**Validates: Requirements 8.1, 8.6**

Property 16: Configuration validation rejects all invalid inputs.
For any MirrorConfig where at least one invalidity condition holds
(empty/long name, duplicate names, invalid URL scheme, empty strings
in lists, list count out of bounds, timeout out of range), the
validation function SHALL return one or more error messages.

Property 17: Valid configuration is always accepted.
For any MirrorConfig where all names are unique 1-128 character strings,
all base_urls are valid HTTP/HTTPS URLs, all suites/components/architectures
are non-empty strings within their count limits, and download_timeout is
30-3600, the validation function SHALL return zero errors.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from debcraft.domain.mirror.config import (
    MirrorConfig,
    RepositoryConfig,
    validate_mirror_config,
    validate_repository_config,
)

# ---------------------------------------------------------------------------
# Strategies for generating valid components
# ---------------------------------------------------------------------------

# Characters safe for names (printable ASCII, no whitespace-only edge cases)
_NAME_CHARS = st.characters(
    whitelist_categories=("L", "N", "P", "S"),
    blacklist_characters="\x00",
)


def _valid_name() -> st.SearchStrategy[str]:
    """Generate a valid repository name: 1-128 non-empty characters."""
    return st.text(_NAME_CHARS, min_size=1, max_size=128).filter(lambda s: len(s.strip()) > 0)


def _valid_url() -> st.SearchStrategy[str]:
    """Generate a valid HTTP or HTTPS URL with a host."""
    scheme = st.sampled_from(["http", "https"])
    # Use simple hostname-safe characters
    host_chars = st.characters(
        whitelist_categories=("L", "N"),
        whitelist_characters="-.",
    )
    host = st.text(host_chars, min_size=1, max_size=30).filter(
        lambda h: h[0].isalnum() and h[-1].isalnum() and ".." not in h
    )
    path = st.text(
        st.characters(whitelist_categories=("L", "N"), whitelist_characters="/-_"),
        min_size=0,
        max_size=30,
    )
    return st.builds(lambda s, h, p: f"{s}://{h}/{p}", scheme, host, path)


def _non_empty_string() -> st.SearchStrategy[str]:
    """Generate a non-empty string (suitable for suites/components/architectures)."""
    return st.text(
        st.characters(whitelist_categories=("L", "N"), whitelist_characters="-_."),
        min_size=1,
        max_size=30,
    )


def _valid_string_list(min_count: int, max_count: int) -> st.SearchStrategy[list[str]]:
    """Generate a list of non-empty strings within count bounds."""
    return st.lists(_non_empty_string(), min_size=min_count, max_size=max_count)


def _valid_repository_config() -> st.SearchStrategy[RepositoryConfig]:
    """Generate a fully valid RepositoryConfig."""
    return st.builds(
        RepositoryConfig,
        name=_valid_name(),
        base_url=_valid_url(),
        suites=_valid_string_list(1, 20),
        components=_valid_string_list(1, 50),
        architectures=_valid_string_list(1, 20),
    )


def _valid_timeout() -> st.SearchStrategy[int]:
    """Generate a valid download_timeout in range [30, 3600]."""
    return st.integers(min_value=30, max_value=3600)


# ---------------------------------------------------------------------------
# Strategies for generating invalid components
# ---------------------------------------------------------------------------


def _invalid_name_empty() -> st.SearchStrategy[str]:
    """Generate an empty name."""
    return st.just("")


def _invalid_name_too_long() -> st.SearchStrategy[str]:
    """Generate a name exceeding 128 characters."""
    return st.text(_NAME_CHARS, min_size=129, max_size=200)


def _invalid_url_bad_scheme() -> st.SearchStrategy[str]:
    """Generate a URL with a non-http/https scheme."""
    bad_scheme = st.sampled_from(["ftp", "file", "ssh", "gopher", ""])
    host = st.just("example.com")
    return st.builds(lambda s, h: f"{s}://{h}/path", bad_scheme, host)


def _invalid_url_no_host() -> st.SearchStrategy[str]:
    """Generate a URL without a valid host."""
    return st.sampled_from(["https://", "http://", "https:///path", ""])


def _invalid_timeout_too_low() -> st.SearchStrategy[int]:
    """Generate a timeout below 30."""
    return st.integers(max_value=29)


def _invalid_timeout_too_high() -> st.SearchStrategy[int]:
    """Generate a timeout above 3600."""
    return st.integers(min_value=3601, max_value=100000)


# ---------------------------------------------------------------------------
# Property 16: Configuration validation rejects all invalid inputs
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
class TestProperty16InvalidConfigRejection:
    """Property 16: Configuration validation rejects all invalid inputs.

    For any MirrorConfig where at least one invalidity condition holds,
    the validation function SHALL return one or more error messages.
    """

    @settings(max_examples=200)
    @given(name=_invalid_name_empty())
    def test_empty_name_rejected(self, name: str) -> None:
        """Empty repository name is always rejected."""
        config = RepositoryConfig(
            name=name,
            base_url="https://example.com/repo",
            suites=["stable"],
            components=["main"],
            architectures=["amd64"],
        )
        errors = validate_repository_config(config)
        assert len(errors) > 0, "Empty name should produce validation errors"

    @settings(max_examples=200)
    @given(name=_invalid_name_too_long())
    def test_name_exceeding_128_chars_rejected(self, name: str) -> None:
        """Names longer than 128 characters are always rejected."""
        config = RepositoryConfig(
            name=name,
            base_url="https://example.com/repo",
            suites=["stable"],
            components=["main"],
            architectures=["amd64"],
        )
        errors = validate_repository_config(config)
        assert len(errors) > 0, f"Name of length {len(name)} should be rejected"

    @settings(max_examples=200)
    @given(url=_invalid_url_bad_scheme())
    def test_invalid_url_scheme_rejected(self, url: str) -> None:
        """URLs with schemes other than http/https are rejected."""
        config = RepositoryConfig(
            name="valid-name",
            base_url=url,
            suites=["stable"],
            components=["main"],
            architectures=["amd64"],
        )
        errors = validate_repository_config(config)
        assert len(errors) > 0, f"URL '{url}' with invalid scheme should be rejected"

    @settings(max_examples=200)
    @given(url=_invalid_url_no_host())
    def test_url_without_host_rejected(self, url: str) -> None:
        """URLs without a valid host are rejected."""
        config = RepositoryConfig(
            name="valid-name",
            base_url=url,
            suites=["stable"],
            components=["main"],
            architectures=["amd64"],
        )
        errors = validate_repository_config(config)
        assert len(errors) > 0, f"URL '{url}' without host should be rejected"

    @settings(max_examples=200)
    @given(
        count=st.integers(min_value=21, max_value=30),
    )
    def test_suites_exceeding_max_rejected(self, count: int) -> None:
        """Suites lists with more than 20 entries are rejected."""
        config = RepositoryConfig(
            name="valid-name",
            base_url="https://example.com/repo",
            suites=[f"suite{i}" for i in range(count)],
            components=["main"],
            architectures=["amd64"],
        )
        errors = validate_repository_config(config)
        assert len(errors) > 0, f"Suites list of {count} should be rejected"

    @settings(max_examples=200)
    @given(
        count=st.integers(min_value=51, max_value=70),
    )
    def test_components_exceeding_max_rejected(self, count: int) -> None:
        """Components lists with more than 50 entries are rejected."""
        config = RepositoryConfig(
            name="valid-name",
            base_url="https://example.com/repo",
            suites=["stable"],
            components=[f"comp{i}" for i in range(count)],
            architectures=["amd64"],
        )
        errors = validate_repository_config(config)
        assert len(errors) > 0, f"Components list of {count} should be rejected"

    @settings(max_examples=200)
    @given(
        count=st.integers(min_value=21, max_value=30),
    )
    def test_architectures_exceeding_max_rejected(self, count: int) -> None:
        """Architectures lists with more than 20 entries are rejected."""
        config = RepositoryConfig(
            name="valid-name",
            base_url="https://example.com/repo",
            suites=["stable"],
            components=["main"],
            architectures=[f"arch{i}" for i in range(count)],
        )
        errors = validate_repository_config(config)
        assert len(errors) > 0, f"Architectures list of {count} should be rejected"

    @settings(max_examples=200)
    @given(data=st.data())
    def test_empty_suites_list_rejected(self, data: st.DataObject) -> None:
        """Empty suites list is always rejected."""
        config = RepositoryConfig(
            name="valid-name",
            base_url="https://example.com/repo",
            suites=[],
            components=["main"],
            architectures=["amd64"],
        )
        errors = validate_repository_config(config)
        assert len(errors) > 0, "Empty suites list should be rejected"

    @settings(max_examples=200)
    @given(data=st.data())
    def test_empty_components_list_rejected(self, data: st.DataObject) -> None:
        """Empty components list is always rejected."""
        config = RepositoryConfig(
            name="valid-name",
            base_url="https://example.com/repo",
            suites=["stable"],
            components=[],
            architectures=["amd64"],
        )
        errors = validate_repository_config(config)
        assert len(errors) > 0, "Empty components list should be rejected"

    @settings(max_examples=200)
    @given(data=st.data())
    def test_empty_architectures_list_rejected(self, data: st.DataObject) -> None:
        """Empty architectures list is always rejected."""
        config = RepositoryConfig(
            name="valid-name",
            base_url="https://example.com/repo",
            suites=["stable"],
            components=["main"],
            architectures=[],
        )
        errors = validate_repository_config(config)
        assert len(errors) > 0, "Empty architectures list should be rejected"

    @settings(max_examples=200)
    @given(
        valid_items=_valid_string_list(1, 19),
        insert_pos=st.integers(min_value=0, max_value=19),
    )
    def test_empty_string_in_suites_rejected(self, valid_items: list[str], insert_pos: int) -> None:
        """An empty string within the suites list is rejected."""
        pos = min(insert_pos, len(valid_items))
        suites = [*valid_items[:pos], "", *valid_items[pos:]]
        config = RepositoryConfig(
            name="valid-name",
            base_url="https://example.com/repo",
            suites=suites,
            components=["main"],
            architectures=["amd64"],
        )
        errors = validate_repository_config(config)
        assert len(errors) > 0, "Empty string in suites should be rejected"

    @settings(max_examples=200)
    @given(
        valid_items=_valid_string_list(1, 49),
        insert_pos=st.integers(min_value=0, max_value=49),
    )
    def test_empty_string_in_components_rejected(self, valid_items: list[str], insert_pos: int) -> None:
        """An empty string within the components list is rejected."""
        pos = min(insert_pos, len(valid_items))
        components = [*valid_items[:pos], "", *valid_items[pos:]]
        config = RepositoryConfig(
            name="valid-name",
            base_url="https://example.com/repo",
            suites=["stable"],
            components=components,
            architectures=["amd64"],
        )
        errors = validate_repository_config(config)
        assert len(errors) > 0, "Empty string in components should be rejected"

    @settings(max_examples=200)
    @given(
        valid_items=_valid_string_list(1, 19),
        insert_pos=st.integers(min_value=0, max_value=19),
    )
    def test_empty_string_in_architectures_rejected(self, valid_items: list[str], insert_pos: int) -> None:
        """An empty string within the architectures list is rejected."""
        pos = min(insert_pos, len(valid_items))
        architectures = [*valid_items[:pos], "", *valid_items[pos:]]
        config = RepositoryConfig(
            name="valid-name",
            base_url="https://example.com/repo",
            suites=["stable"],
            components=["main"],
            architectures=architectures,
        )
        errors = validate_repository_config(config)
        assert len(errors) > 0, "Empty string in architectures should be rejected"

    @settings(max_examples=200)
    @given(timeout=_invalid_timeout_too_low())
    def test_timeout_below_minimum_rejected(self, timeout: int) -> None:
        """Download timeout below 30 seconds is rejected."""
        config = MirrorConfig(repositories=[], download_timeout=timeout)
        errors = validate_mirror_config(config)
        assert len(errors) > 0, f"Timeout {timeout} below 30 should be rejected"

    @settings(max_examples=200)
    @given(timeout=_invalid_timeout_too_high())
    def test_timeout_above_maximum_rejected(self, timeout: int) -> None:
        """Download timeout above 3600 seconds is rejected."""
        config = MirrorConfig(repositories=[], download_timeout=timeout)
        errors = validate_mirror_config(config)
        assert len(errors) > 0, f"Timeout {timeout} above 3600 should be rejected"

    @settings(max_examples=200)
    @given(
        name=_valid_name(),
        timeout=_valid_timeout(),
    )
    def test_duplicate_repository_names_rejected(self, name: str, timeout: int) -> None:
        """Duplicate repository names in a MirrorConfig are rejected."""
        repo = RepositoryConfig(
            name=name,
            base_url="https://example.com/repo",
            suites=["stable"],
            components=["main"],
            architectures=["amd64"],
        )
        config = MirrorConfig(
            repositories=[repo, repo],
            download_timeout=timeout,
        )
        errors = validate_mirror_config(config)
        assert any("duplicate" in e.lower() for e in errors), (
            f"Duplicate name '{name}' should produce a duplicate error"
        )


# ---------------------------------------------------------------------------
# Property 17: Valid configuration is always accepted
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.mirror
class TestProperty17ValidConfigAccepted:
    """Property 17: Valid configuration is always accepted.

    For any MirrorConfig where all names are unique 1-128 character strings,
    all base_urls are valid HTTP/HTTPS URLs, all suites/components/architectures
    are non-empty strings within their count limits, and download_timeout is
    30-3600, the validation function SHALL return zero errors.
    """

    @settings(max_examples=200)
    @given(config=_valid_repository_config())
    def test_valid_repository_config_produces_no_errors(self, config: RepositoryConfig) -> None:
        """Any valid RepositoryConfig passes validation with no errors."""
        errors = validate_repository_config(config)
        assert errors == [], f"Valid config should produce no errors, got: {errors}"

    @settings(max_examples=200)
    @given(
        repos=st.lists(
            _valid_repository_config(),
            min_size=0,
            max_size=5,
        ),
        timeout=_valid_timeout(),
    )
    def test_valid_mirror_config_produces_no_errors(self, repos: list[RepositoryConfig], timeout: int) -> None:
        """Any valid MirrorConfig with unique names passes validation."""
        # Ensure unique names by appending index
        unique_repos = []
        for i, repo in enumerate(repos):
            # Truncate name to leave room for suffix while staying <= 128
            base_name = repo.name[:120]
            unique_name = f"{base_name}_{i}"
            unique_repos.append(
                RepositoryConfig(
                    name=unique_name,
                    base_url=repo.base_url,
                    suites=repo.suites,
                    components=repo.components,
                    architectures=repo.architectures,
                )
            )

        config = MirrorConfig(
            repositories=unique_repos,
            download_timeout=timeout,
        )
        errors = validate_mirror_config(config)
        assert errors == [], f"Valid MirrorConfig should produce no errors, got: {errors}"

    @settings(max_examples=200)
    @given(timeout=_valid_timeout())
    def test_valid_timeout_accepted(self, timeout: int) -> None:
        """Any timeout in [30, 3600] is accepted."""
        config = MirrorConfig(repositories=[], download_timeout=timeout)
        errors = validate_mirror_config(config)
        assert errors == [], f"Timeout {timeout} should be valid, got: {errors}"

    @settings(max_examples=200)
    @given(
        name=_valid_name(),
        url=_valid_url(),
        suites=_valid_string_list(1, 20),
        components=_valid_string_list(1, 50),
        architectures=_valid_string_list(1, 20),
    )
    def test_all_valid_fields_together_accepted(
        self,
        name: str,
        url: str,
        suites: list[str],
        components: list[str],
        architectures: list[str],
    ) -> None:
        """A config with all fields valid individually is accepted as a whole."""
        config = RepositoryConfig(
            name=name,
            base_url=url,
            suites=suites,
            components=components,
            architectures=architectures,
        )
        errors = validate_repository_config(config)
        assert errors == [], f"All-valid config should produce no errors, got: {errors}"
