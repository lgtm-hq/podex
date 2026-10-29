#!/usr/bin/env python3
"""Give the standalone CI Pylint interpreter the locked project dependencies."""

import importlib.util
import os
import shutil
import site
import subprocess  # nosec B404 - fixed dependency setup command
import sys
from pathlib import Path


def configure_project_imports() -> None:
    """Resolve project imports before Pylint infers framework and SDK types.

    The quality image runs Pylint with its standalone Python interpreter. Its
    environment does not contain Podex's dependencies. Local uv runs already
    provide them, so only the bare image needs the frozen dependency sync.
    """
    project = Path(__file__).resolve().parents[3]
    site_packages = (
        project
        / ".venv"
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )
    if site_packages.is_dir():
        site.addsitedir(str(site_packages))
    required = ("sqlalchemy", "fastapi", "assertpy", "anthropic")
    project_python = project / ".venv" / "bin" / "python"
    project_pylint = project / ".venv" / "bin" / "pylint"
    external_interpreter = Path(sys.prefix).resolve() != (project / ".venv").resolve()
    if (
        external_interpreter
        or not project_pylint.is_file()
        or any(importlib.util.find_spec(module) is None for module in required)
    ):
        uv_executable = shutil.which("uv")
        if uv_executable is None:
            raise RuntimeError("uv is required to prepare Pylint's project imports")
        subprocess.run(  # nosec B603 - fixed argv and frozen local project
            [
                uv_executable,
                "sync",
                "--frozen",
                "--no-install-project",
                "--python",
                sys.executable,
            ],
            cwd=project,
            check=True,
            timeout=300,
        )
        site.addsitedir(str(site_packages))
    # Framework inference depends on the pinned Pylint/Astroid pair as well as
    # the imports. Re-exec once into the locked interpreter when the CI image
    # launched an older standalone Pylint. No diagnostic is disabled.
    if external_interpreter:
        os.execv(  # nosec B606 - fixed project interpreter; original lint argv
            str(project_python),
            [str(project_python), "-m", "pylint", *sys.argv[1:]],
        )
    sys.path.insert(0, str(project / "src"))


if __name__ == "__main__":
    configure_project_imports()
