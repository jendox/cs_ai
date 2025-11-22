"""
High-level ticket classification filter.

Determines whether a ticket is a **service ticket** (AI reply should be skipped)
or a **user ticket** (AI reply may be generated).

The filter is driven by rule sets stored in the database and evaluates:
- sender email/domain,
- subject patterns,
- ticket tags,
- delivery channel,
- API-allowed user message patterns,
- spam/marketing subject patterns.

Public API:
- RuleOutcome        — tri-state outcome for a rule.
- RuleResult         — outcome of a single rule evaluation.
- ServiceDecision    — final classification for a ticket.
- TicketsFilter      — main classifier.
"""
import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto

from src.libs.zendesk_client.models import Ticket
from src.tickets_filter import helpers
from src.tickets_filter.config import FilterConfig

__all__ = (
    "TicketsFilter",
)


class RuleOutcome(Enum):
    """Three-state outcome produced by a single rule."""

    ABSTAIN = auto()
    """Rule is not applicable to this ticket."""
    SERVICE = auto()
    """Ticket is recognized as service-level (should be skipped by AI)."""
    USER = auto()
    """Ticket is recognized as user-level (AI response allowed, veto)."""


@dataclass(frozen=True)
class RuleResult:
    """Result of a single rule evaluation.  """

    outcome: RuleOutcome
    """Overall classification from the rule."""
    reason: str | None = None
    """Optional detail (pattern, tag, domain, etc.) that led to this outcome."""


@dataclass(frozen=True)
class ServiceDecision:
    """
    Final classification result for a ticket.

    Attributes:
        is_service:  True = service ticket (AI reply skipped),
                     False = user ticket (AI reply allowed).
        rule:        Name of the rule that produced the final outcome, if any.
        detail:      Optional diagnostic detail (pattern/tag/domain/etc.).
    """

    is_service: bool
    """True = service ticket (AI reply skipped), False = user ticket (AI reply allowed)."""
    rule: str | None = None
    """Name of the rule that produced the final outcome, if any."""
    detail: str | None = None
    """Optional diagnostic information describing the matched rule."""


