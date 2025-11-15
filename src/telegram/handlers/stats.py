from collections import defaultdict
from datetime import datetime

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.db import session_local
from src.db.models import UserRole
from src.db.repositories import TicketsRepository
from src.libs.zendesk_client.models import Brand
from src.telegram.filters import RoleRequired

router = Router(name=__name__)


def _format_brand(brand_id: int) -> str:
    try:
        brand = Brand(brand_id)
        return brand.name.title()
    except Exception:
        return f"#{brand_id}"


def _short_brand(brand_id: int) -> str:
    try:
        brand = Brand(brand_id)
        return brand.short
    except Exception:
        return "??"


def _format_datetime(dt: datetime | None) -> str:
    if dt is None:
        return "-"
    return dt.strftime("%d-%m-%Y %H:%M")


def _get_limit(text: str) -> int | None:
    try:
        return int(text.rsplit(sep=" ", maxsplit=1)[-1].strip())
    except ValueError:
        return None


@router.message(Command("stats"), RoleRequired(UserRole.USER))
async def cmd_stats(message: Message, role: UserRole):
    async with session_local() as session:
        repo = TicketsRepository(session)
        tickets = await repo.get_observing_tickets(_get_limit(message.text))

    if not tickets:
        return await message.answer("✅ There are no tickets with <code>observing=True</code> yet.")

    total = len(tickets)

    per_brand_status: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for ticket in tickets:
        per_brand_status[ticket.brand_id][ticket.status] += 1

    lines = ["<b>📊 Observing tickets</b>", f"Total: <b>{total}</b>\n", "🧾 By brands & statuses:"]
    for brand_id, status_counts in per_brand_status.items():
        total_brand = sum(status_counts.values())
        brand_name = _format_brand(brand_id)
        parts = [f"{status}: {count}" for status, count in sorted(status_counts.items())]
        lines.append(f"• <b>{brand_name}</b> — {total_brand} ({', '.join(parts)})")

    rows = []
    header = ["ID", "Brand", "Status", "Updated_at", "Last_seen_at"]
    rows.append(header)
    for ticket in tickets:
        rows.append([
            str(ticket.ticket_id),
            _short_brand(ticket.brand_id),
            ticket.status.title(),
            _format_datetime(ticket.updated_at),
            _format_datetime(ticket.last_seen_at),
        ])

    col_widths = [max(len(row[i]) for row in rows) for i in range(len(header))]

    def fmt_row(row: list[str]) -> str:
        return "  ".join(val.ljust(col_widths[i]) for i, val in enumerate(row))

    table_text = "\n".join(fmt_row(r) for r in rows)
    lines.append("")
    lines.append(f"<b>Last {total} tickets with observing=True:</b>")
    lines.append("<pre>")
    lines.append(table_text)
    lines.append("</pre>")

    return await message.answer("\n".join(lines))
