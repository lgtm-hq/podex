"""Main API router."""

from fastapi import APIRouter

from podex.api.episodes import router as episodes_router
from podex.api.media import router as media_router
from podex.api.podcasts import router as podcasts_router
from podex.api.search import router as search_router
from podex.api.stats import router as stats_router
from podex.api.status import router as status_router

api_router = APIRouter()

api_router.include_router(podcasts_router)
api_router.include_router(episodes_router)
api_router.include_router(media_router)
api_router.include_router(search_router)
api_router.include_router(stats_router)
api_router.include_router(status_router)
