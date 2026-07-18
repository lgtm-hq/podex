"""Top-level router for the ``/api/v2`` surface."""

from fastapi import APIRouter

from podex.api.v2 import auth, episodes, media, podcasts, stats

api_v2_router = APIRouter(tags=["v2"])


def read_status() -> dict[str, str]:
    """Report that the v2 API surface is reachable."""
    return {"status": "ok", "api": "v2"}


api_v2_router.add_api_route("/status", read_status, methods=["GET"])
api_v2_router.include_router(podcasts.router)
api_v2_router.include_router(episodes.router)
api_v2_router.include_router(media.router)
api_v2_router.include_router(stats.router)
api_v2_router.include_router(auth.router)
