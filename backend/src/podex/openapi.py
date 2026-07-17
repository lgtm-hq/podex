"""Emit the FastAPI OpenAPI schema as a JSON artifact.

The generated schema is the single source of truth for the frontend's typed
API client (``frontend/src/lib/types.gen.ts``). Run this module to refresh the
committed ``frontend/openapi.json`` after changing any API route or schema::

    uv run python -m podex.openapi ../frontend/openapi.json

With no argument the schema is written to stdout.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from podex.main import app


def build_openapi_schema() -> dict[str, Any]:
    """Return the application's OpenAPI schema as a plain dict."""
    return app.openapi()


def render_openapi_json() -> str:
    """Render the OpenAPI schema as deterministic, newline-terminated JSON."""
    schema = build_openapi_schema()
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Write the OpenAPI schema to the given path (or stdout)."""
    args = sys.argv[1:] if argv is None else argv
    payload = render_openapi_json()
    if args:
        Path(args[0]).write_text(payload, encoding="utf-8")
    else:
        sys.stdout.write(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
