"""Top-level router for the ``/api/v2`` surface."""

from fastapi import APIRouter

api_v2_router = APIRouter(tags=["v2"])


@api_v2_router.get("/status")
def read_status() -> dict[str, str]:
    """Report that the v2 API surface is reachable."""
    return {"status": "ok", "api": "v2"}
