"""Keep OSV suppressions time-boxed, explained and tied to live advisories."""

import datetime as dt
import tomllib
from pathlib import Path

from assertpy import assert_that

OSV_CONFIG = Path(__file__).resolve().parents[2] / ".osv-scanner.toml"

# Advisories withdrawn upstream. OSV stops reporting them, so an entry for
# one masks nothing and only hides that the file needs maintenance.
WITHDRAWN_ADVISORIES = frozenset({"GHSA-ch52-4w7c-c8xp"})


def test_no_expired_or_withdrawn_suppressions() -> None:
    """Every ignore entry has a reason, an unexpired date and a live advisory."""
    config = tomllib.loads(OSV_CONFIG.read_text(encoding="utf-8"))
    today = dt.datetime.now(tz=dt.UTC).date()

    for entry in config.get("IgnoredVulns", []):
        assert_that(entry).contains_key("id", "ignoreUntil", "reason")
        assert_that(WITHDRAWN_ADVISORIES).does_not_contain(entry["id"])
        assert_that(entry["ignoreUntil"]).is_type_of(dt.date)
        assert_that(entry["ignoreUntil"]).is_greater_than_or_equal_to(today)
        assert_that(entry["reason"].strip()).is_not_empty()
