from .llm import router as llm_router
from .start import router as start_router
from .stats import router as stats_router
from .tickets import router as tickets_router
from .users import router as users_router
from .zendesk import router as zendesk_router

__all__ = (
    "routers",
)

routers = [
    start_router,
    stats_router,
    tickets_router,
    users_router,
    llm_router,
    zendesk_router,
]
