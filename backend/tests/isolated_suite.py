"""Copy the PostgreSQL test scaffolding into a ``pytester`` directory.

Guard tests run an inner pytest on a copy of ``tests/conftest.py`` and
``tests/postgres/conftest.py`` (plus the pytest configuration and Alembic
files) so they can add offending test files without touching the real suite.
"""

import shutil
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
_COPIED = (
    "pyproject.toml",
    "tests/__init__.py",
    "tests/conftest.py",
    "tests/postgres/__init__.py",
    "tests/postgres/conftest.py",
)
_LINKED = ("alembic", "alembic.ini")


def build_isolated_suite(pytester: pytest.Pytester, tests: dict[str, str]) -> None:
    """Lay out the scaffolding plus ``tests`` under ``pytester.path``.

    Args:
        pytester: The pytester whose directory receives the copy.
        tests: File names under ``tests/postgres`` mapped to their source.
    """
    for relative in _COPIED:
        target = pytester.path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(BACKEND_DIR / relative, target)
    for relative in _LINKED:
        (pytester.path / relative).symlink_to(BACKEND_DIR / relative)
    for name, source in tests.items():
        (pytester.path / "tests" / "postgres" / name).write_text(source)
