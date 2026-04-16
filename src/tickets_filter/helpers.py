from collections.abc import Iterable

from src.libs.zendesk_client.models import Ticket


def normalize_email(value: str | None = None) -> str:
    if value is None:
        return ""
    return value.strip().lower()


def email_domain(email: str) -> str:
    return email.split("@", 1)[-1] if "@" in email else ""


def has_any_prefix(tags: Iterable[str], prefixes: Iterable[str]) -> bool:
    for tag in tags:
        for prefix in prefixes:
            if tag.startswith(prefix):
                return True
    return False


def get_subject(ticket: Ticket) -> str:
    return (ticket.subject or ticket.raw_subject or "").strip()


def get_tags(ticket: Ticket) -> list[str]:
    return list(ticket.tags or [])


def get_via_channel(ticket: Ticket) -> str:
    if ticket.via and ticket.via.channel:
        return (ticket.via.channel or "").strip().lower()
    return ""


def get_sender_email(ticket: Ticket) -> str:
    if ticket.via and ticket.via.source and ticket.via.source.from_:
        return normalize_email(ticket.via.source.from_.address or "")
    return ""


def get_sender_name(ticket: Ticket) -> str:
    if ticket.via and ticket.via.source and ticket.via.source.from_:
        return (ticket.via.source.from_.name or "").strip()
    return ""


def make_log_record(
    ticket: Ticket,
    rule: str | None = None,
    reason: str | None = None,
) -> dict:
    return {
        "ticket_id": ticket.id,
        "brand_id": ticket.brand_id,
        "filter_rule": rule,
        "filter_reason": reason,
        "filter_channel": get_via_channel(ticket),
        "filter_subject": get_subject(ticket),
        "filter_sender": get_sender_email(ticket),
    }
