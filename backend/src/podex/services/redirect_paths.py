"""Constrain emailed navigation targets using browser-compatible URL parsing."""

from pydantic import HttpUrl, ValidationError

from podex.models.magic_link_token import REDIRECT_PATH_MAX_LENGTH


def site_path(*, target: object, origin: str) -> str:
    """Normalize a same-origin path or use the existing account landing page.

    Args:
        target: Untrusted redirect field from the sign-in request.
        origin: Configured public web URL, never the request's Host header.

    Returns:
        A normalized path, query and fragment safe for browser navigation.
    """
    fallback = "/account"
    if (
        not isinstance(target, str)
        or not target.startswith("/")
        or target.startswith(("//", "/\\"))
        or any(
            ord(character) <= 31 or 127 <= ord(character) <= 159 for character in target
        )
    ):
        return fallback

    try:
        base = HttpUrl(origin)
        # Pydantic's URL parser follows WHATWG normalization, including encoded
        # dot segments and backslashes, unlike urllib's RFC-style path parsing.
        base_origin = f"{base.scheme}://{base.host}:{base.port}"
        resolved = HttpUrl(f"{base_origin}{target}")
    except ValidationError:
        return fallback
    if (resolved.scheme, resolved.host, resolved.port) != (
        base.scheme,
        base.host,
        base.port,
    ):
        return fallback
    path = resolved.path or "/"
    # Normalization must not create an authority when this path is parsed again.
    if path.startswith(("//", "/\\")):
        return fallback
    query = f"?{resolved.query}" if resolved.query else ""
    fragment = f"#{resolved.fragment}" if resolved.fragment else ""
    normalized = f"{path}{query}{fragment}"
    # Percent-encoding can expand a valid request beyond the persistence limit.
    return normalized if len(normalized) <= REDIRECT_PATH_MAX_LENGTH else fallback
