from fastapi import APIRouter

from app.modules.actuals.router import router as actuals_router
from app.modules.auth.router import router as auth_router
from app.modules.dashboards.router import router as dashboards_router
from app.modules.documents.router import router as documents_router
from app.modules.equipment.router import router as equipment_router
from app.modules.equipment.shift_router import router as shift_router
from app.modules.notifications.router import router as notifications_router
from app.modules.photos.router import router as photos_router
from app.modules.projects.router import router as projects_router
from app.modules.readiness.router import router as readiness_router
from app.modules.schedule.router import router as schedule_router
from app.modules.settings.router import router as settings_router
from app.modules.works.router import router as works_router

API_V1_PREFIX = "/api/v1"

api_v1_router = APIRouter(prefix=API_V1_PREFIX)
api_v1_router.include_router(auth_router)
api_v1_router.include_router(projects_router)
api_v1_router.include_router(schedule_router)
api_v1_router.include_router(actuals_router)
api_v1_router.include_router(photos_router)
api_v1_router.include_router(readiness_router)
api_v1_router.include_router(dashboards_router)
api_v1_router.include_router(settings_router)
api_v1_router.include_router(works_router)
api_v1_router.include_router(equipment_router)
api_v1_router.include_router(shift_router)
api_v1_router.include_router(documents_router)
api_v1_router.include_router(notifications_router)
