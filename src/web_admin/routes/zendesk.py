from typing import Annotated

from fastapi import APIRouter, Depends

from src.admin.services import ZendeskAdminService
from src.db.models import AdminUser as AdminUserEntity, UserRole
from src.web_admin.dependencies import require_role

router = APIRouter(prefix="/zendesk", tags=["zendesk"])


@router.get("/mode")
async def get_zendesk_mode(
    user: Annotated[AdminUserEntity, Depends(require_role(UserRole.USER))],
) -> dict[str, str]:
    async with ZendeskAdminService() as service:
        channel = await service.get_mode()

    return {
        "review_channel": channel.value,
        "user": user.username,
        "role": user.role.value,
    }
