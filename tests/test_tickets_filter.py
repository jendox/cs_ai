"""Unit tests for rule-based ticket filtering."""

from __future__ import annotations

from src.libs.zendesk_client.models import FromTo, Source, Ticket, Via
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
        brand_id=12345,
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


def test_platform_tagged_ticket_without_sender_is_not_service() -> None:
    """Regression: a customer message delivered through a marketplace API
    (e.g. Amazon Buyer-Seller) that strips the sender email must NOT be
    flagged as service just because of the platform tag. The old
    `_rule_platform_combo.has_no_sender` branch caused this false positive."""
    rules = [
        TicketsFilterRuleDTO(
            kind=TicketsFilterRuleKind.PLATFORM_TAG_HINT,
            value="amazon",
        ),
    ]
    cfg = FilterConfig.from_rules(rules)
    flt = TicketsFilter(cfg)

    ticket = Ticket(
        id=2042,
        brand_id=12345,
        subject="Question about my order",
        description="Hi, is this product still available?",
        tags=["amazon", "amazon_order_item_123"],
        via=Via(
            channel="api",
            source=Source(from_=FromTo(), rel=None, to_=FromTo()),
        ),
    )
    decision = flt.classify_ticket(ticket)
    assert decision.is_service is False, (
        f"Customer marketplace message must fall through to LLM, "
        f"got rule={decision.rule!r} detail={decision.detail!r}"
    )


def test_platform_tagged_ticket_via_api_is_not_auto_service() -> None:
    """Regression: platform tag + via=api alone must not mark a ticket as
    service. Only sender_strict (system domains/addresses) may do that."""
    rules = [
        TicketsFilterRuleDTO(
            kind=TicketsFilterRuleKind.PLATFORM_TAG_HINT,
            value="ebay",
        ),
    ]
    cfg = FilterConfig.from_rules(rules)
    flt = TicketsFilter(cfg)
    ticket = _ticket(
        channel="api",
        tags=["ebay"],
        sender="real-buyer@gmail.com",
        subject="Where is my parcel?",
    )
    decision = flt.classify_ticket(ticket)
    assert decision.is_service is False


def test_platform_system_domain_still_marks_service_via_sender_strict() -> None:
    """sender_strict already catches system domains; platform_combo removal
    does not regress this path."""
    rules = [
        TicketsFilterRuleDTO(
            kind=TicketsFilterRuleKind.SYSTEM_DOMAIN,
            value="amazon.com",
        ),
        TicketsFilterRuleDTO(
            kind=TicketsFilterRuleKind.PLATFORM_TAG_HINT,
            value="amazon",
        ),
    ]
    cfg = FilterConfig.from_rules(rules)
    flt = TicketsFilter(cfg)
    ticket = _ticket(
        sender="no-reply@amazon.com",
        tags=["amazon", "amazon_order_shipped"],
        channel="email",
    )
    decision = flt.classify_ticket(ticket)
    assert decision.is_service is True
    assert decision.rule == "sender_strict"
