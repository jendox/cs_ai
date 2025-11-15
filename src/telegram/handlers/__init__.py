from .start import router as start_router
from .stats import router as stats_router
from .tickets import router as tickets_router

__all__ = (
    "routers",
)

routers = [
    start_router,
    stats_router,
    tickets_router,
]
