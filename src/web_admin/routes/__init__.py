from fastapi import APIRouter

from .auth import router as auth_router
from .filter_rules import router as filter_rules_router
from .health import router as health_router
from .llm import router as llm_router
from .playground import router as playground_router
from .prompts import router as prompts_router
from .replies import router as replies_router
from .tickets import router as tickets_router
from .users import router as users_router
from .zendesk import router as zendesk_router

router = APIRouter()

router.include_router(auth_router)
router.include_router(health_router)
router.include_router(zendesk_router)
router.include_router(tickets_router)
router.include_router(replies_router)
router.include_router(playground_router)
router.include_router(filter_rules_router)
router.include_router(llm_router)
router.include_router(prompts_router)
router.include_router(users_router)
