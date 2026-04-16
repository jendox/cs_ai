"""Unit tests for rule-based ticket filtering."""

from __future__ import annotations

from src.libs.zendesk_client.models import Brand, FromTo, Source, Ticket, Via
from src.tickets_filter.config import FilterConfig, TicketsFilterRuleKind
from src.tickets_filter.dto import TicketsFilterRuleDTO
from src.tickets_filter.filter import TicketsFilter


def _ticket(
    *,
    subject: str = "Need help",
    description: str = "Hello",
    sender: str = "buyer@example.com",
    channel: str = "email",
    tags: list[str] | None = None,
) -> Ticket:
    return Ticket(
        id=1001,
        brand=Brand.SUPERSELF,
        subject=subject,
        description=description,
        tags=tags or [],
        via=Via(
            channel=channel,
            source=Source(
                from_=FromTo(address=sender, name="Buyer"),
                rel=None,
                to_=FromTo(),
            ),
        ),
    )


def test_system_domain_marks_service() -> None:
    rules = [
        TicketsFilterRuleDTO(
            kind=TicketsFilterRuleKind.SYSTEM_DOMAIN,
            value="notifications.example",
        ),
    ]
    cfg = FilterConfig.from_rules(rules)
    flt = TicketsFilter(cfg)
    ticket = _ticket(sender="no-reply@notifications.example")
    decision = flt.classify_ticket(ticket)
    assert decision.is_service is True
    assert decision.rule == "sender_strict"


def test_api_allowed_pattern_overrides_service_signals() -> None:
    rules = [
        TicketsFilterRuleDTO(
            kind=TicketsFilterRuleKind.SYSTEM_DOMAIN,
            value="amazon.com",
        ),
        TicketsFilterRuleDTO(
            kind=TicketsFilterRuleKind.API_ALLOWED_PATTERN,
            value=r"^new customer message on",
            is_regex=True,
            via_channel="api",
        ),
    ]
    cfg = FilterConfig.from_rules(rules)
    flt = TicketsFilter(cfg)
    ticket = _ticket(
        sender="store-news@amazon.com",
        channel="api",
        subject="New customer message on order123",
    )
    decision = flt.classify_ticket(ticket)
    assert decision.is_service is False
    assert decision.rule == "api_exception"


def test_inactive_rules_are_ignored() -> None:
    rules = [
        TicketsFilterRuleDTO(
            kind=TicketsFilterRuleKind.SYSTEM_DOMAIN,
            value="evil.test",
            is_active=False,
        ),
    ]
    cfg = FilterConfig.from_rules(rules)
    flt = TicketsFilter(cfg)
    ticket = _ticket(sender="user@evil.test")
    decision = flt.classify_ticket(ticket)
    assert decision.is_service is False
    assert decision.rule is None
