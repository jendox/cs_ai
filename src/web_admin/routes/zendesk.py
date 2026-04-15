from fastapi import APIRouter

from src.admin.services import ZendeskAdminService

router = APIRouter(prefix="/zendesk", tags=["zendesk"])


@router.get("/mode")
async def get_zendesk_mode() -> dict[str, str]:
    async with ZendeskAdminService() as service:
        channel = await service.get_mode()

    return {"review_channel": channel.value}
