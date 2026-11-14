"""Tests for the last-resort free-text redaction used by the reporter."""

import logging
import re
from secrets import token_hex
from urllib.parse import quote, quote_plus

import pytest
from assertpy import assert_that
from pydantic import SecretStr
from sentry_sdk.types import Breadcrumb

from podex.config import (
    AuthSettings,
    DatabaseSettings,
    ObservabilitySettings,
    RateLimitSettings,
    Settings,
)
from podex.observability import before_breadcrumb, init_sentry
from podex.redaction import (
    SHORT_VALUE_LENGTH,
    SecretRedactor,
    redact_in_place,
    redact_url_userinfo,
    secret_values,
    undersized_field_paths,
)
from tests.conftest import SentryEnvelopeRecorder

_SECRET = f"s+cret/{token_hex(8)}"


def _lower_escapes(text: str) -> str:
    """Return ``text`` with its percent-escapes in lowercase hex."""
    return re.sub(r"%[0-9A-F]{2}", lambda match: match.group(0).lower(), text)


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
        (
            "fetch https://reader:pa'ss@host.invalid/path failed",
            "fetch https://[redacted]@host.invalid/path failed",
        ),
        ("'https://h.test','a@b'", "'https://h.test','a@b'"),
        (
            "for url 'https://reader:pa'ss@host.invalid/rss'",
            "for url 'https://[redacted]@host.invalid/rss'",
        ),
        ('"https://u:p\'w@h.test"', '"https://[redacted]@h.test"'),
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


def test_short_secrets_are_redacted_and_empty_ones_ignored() -> None:
    """Every non-empty secret is redacted, however short; empty means unset."""
    short = "s3cr3t"
    redactor = _redactor("", short)

    assert_that(len(short)).is_less_than(SHORT_VALUE_LENGTH)
    assert_that(redactor.redact(f"key {short} rejected")).is_equal_to(
        "key [redacted] rejected",
    )
    assert_that(_redactor("").redact("nothing to hide")).is_equal_to(
        "nothing to hide",
    )


def test_lowercase_percent_escapes_are_redacted() -> None:
    """Percent-encoded forms match with upper- and lowercase escapes."""
    secret = f"p@ss/w {token_hex(4)}"
    upper = quote(secret, safe="")
    lower = _lower_escapes(upper)
    lower_plus = _lower_escapes(quote_plus(secret))

    text = _redactor(secret).redact(f"a={upper} b={lower} c={lower_plus}")

    assert_that(lower).is_not_equal_to(upper)
    assert_that(text).is_equal_to("a=[redacted] b=[redacted] c=[redacted]")


def test_mixed_case_escapes_match_but_literals_keep_their_case() -> None:
    """Each escape's hex digits match in either case; letters do not."""
    secret = f"P+foo/bar{token_hex(3)}"
    mixed = quote(secret, safe="").replace("%2F", "%2f")
    redactor = _redactor(secret)

    assert_that(mixed).contains("%2B")
    assert_that(mixed).contains("%2f")
    assert_that(redactor.redact(f"key={mixed}")).is_equal_to("key=[redacted]")
    assert_that(redactor.redact(f"key={mixed.lower()}")).is_equal_to(
        f"key={mixed.lower()}",
    )


def test_quoted_neighbour_is_still_searched_for_secrets() -> None:
    """Text skipped as userinfo is still searched for configured values."""
    secret = f"tok{token_hex(4)}"

    text = _redactor(secret).redact(f"'https://h.test','{secret}@b'")

    assert_that(text).is_equal_to("'https://h.test','[redacted]@b'")


def test_secret_without_utf8_encoding_is_redacted_raw() -> None:
    """A lone surrogate skips the encoded forms; configuring never raises."""
    secret = f"pw\udcff{token_hex(4)}"

    redactor = _redactor(secret)

    assert_that(redactor.redact(f"login {secret} failed")).is_equal_to(
        "login [redacted] failed",
    )


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


def test_url_settings_contribute_their_password() -> None:
    """The database and Redis URL settings contribute only the password."""
    password = f"p%40ss-{token_hex(4)}"
    settings = Settings(
        database=DatabaseSettings(
            url=SecretStr(f"postgresql://podex:{password}@db.invalid/podex"),
        ),
    )

    values = set(secret_values(settings))

    assert_that(password in values).is_true()
    assert_that(password.replace("%40", "@") in values).is_true()
    assert_that([value for value in values if "://" in value]).is_empty()


