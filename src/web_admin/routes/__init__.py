from fastapi import APIRouter

from .auth import router as auth_router
from .health import router as health_router
from .zendesk import router as zendesk_router

router = APIRouter()

router.include_router(auth_router)
router.include_router(health_router)
router.include_router(zendesk_router)
