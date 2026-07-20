"""Top-level router for the ``/api/v2`` surface."""

from fastapi import APIRouter

from podex.api.deps import AppSettings
from podex.api.v2 import auth, episodes, media, ops, podcasts, stats, takedowns
from podex.api.v2.schemas import ApiStatusRead

api_v2_router = APIRouter(tags=["v2"])


def read_status(settings: AppSettings) -> ApiStatusRead:
    """Report that the v2 API surface is reachable and how sign-in works."""
    return ApiStatusRead(workos_enabled=settings.workos_enabled)


api_v2_router.add_api_route(
    "/status",
    read_status,
    methods=["GET"],
    response_model=ApiStatusRead,
)
api_v2_router.include_router(podcasts.router)
api_v2_router.include_router(episodes.router)
api_v2_router.include_router(media.router)
api_v2_router.include_router(stats.router)
api_v2_router.include_router(auth.router)
api_v2_router.include_router(ops.router)
api_v2_router.include_router(takedowns.router)
