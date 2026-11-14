"""Check security constraints across every supported lockfile resolution."""

import tomllib
from pathlib import Path

from assertpy import assert_that
from packaging.version import Version


def test_supported_python_lock_has_no_vulnerable_soupsieve() -> None:
    """Every Soup Sieve branch must fix both known parser advisories."""
    lock_path = Path(__file__).resolve().parents[1] / "uv.lock"
    with lock_path.open("rb") as lock_file:
        lock = tomllib.load(lock_file)

    soupsieve_packages = [
        package for package in lock["package"] if package["name"] == "soupsieve"
    ]
    assert_that(soupsieve_packages).is_not_empty()
    for package in soupsieve_packages:
        # GHSA-mmhr-3c5m-f64f affects <4.0.0; GHSA-6655-6qjp-jx7x affects <=2.10.
        assert_that(Version(package["version"]) >= Version("4.0.0")).described_as(
            f"Vulnerable Soup Sieve lock branch: {package}"
        ).is_true()