class TicketsFilter:
    """
    High-level ticket filter.

    Usage:
        config = FilterConfig.from_rules(rules)
        filter_ = TicketsFilter(config)

        decision = filter_.classify_ticket(ticket)
        is_service = filter_.is_service_ticket(ticket)
    """

    def __init__(
        self,
        config: FilterConfig,
    ) -> None:
        self.config = config
        self.logger = logging.getLogger("tickets_filter")

    def _rule_sender_strict(self, ticket: Ticket) -> RuleResult:
        """
        Identify system-generated tickets based on sender information.

        A ticket is considered service-level if:
         the sender address matches a known system address,
         the domain is in the system domain list,
         the email contains an address hint (no-reply, postmaster, daemon, robot, etc.),
         the sender name indicates automated origin (contains 'do not reply' or 'notification').
        """

        email = helpers.get_sender_email(ticket)
        if not email:
            return RuleResult(RuleOutcome.ABSTAIN)
        if email in self.config.system_addresses:
            return RuleResult(RuleOutcome.SERVICE, f"sender_strict.system_address: {email}")
        domain = helpers.email_domain(email)
        if domain in self.config.system_domains:
            return RuleResult(RuleOutcome.SERVICE, f"sender_strict.system_domain: {domain}")
        if any(hint in email.lower() for hint in self.config.address_hints):
            return RuleResult(RuleOutcome.SERVICE, "sender_strict.address_hint")

        name = helpers.get_sender_name(ticket)
        if name:
            name_lower = name.lower()
            if "do not reply" in name_lower or "notification" in name_lower:
                return RuleResult(RuleOutcome.SERVICE, f"sender_strict.sender_name: {name}")
        return RuleResult(RuleOutcome.ABSTAIN)

    def _rule_subject(self, ticket: Ticket) -> RuleResult:
        """
        Match known service/system subject patterns.

        Covers auto-replies, notifications, delivery errors, verification codes,
        bank statements, surveys, and other automated messages.
        """

        subject = helpers.get_subject(ticket).lower()
        if not subject:
            return RuleResult(RuleOutcome.ABSTAIN)
        for pattern in self.config.subject_patterns:
            if pattern.search(subject):
                return RuleResult(RuleOutcome.SERVICE, reason=f"rule_subject.pattern: {pattern.pattern}")
        return RuleResult(RuleOutcome.ABSTAIN)

    def _rule_tags_service(self, ticket: Ticket) -> RuleResult:
        """
        Mark a ticket as service-level based on its tags.

        Triggers when:
        - a tag matches one of the exact service tags, or
        - a tag starts with a known service prefix (e.g., 'forward_to_').
        """

        tags = helpers.get_tags(ticket)
        low_tags = {tag.lower() for tag in tags}
        if any(tag in low_tags for tag in self.config.service_tags_exact):
            return RuleResult(RuleOutcome.SERVICE, "tags_service.tag")
        if helpers.has_any_prefix(low_tags, self.config.service_tags_prefixes):
            return RuleResult(RuleOutcome.SERVICE, "tags_service.tag_prefix")
        return RuleResult(RuleOutcome.ABSTAIN)

    def _rule_platform_combo(self, ticket: Ticket) -> RuleResult:
        """
        Detect platform/system-origin tickets based on tag-hints + delivery metadata.

        A ticket is considered service-level when:
        1. It contains a platform-indicative tag (amazon/ebay/shopify/tiktok/etc.),
        2. AND one of the following is true:
           - the sender is missing,
           - the sender domain is a known system domain,
           - the ticket was delivered through the 'api' channel.
        """

        tags = [tag.lower() for tag in helpers.get_tags(ticket)]
        has_platform_tag = any(
            hint in tag for tag in tags for hint in self.config.platform_tag_hints
        )
        if not has_platform_tag:
            return RuleResult(RuleOutcome.ABSTAIN)

        sender = helpers.get_sender_email(ticket)
        via_channel = helpers.get_via_channel(ticket)

        if not sender:
            return RuleResult(RuleOutcome.SERVICE, "platform_combo.has_no_sender")
        domain = helpers.email_domain(sender)
        if domain in self.config.system_domains:
            return RuleResult(RuleOutcome.SERVICE, f"platform_combo.system_domains: {domain}")
        if via_channel == "api":
            return RuleResult(RuleOutcome.SERVICE, f"platform_combo.via_channel: {via_channel}")
        return RuleResult(RuleOutcome.ABSTAIN)

    def _rule_api_exceptions(self, ticket: Ticket) -> RuleResult:
        """
        Exceptions for platform API messages that actually contain new customer messages.

        If the subject matches any known 'customer message' API pattern,
        the ticket is explicitly treated as a user ticket (USER outcome) even
        if other rules would classify it as service-level.
        """

        subject = helpers.get_subject(ticket)
        if not subject:
            return RuleResult(RuleOutcome.ABSTAIN)

        for pattern in self.config.api_allowed_patterns:
            if pattern.search(subject):
                return RuleResult(RuleOutcome.USER, f"api_exceptions.pattern: {pattern.pattern}")
        return RuleResult(RuleOutcome.ABSTAIN)

    def _rule_spam_marketing(self, ticket: Ticket) -> RuleResult:
        """
        Detect clear marketing/spam/collaboration emails from personal mailboxes.

        Active only when:
         sender domain is personal (gmail/googlemail/outlook/yahoo/hotmail),
         subject matches one of the spam/marketing patterns.

        These tickets are marked as SERVICE.
        """

        email = helpers.get_sender_email(ticket)
        if not email:
            return RuleResult(RuleOutcome.ABSTAIN)

        domain = helpers.email_domain(email)

        personal_like = (
            domain.endswith("gmail.com")
            or domain.endswith("googlemail.com")
            or domain.endswith("outlook.com")
            or domain.endswith("yahoo.com")
            or domain.endswith("hotmail.com")
        )
        if not personal_like:
            return RuleResult(RuleOutcome.ABSTAIN)

        subject = helpers.get_subject(ticket).lower()
        if not subject:
            return RuleResult(RuleOutcome.ABSTAIN)

        for pattern in self.config.spam_subject_patterns:
            if pattern.search(subject):
                return RuleResult(RuleOutcome.SERVICE, f"spam_marketing.pattern: {pattern.pattern}")

        return RuleResult(RuleOutcome.ABSTAIN)

    def classify_ticket(self, ticket: Ticket) -> ServiceDecision:
        """
        Run the full rule pipeline and return a detailed classification result.

        Order of evaluation:
            1. API exceptions (may explicitly mark a ticket as user-level).
            2. Ordered rule chain:
                 sender_strict → subject_pattern → tag_service → platform_combo → spam_subject
            3. If no rule fires → ticket is treated as a user ticket by default.
        """

        # 1. API exceptions (may explicitly mark a ticket as user-level).
        exc = self._rule_api_exceptions(ticket)
        if exc.outcome == RuleOutcome.USER:
            self.logger.info(
                "allow",
                extra=helpers.make_log_record(ticket, "api_exception", exc.reason),
            )
            return ServiceDecision(is_service=False, rule="api_exception")

        # 2. Ordered rule chain
        rules: list[tuple[str, Callable[[Ticket], RuleResult]]] = [
            ("sender_strict", self._rule_sender_strict),
            ("subject_pattern", self._rule_subject),
            ("tag_service", self._rule_tags_service),
            ("platform_combo", self._rule_platform_combo),
            ("spam_subject", self._rule_spam_marketing),
        ]
        for rule_name, rule_function in rules:
            result = rule_function(ticket)
            if result.outcome == RuleOutcome.SERVICE:
                self.logger.info("skip", extra=helpers.make_log_record(ticket, rule_name, result.reason))
                return ServiceDecision(is_service=True, rule=rule_name)
            if result.outcome == RuleOutcome.USER:
                self.logger.info("allow", extra=helpers.make_log_record(ticket, rule_name, result.reason))
                return ServiceDecision(is_service=False, rule=rule_name)

        # 3. If no rule fires → ticket is treated as a user ticket by default
        self.logger.info(
            "allow", extra=helpers.make_log_record(ticket, reason="no_rule_fires"),
        )
        return ServiceDecision(is_service=False, rule=None)

    def is_service_ticket(self, ticket: Ticket) -> bool:
        """
        Convenience shortcut returning only the boolean classification.

        Returns:
            True  — service ticket (AI reply skipped),
            False — user ticket (AI reply allowed).
        """

        decision = self.classify_ticket(ticket)
        return decision.is_service
