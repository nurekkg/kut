from fastapi import APIRouter

from app.modules.auth.router import router as auth_router
from app.modules.projects.router import router as projects_router

API_V1_PREFIX = "/api/v1"

api_v1_router = APIRouter(prefix=API_V1_PREFIX)
api_v1_router.include_router(auth_router)
api_v1_router.include_router(projects_router)
