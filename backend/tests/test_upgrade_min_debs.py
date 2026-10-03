"""Tests for the runtime Debian min-version upgrade script."""

from __future__ import annotations

import os
import stat
import subprocess  # nosec B404 - test invokes a fixed local script path
from pathlib import Path

import pytest
from assertpy import assert_that

BACKEND_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "upgrade-min-debs.sh"
FRONTEND_SCRIPT = (
    Path(__file__).resolve().parents[2] / "frontend" / "scripts" / "upgrade-min-debs.sh"
)


@pytest.fixture
def bin_dir(tmp_path: Path) -> Path:
    """Return an empty directory used as PATH for mocked Debian tools."""
    path = tmp_path / "bin"
    path.mkdir()
    return path


def _write_executable(
    path: Path,
    contents: str,
) -> None:
    """Write a POSIX script and mark it executable.

    Args:
        path: Destination path for the script.
        contents: Script body, including the shebang.
    """
    path.write_text(contents, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _install_mocks(
    bin_dir: Path,
    *,
    installed_version: str = "10.46-1~deb13u3",
    compare_exit: int = 0,
    apt_fail: bool = False,
) -> Path:
    """Install apt-get, dpkg, and dpkg-query stubs.

    Args:
        bin_dir: Directory prepended to PATH.
        installed_version: Version printed by dpkg-query.
        compare_exit: Exit status of `dpkg --compare-versions`.
        apt_fail: When True, apt-get install exits 1.

    Returns:
        Path of the apt-get argument log.
    """
    apt_log = bin_dir / "apt.log"
    _write_executable(
        path=bin_dir / "apt-get",
        contents=f"""#!/bin/sh
echo "$@" >> "{apt_log}"
if [ "$1" = "install" ] && [ "{int(apt_fail)}" = "1" ]; then
	exit 1
fi
exit 0
""",
    )
    _write_executable(
        path=bin_dir / "dpkg-query",
        contents=f"""#!/bin/sh
printf '%s\\n' "{installed_version}"
""",
    )
    _write_executable(
        path=bin_dir / "dpkg",
        contents=f"""#!/bin/sh
if [ "$1" = "--compare-versions" ]; then
	exit {compare_exit}
fi
exit 1
""",
    )
    return apt_log


def _run_script(
    bin_dir: Path,
    *args: str,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the backend upgrade script with mocked tools on PATH.

    Args:
        bin_dir: Directory of mocked binaries.
        *args: Arguments forwarded to the script.
        extra_env: Extra environment variables.

    Returns:
        Completed process with captured text output.
    """
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
    }
    if extra_env is not None:
        env.update(extra_env)
    return subprocess.run(  # nosec B603 - argv list, script path is a repo constant
        [str(BACKEND_SCRIPT), *args],
        capture_output=True,
        check=False,
        env=env,
        text=True,
    )


def test_frontend_and_backend_scripts_match() -> None:
    """Both Docker contexts ship the same upgrade script."""
    assert_that(BACKEND_SCRIPT.read_text(encoding="utf-8")).is_equal_to(
        FRONTEND_SCRIPT.read_text(encoding="utf-8"),
    )


def test_usage_without_args(
    bin_dir: Path,
) -> None:
    """A missing package spec exits 2 with a usage line."""
    result = _run_script(bin_dir)

    assert_that(result.returncode).is_equal_to(2)
    assert_that(result.stderr).contains("Usage:")


def test_invalid_spec_exits_2(
    bin_dir: Path,
) -> None:
    """A spec without pkg=minver is rejected."""
    result = _run_script(bin_dir, "libpcre2-8-0")

    assert_that(result.returncode).is_equal_to(2)
    assert_that(result.stderr).contains("invalid spec")


def test_upgrade_without_exact_pin_and_cleanup(
    bin_dir: Path,
    tmp_path: Path,
) -> None:
    """apt-get upgrades unpinned packages and clears the apt lists dir."""
    apt_log = _install_mocks(bin_dir=bin_dir)
    lists_dir = tmp_path / "lists"
    lists_dir.mkdir()
    leftover = lists_dir / "security.list"
    leftover.write_text("stale", encoding="utf-8")

    result = _run_script(
        bin_dir,
        "libpcre2-8-0=10.46-1~deb13u3",
        "libssl3t64=3.5.7-1~deb13u3",
        extra_env={"APT_LISTS_DIR": str(lists_dir)},
    )

    assert_that(result.returncode).is_equal_to(0)
    logged = apt_log.read_text(encoding="utf-8")
    assert_that(logged).contains("update")
    assert_that(logged).contains(
        "install -y --no-install-recommends --only-upgrade libpcre2-8-0 libssl3t64",
    )
    assert_that(logged).does_not_contain("=")
    assert_that(leftover.exists()).is_false()


def test_rejects_installed_version_below_minimum(
    bin_dir: Path,
    tmp_path: Path,
) -> None:
    """A package older than the floor fails the build."""
    _install_mocks(
        bin_dir=bin_dir,
        installed_version="10.46-1~deb13u2",
        compare_exit=1,
    )
    lists_dir = tmp_path / "lists"
    lists_dir.mkdir()

    result = _run_script(
        bin_dir,
        "libpcre2-8-0=10.46-1~deb13u3",
        extra_env={"APT_LISTS_DIR": str(lists_dir)},
    )

    assert_that(result.returncode).is_equal_to(1)
    assert_that(result.stderr).contains("older than required")
