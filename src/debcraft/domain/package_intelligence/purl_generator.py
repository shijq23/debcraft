"""Package URL (PURL) generation for Debian binary packages.

Pure function for generating PURL strings in the ``pkg:deb`` scheme
conforming to the PURL specification.
"""

from __future__ import annotations

from debcraft.domain.package_intelligence.errors import PURLGenerationError

# Characters that must be percent-encoded in PURL name/version components.
_PURL_ENCODE_MAP: dict[str, str] = {
    ":": "%3A",
    "+": "%2B",
    "@": "%40",
    "?": "%3F",
    "#": "%23",
}


def _percent_encode(value: str) -> str:
    """Percent-encode special PURL characters in a component value.

    Encodes ``:`` → ``%3A``, ``+`` → ``%2B``, ``@`` → ``%40``,
    ``?`` → ``%3F``, ``#`` → ``%23``.

    Args:
        value: The raw component string to encode.

    Returns:
        The percent-encoded string.
    """
    result = value
    for char, encoded in _PURL_ENCODE_MAP.items():
        result = result.replace(char, encoded)
    return result


def generate_purl(
    package_name: str,
    version: str,
    architecture: str,
    distro: str | None = None,
) -> str:
    """Generate a pkg:deb PURL string.

    Produces a Package URL in the format::

        pkg:deb/<distro>/<package_name>@<version>?arch=<architecture>

    Special characters in ``package_name`` and ``version`` are
    percent-encoded per the PURL specification.

    Args:
        package_name: The Debian binary package name.
        version: The package version string (may include epoch like ``1:2.0``).
        architecture: The package architecture (e.g. ``amd64``, ``all``).
        distro: The distribution namespace (e.g. ``debian``, ``ubuntu``).
            Defaults to ``"debian"`` when ``None`` or empty.

    Returns:
        A valid PURL string in the ``pkg:deb`` scheme.

    Raises:
        PURLGenerationError: If any required field (package_name, version,
            or architecture) is ``None``, empty, or whitespace-only.
    """
    if package_name is None or not package_name.strip():
        raise PURLGenerationError("package_name")

    if version is None or not version.strip():
        raise PURLGenerationError("version")

    if architecture is None or not architecture.strip():
        raise PURLGenerationError("architecture")

    # Default distro to "debian" when unspecified or empty
    namespace = "debian" if distro is None or not distro.strip() else distro.strip().lower()

    encoded_name = _percent_encode(package_name)
    encoded_version = _percent_encode(version)

    return f"pkg:deb/{namespace}/{encoded_name}@{encoded_version}?arch={architecture}"
