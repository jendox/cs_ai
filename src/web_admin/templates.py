from pathlib import Path

from fastapi.templating import Jinja2Templates

from src.libs.zendesk_client.models import Brand

WEB_ADMIN_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(WEB_ADMIN_DIR / "templates"))


def brand_label(value: int | str | None) -> str:
    if value is None:
        return "-"
    try:
        brand = Brand(int(value))
    except (TypeError, ValueError):
        return str(value)
    return brand.short


templates.env.filters["brand_label"] = brand_label
