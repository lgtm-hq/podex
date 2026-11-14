"""Tests for the last-resort free-text redaction used by the reporter."""

from secrets import token_hex
from urllib.parse import quote, quote_plus

import pytest
from assertpy import assert_that
from pydantic import SecretStr
from sentry_sdk.types import Breadcrumb

from podex.config import AuthSettings, DatabaseSettings, Settings
from podex.observability import before_breadcrumb
from podex.redaction import (
    MIN_REDACTED_SECRET_LENGTH,
    SecretRedactor,
    redact_in_place,
    redact_url_userinfo,
    secret_values,
)

_SECRET = f"s+cret/{token_hex(8)}"


def _redactor(*secrets: str) -> SecretRedactor:
    """Return a redactor configured with ``secrets``."""
    redactor = SecretRedactor()
    redactor.configure(secrets)
    return redactor


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("GET https://u:p@h.test/x", "GET https://[redacted]@h.test/x"),
        ("'redis://:pw@r:6379/0'", "'redis://[redacted]@r:6379/0'"),
        ("https://h.test/a@b", "https://h.test/a@b"),
        ("https://h.test?x=a@b", "https://h.test?x=a@b"),
        ("mail bob@example.test", "mail bob@example.test"),
        ('"https://h.test", "a@b"', '"https://h.test", "a@b"'),
    ],
)
def test_url_userinfo_is_redacted_and_nothing_else(text: str, expected: str) -> None:
    """Only the userinfo of a URL authority is replaced."""
    assert_that(redact_url_userinfo(text)).is_equal_to(expected)


def test_known_secret_values_are_redacted_raw_and_url_encoded() -> None:
    """Each configured value is replaced, including its URL-encoded forms."""
    secret = f"s+c ret/{token_hex(8)}"
    redactor = _redactor(secret)
    encoded = quote(secret, safe="")
    form_encoded = quote_plus(secret)

    text = redactor.redact(f"raw={secret} path={encoded} query={form_encoded}")

    assert_that(text).is_equal_to("raw=[redacted] path=[redacted] query=[redacted]")


def test_short_and_empty_secrets_are_not_matched() -> None:
    """Values too short to be credentials never redact unrelated text."""
    short = "x" * (MIN_REDACTED_SECRET_LENGTH - 1)
    redactor = _redactor("", short)

    assert_that(redactor.redact(f"an {short} value")).is_equal_to(f"an {short} value")


def test_overlapping_secrets_are_redacted_whole() -> None:
    """A secret containing another is replaced entirely."""
    inner = f"inner-{token_hex(4)}"
    outer = f"{inner}-outer"
    redactor = _redactor(inner, outer)

    assert_that(redactor.redact(f"a {outer} b {inner}")).is_equal_to(
        "a [redacted] b [redacted]",
    )


def test_redactor_never_renders_its_secrets() -> None:
    """The redactor's representation names no configured value."""
    redactor = _redactor(_SECRET)

    assert_that(repr(redactor)).is_equal_to("SecretRedactor()")
    assert_that(str(redactor)).does_not_contain(_SECRET)


def test_large_text_is_redacted_in_one_pass() -> None:
    """Large and adversarial inputs are handled, each secret replaced."""
    redactor = _redactor(*(f"secret-{index}-{token_hex(4)}" for index in range(50)))
    adversarial = "://" * 50_000 + "a" * 100_000

    assert_that(redactor.redact(adversarial)).is_equal_to(adversarial)
    large = f"{_SECRET} " * 10_000
    assert_that(_redactor(_SECRET).redact(large)).is_equal_to("[redacted] " * 10_000)


def test_secret_values_take_passwords_from_urls() -> None:
    """URL-valued secrets contribute their password, others their whole value."""
    password = f"p%40ss-{token_hex(4)}"
    settings = Settings(
        database=DatabaseSettings(
            url=SecretStr(f"postgresql://podex:{password}@db.invalid/podex"),
        ),
        auth=AuthSettings(smtp_password=SecretStr(_SECRET)),
    )

    values = set(secret_values(settings))

    assert_that(values).contains(password, password.replace("%40", "@"), _SECRET)
    assert_that(values).does_not_contain("postgresql://podex")
    assert_that([value for value in values if "://" in value]).is_empty()


def test_secret_values_ignore_unparsable_urls() -> None:
    """A malformed URL secret contributes nothing instead of failing."""
    settings = Settings(
        database=DatabaseSettings(url=SecretStr("postgresql://[unclosed/podex")),
    )

    assert_that(set(secret_values(settings))).is_not_empty()
    assert_that(set(secret_values(settings))).does_not_contain(
        "postgresql://[unclosed/podex",
    )


def test_redact_in_place_reaches_nested_strings() -> None:
    """Every nested string is redacted; keys and other values are kept."""
    data: dict[str, object] = {
        "message": "https://u:p@h.test",
        "values": [{"value": "x https://u:p@h.test"}, 3, None],
    }

    redact_in_place(data, redact_url_userinfo)

    assert_that(data).is_equal_to(
        {
            "message": "https://[redacted]@h.test",
            "values": [{"value": "x https://[redacted]@h.test"}, 3, None],
        },
    )


def test_breadcrumb_headers_keep_only_the_allowlist() -> None:
    """Breadcrumb header data is reduced like request headers."""
    crumb: Breadcrumb = {
        "type": "http",
        "message": "GET https://u:p@h.test/x",
        "data": {
            "url": "https://h.test/x",
            "headers": {"User-Agent": "agent/1", "Authorization": "Bearer tok"},
            "Headers": "Authorization: Bearer tok",
        },
    }

    sent = before_breadcrumb(crumb, {})

    assert_that(sent["data"]).is_equal_to(
        {
            "url": "https://h.test/x",
            "headers": {"User-Agent": "agent/1"},
            "Headers": {},
        },
    )
    assert_that(sent["message"]).is_equal_to("GET https://[redacted]@h.test/x")
