"""Main router for version 2 API endpoints."""

from fastapi import APIRouter

from podex.api.v2.admin import router as admin_router
from podex.api.v2.auth import router as auth_router
from podex.api.v2.ops import router as ops_router
from podex.api.v2.public import router as public_router

api_v2_router = APIRouter()
api_v2_router.include_router(auth_router)
api_v2_router.include_router(public_router)
api_v2_router.include_router(ops_router)
api_v2_router.include_router(admin_router)
