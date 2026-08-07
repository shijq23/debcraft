"""Unit tests for domain/mirror/config.py configuration models and validation."""

import pytest

from debcraft.domain.mirror.config import (
    MirrorConfig,
    RepositoryConfig,
    validate_mirror_config,
    validate_repository_config,
)


@pytest.mark.unit
@pytest.mark.mirror
class TestRepositoryConfig:
    """Tests for RepositoryConfig dataclass."""

    def test_construction(self):
        rc = RepositoryConfig(
            name="elxr",
            base_url="https://mirror.elxr.dev/elxr",
            suites=["elxr3"],
            components=["main"],
            architectures=["amd64", "arm64"],
        )
        assert rc.name == "elxr"
        assert rc.base_url == "https://mirror.elxr.dev/elxr"
        assert rc.suites == ["elxr3"]
        assert rc.components == ["main"]
        assert rc.architectures == ["amd64", "arm64"]

    def test_frozen(self):
        rc = RepositoryConfig(name="a", base_url="https://x.com", suites=["s"], components=["c"], architectures=["a"])
        with pytest.raises(AttributeError):
            rc.name = "other"  # type: ignore[misc]


@pytest.mark.unit
@pytest.mark.mirror
class TestMirrorConfig:
    """Tests for MirrorConfig dataclass and defaults."""

    def test_defaults(self):
        mc = MirrorConfig()
        assert mc.repositories == []
        assert mc.download_timeout == 300
        assert mc.max_connections_per_repo == 20
        assert mc.max_total_connections == 60

    def test_frozen(self):
        mc = MirrorConfig()
        with pytest.raises(AttributeError):
            mc.download_timeout = 100  # type: ignore[misc]


@pytest.mark.unit
@pytest.mark.mirror
class TestValidateRepositoryConfig:
    """Tests for validate_repository_config."""

    def _valid_config(self, **overrides) -> RepositoryConfig:
        defaults = {
            "name": "test-repo",
            "base_url": "https://mirror.example.com/debian",
            "suites": ["bookworm"],
            "components": ["main", "contrib"],
            "architectures": ["amd64"],
        }
        defaults.update(overrides)
        return RepositoryConfig(**defaults)

    def test_valid_config_no_errors(self):
        errors = validate_repository_config(self._valid_config())
        assert errors == []

    def test_empty_name_rejected(self):
        errors = validate_repository_config(self._valid_config(name=""))
        assert len(errors) == 1
        assert "name" in errors[0].lower()

    def test_name_at_boundary_128_chars_accepted(self):
        errors = validate_repository_config(self._valid_config(name="x" * 128))
        assert errors == []

    def test_name_exceeds_128_chars_rejected(self):
        errors = validate_repository_config(self._valid_config(name="x" * 129))
        assert any("128" in e for e in errors)

    def test_http_url_accepted(self):
        errors = validate_repository_config(self._valid_config(base_url="http://mirror.example.com/debian"))
        assert errors == []

    def test_https_url_accepted(self):
        errors = validate_repository_config(self._valid_config(base_url="https://mirror.example.com/debian"))
        assert errors == []

    def test_ftp_url_rejected(self):
        errors = validate_repository_config(self._valid_config(base_url="ftp://mirror.example.com/debian"))
        assert any("http" in e.lower() for e in errors)

    def test_empty_url_rejected(self):
        errors = validate_repository_config(self._valid_config(base_url=""))
        assert any("url" in e.lower() or "empty" in e.lower() for e in errors)

    def test_url_without_host_rejected(self):
        errors = validate_repository_config(self._valid_config(base_url="https://"))
        assert any("host" in e.lower() for e in errors)

    def test_suites_empty_list_rejected(self):
        errors = validate_repository_config(self._valid_config(suites=[]))
        assert any("suites" in e for e in errors)

    def test_suites_at_max_20_accepted(self):
        errors = validate_repository_config(self._valid_config(suites=[f"s{i}" for i in range(20)]))
        assert errors == []

    def test_suites_exceeds_20_rejected(self):
        errors = validate_repository_config(self._valid_config(suites=[f"s{i}" for i in range(21)]))
        assert any("suites" in e and "20" in e for e in errors)

    def test_suites_contains_empty_string_rejected(self):
        errors = validate_repository_config(self._valid_config(suites=["bookworm", ""]))
        assert any("empty" in e.lower() for e in errors)

    def test_components_empty_list_rejected(self):
        errors = validate_repository_config(self._valid_config(components=[]))
        assert any("components" in e for e in errors)

    def test_components_at_max_50_accepted(self):
        errors = validate_repository_config(self._valid_config(components=[f"c{i}" for i in range(50)]))
        assert errors == []

    def test_components_exceeds_50_rejected(self):
        errors = validate_repository_config(self._valid_config(components=[f"c{i}" for i in range(51)]))
        assert any("components" in e and "50" in e for e in errors)

    def test_architectures_empty_list_rejected(self):
        errors = validate_repository_config(self._valid_config(architectures=[]))
        assert any("architectures" in e for e in errors)

    def test_architectures_at_max_20_accepted(self):
        errors = validate_repository_config(self._valid_config(architectures=[f"a{i}" for i in range(20)]))
        assert errors == []

    def test_architectures_exceeds_20_rejected(self):
        errors = validate_repository_config(self._valid_config(architectures=[f"a{i}" for i in range(21)]))
        assert any("architectures" in e and "20" in e for e in errors)


@pytest.mark.unit
@pytest.mark.mirror
class TestValidateMirrorConfig:
    """Tests for validate_mirror_config."""

    def _valid_repo(self, name: str = "repo") -> RepositoryConfig:
        return RepositoryConfig(
            name=name,
            base_url="https://mirror.example.com",
            suites=["stable"],
            components=["main"],
            architectures=["amd64"],
        )

    def test_valid_config_no_errors(self):
        mc = MirrorConfig(repositories=[self._valid_repo()])
        assert validate_mirror_config(mc) == []

    def test_timeout_below_minimum_rejected(self):
        mc = MirrorConfig(repositories=[], download_timeout=29)
        errors = validate_mirror_config(mc)
        assert any("30" in e for e in errors)

    def test_timeout_at_minimum_accepted(self):
        mc = MirrorConfig(repositories=[], download_timeout=30)
        errors = validate_mirror_config(mc)
        assert not any("timeout" in e.lower() for e in errors)

    def test_timeout_above_maximum_rejected(self):
        mc = MirrorConfig(repositories=[], download_timeout=3601)
        errors = validate_mirror_config(mc)
        assert any("3600" in e for e in errors)

    def test_timeout_at_maximum_accepted(self):
        mc = MirrorConfig(repositories=[], download_timeout=3600)
        errors = validate_mirror_config(mc)
        assert not any("timeout" in e.lower() for e in errors)

    def test_duplicate_names_rejected(self):
        mc = MirrorConfig(repositories=[self._valid_repo("same"), self._valid_repo("same")])
        errors = validate_mirror_config(mc)
        assert any("duplicate" in e.lower() for e in errors)

    def test_unique_names_accepted(self):
        mc = MirrorConfig(repositories=[self._valid_repo("one"), self._valid_repo("two")])
        errors = validate_mirror_config(mc)
        assert errors == []

    def test_propagates_repository_errors(self):
        bad_repo = RepositoryConfig(name="", base_url="ftp://x", suites=[], components=[], architectures=[])
        mc = MirrorConfig(repositories=[bad_repo])
        errors = validate_mirror_config(mc)
        # Should have errors from the invalid repository config
        assert len(errors) > 0
