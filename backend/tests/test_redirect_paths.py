"""Browser-compatible validation of emailed sign-in destinations."""

import pytest
from assertpy import assert_that

from podex.services.redirect_paths import site_path


@pytest.mark.parametrize(
    "target",
    [
        None,
        42,
        "",
        "account",
        "//other.example",
        "/\\other.example",
        "https://other.example",
        "javascript:alert(1)",
        " /account",
        "\t/account",
        "/\t/other.example",
        "/\r/other.example",
        "/\n/other.example",
        "/a/..//other.example",
        "/%2e%2e//other.example",
    ],
)
def test_site_path_rejects_unsafe_targets(target: object) -> None:
    """Unsafe and non-path targets use the existing account landing page."""
    assert_that(site_path(target=target, origin="https://podex.example")).is_equal_to(
        "/account",
    )


@pytest.mark.parametrize("code", [*range(32), *range(127, 160)])
def test_site_path_rejects_controls(code: int) -> None:
    """Reject control characters before a URL parser can strip them."""
    assert_that(
        site_path(target=f"/account?next={chr(code)}", origin="https://podex.example"),
    ).is_equal_to("/account")


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("/", "/"),
        ("/account/settings?tab=alerts#top", "/account/settings?tab=alerts#top"),
        ("/a/../account?tab=alerts#top", "/account?tab=alerts#top"),
        ("/account\\settings", "/account/settings"),
        ("/account?q=hello world", "/account?q=hello%20world"),
        ("/%2F%2Fother.example", "/%2F%2Fother.example"),
        ("/%5Cother.example", "/%5Cother.example"),
        ("/%09/other.example", "/%09/other.example"),
        ("/account?q=" + "a " * 100 + "z", "/account"),
        ("/" + "é" * 100, "/account"),
        ("/" + "a" * 299, "/" + "a" * 299),
    ],
)
def test_site_path_normalizes_paths(target: str, expected: str) -> None:
    """Preserve query, fragment and encoded separators on same-origin paths."""
    assert_that(
        site_path(target=target, origin="https://podex.example:8443/base"),
    ).is_equal_to(expected)


def test_site_path_rejects_invalid_origin() -> None:
    """Invalid deployment configuration cannot produce an unsafe redirect."""
    assert_that(site_path(target="/account/saved", origin="invalid")).is_equal_to(
        "/account",
    )
