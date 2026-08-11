"""Download location URL construction.

Pure function for constructing fully-qualified download URLs from
repository base URLs and package filename fields.
"""

from __future__ import annotations


def resolve_download_location(base_url: str | None, filename: str | None) -> str:
    """Construct download URL or return NOASSERTION.

    Joins base_url and filename with exactly one ``/`` separator.
    Returns ``NOASSERTION`` when either input is missing, empty, or
    whitespace-only.

    Args:
        base_url: Repository base URL (e.g. ``https://deb.debian.org/debian``).
        filename: Relative package filename
            (e.g. ``pool/main/g/glibc/libc6_2.40_amd64.deb``).

    Returns:
        The fully-qualified download URL, or the string ``NOASSERTION``
        if inputs are insufficient to construct a valid URL.
    """
    if base_url is None or not base_url.strip():
        return "NOASSERTION"

    if filename is None or not filename.strip():
        return "NOASSERTION"

    # Strip trailing slashes from base_url
    normalized_base = base_url.rstrip("/")

    # Strip leading slashes from filename
    normalized_filename = filename.lstrip("/")

    return f"{normalized_base}/{normalized_filename}"