def test_other_secrets_are_matched_whole_even_when_url_shaped() -> None:
    """URL handling is decided by field, so a URL-like password stays whole."""
    smtp_password = f"pass://{token_hex(6)}"
    settings = Settings(auth=AuthSettings(smtp_password=SecretStr(smtp_password)))

    values = set(secret_values(settings))

    assert_that(values).contains(smtp_password)
    assert_that(_redactor(*values).redact(f"login {smtp_password} failed")).is_equal_to(
        "login [redacted] failed",
    )


@pytest.mark.parametrize(
    ("field", "template"),
    [
        ("redis", "redis://redis.invalid:6379/0?password={value}"),
        ("redis", "rediss://redis.invalid/0?ssl_password={value}&ssl=true"),
        ("database", "postgresql+psycopg://podex@db.invalid/podex?password={value}"),
        ("database", "postgresql://podex@db.invalid/podex?sslpassword={value}"),
    ],
)
def test_url_settings_contribute_credential_query_options(
    field: str,
    template: str,
) -> None:
    """Credential options in a URL's query are matched like its password."""
    value = f"opt{token_hex(5)}"
    url = SecretStr(template.format(value=value))
    settings = (
        Settings(rate_limit=RateLimitSettings(redis_url=url))
        if field == "redis"
        else Settings(database=DatabaseSettings(url=url))
    )

    assert_that(value in set(secret_values(settings))).is_true()


def test_unparsable_url_settings_are_redacted_whole() -> None:
    """A URL setting that cannot be parsed contributes its whole value."""
    url = "postgresql://[unclosed/podex"
    settings = Settings(database=DatabaseSettings(url=SecretStr(url)))

    assert_that(set(secret_values(settings))).contains(url)


def test_undersized_field_paths_name_fields_only() -> None:
    """Short configured secrets are listed by path, once each; empty ones not."""
    settings = Settings(
        database=DatabaseSettings(
            url=SecretStr(f"postgresql://podex:{token_hex(2)}@db.invalid/podex"),
        ),
        auth=AuthSettings(
            smtp_password=SecretStr("short"), workos_api_key=SecretStr("")
        ),
        ops_api_key=SecretStr(_SECRET),
    )

    assert_that(undersized_field_paths(settings)).is_equal_to(
        ["database.url", "auth.smtp_password"],
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
    assert_that(sent["type"]).is_equal_to("http")


def test_nested_breadcrumb_headers_keep_only_the_allowlist() -> None:
    """Header mappings at any depth of breadcrumb data are reduced."""
    crumb: Breadcrumb = {
        "data": {
            "request": {
                "headers": {"Authorization": "Bearer tok", "Content-Type": "x"},
            },
            "attempts": [{"HEADERS": {"Cookie": "c=1"}}],
        },
    }

    sent = before_breadcrumb(crumb, {})

    assert_that(sent["data"]).is_equal_to(
        {
            "request": {"headers": {"Content-Type": "x"}},
            "attempts": [{"HEADERS": {}}],
        },
    )


def test_startup_warns_once_per_short_secret_by_path_only(
    sentry_transport: SentryEnvelopeRecorder,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Each short secret is named by path, never by value or exact length."""
    del sentry_transport  # requested so init_sentry uses the in-memory transport
    short = f"k{token_hex(2)}"
    settings = Settings(
        auth=AuthSettings(workos_api_key=SecretStr(short)),
        ops_api_key=SecretStr(_SECRET),
        observability=ObservabilitySettings(
            sentry_dsn="https://public@sentry.invalid/1",
        ),
    )

    with caplog.at_level(logging.WARNING, logger="podex.observability"):
        init_sentry(settings)

    warnings = [
        record.getMessage()
        for record in caplog.records
        if record.name == "podex.observability"
    ]
    assert_that(warnings).is_equal_to(
        [
            "setting auth.workos_api_key is shorter than 8 characters; "
            "error reports redact every occurrence of its value",
        ],
    )
    assert_that(caplog.text).does_not_contain(short)
    assert_that(caplog.text).does_not_contain(_SECRET)
