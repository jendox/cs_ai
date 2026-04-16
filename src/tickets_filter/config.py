from __future__ import annotations

import re
import typing
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from functools import partial
from typing import Self

if typing.TYPE_CHECKING:
    from src.tickets_filter.dto import TicketsFilterRuleDTO

__all__ = (
    "TicketsFilterRuleKind",
    "FilterConfig",
    "ScopedPattern",
)


@dataclass(frozen=True)
class ScopedPattern:
    pattern: re.Pattern
    via_channel: str | None = None


def _normalized_via_channel(rule: TicketsFilterRuleDTO) -> str | None:
    if rule.via_channel is None:
        return None
    normalized = rule.via_channel.strip().lower()
    return normalized or None


def _handler_add_to_set(
    target: set[str],
    _rule: TicketsFilterRuleDTO,
    _value: str,
    value_lower: str,
) -> None:
    target.add(value_lower)


def _handler_add_subject_pattern(
    subject_patterns: dict[tuple[str, str | None], ScopedPattern],
    rule: TicketsFilterRuleDTO,
    value: str,
    _value_lower: str,
) -> None:
    pattern_str = value if rule.is_regex else re.escape(value)
    via_channel = _normalized_via_channel(rule)
    key = (pattern_str, via_channel)
    if key not in subject_patterns:
        subject_patterns[key] = ScopedPattern(
            pattern=re.compile(pattern_str, re.IGNORECASE),
            via_channel=via_channel,
        )


def _handler_add_service_tag_prefix(
    service_tags_prefixes: list[str],
    _rule: TicketsFilterRuleDTO,
    _value: str,
    value_lower: str,
) -> None:
    if value_lower not in service_tags_prefixes:
        service_tags_prefixes.append(value_lower)


def _handler_add_api_allowed_pattern(
    api_allowed_patterns: dict[tuple[str, str | None], ScopedPattern],
    rule: TicketsFilterRuleDTO,
    value: str,
    _value_lower: str,
) -> None:
    pattern_str = value if rule.is_regex else re.escape(value)
    via_channel = _normalized_via_channel(rule)
    key = (pattern_str, via_channel)
    if key not in api_allowed_patterns:
        api_allowed_patterns[key] = ScopedPattern(
            pattern=re.compile(pattern_str, re.IGNORECASE),
            via_channel=via_channel,
        )


def _handler_add_customer_body_pattern(
    customer_body_patterns: dict[tuple[str, str | None], ScopedPattern],
    rule: TicketsFilterRuleDTO,
    value: str,
    _value_lower: str,
) -> None:
    pattern_str = value if rule.is_regex else re.escape(value)
    via_channel = _normalized_via_channel(rule)
    key = (pattern_str, via_channel)
    if key not in customer_body_patterns:
        customer_body_patterns[key] = ScopedPattern(
            pattern=re.compile(pattern_str, re.IGNORECASE | re.DOTALL),
            via_channel=via_channel,
        )


def _handler_add_spam_subject_pattern(
    spam_subject_patterns: dict[tuple[str, str | None], ScopedPattern],
    rule: TicketsFilterRuleDTO,
    value: str,
    _value_lower: str,
) -> None:
    pattern_str = value if rule.is_regex else re.escape(value)
    via_channel = _normalized_via_channel(rule)
    key = (pattern_str, via_channel)
    if key not in spam_subject_patterns:
        spam_subject_patterns[key] = ScopedPattern(
            pattern=re.compile(pattern_str, re.IGNORECASE),
            via_channel=via_channel,
        )


def _handler_add_spam_body_pattern(
    spam_body_patterns: dict[tuple[str, str | None], ScopedPattern],
    rule: TicketsFilterRuleDTO,
    value: str,
    _value_lower: str,
) -> None:
    pattern_str = value if rule.is_regex else re.escape(value)
    via_channel = _normalized_via_channel(rule)
    key = (pattern_str, via_channel)
    if key not in spam_body_patterns:
        spam_body_patterns[key] = ScopedPattern(
            pattern=re.compile(pattern_str, re.IGNORECASE | re.DOTALL),
            via_channel=via_channel,
        )


class TicketsFilterRuleKind(StrEnum):
    SYSTEM_DOMAIN = "system_domain"
    SYSTEM_ADDRESS = "system_address"
    ADDRESS_HINT = "address_hint"
    SUBJECT_PATTERN = "subject_pattern"
    SERVICE_TAG_EXACT = "service_tag_exact"
    SERVICE_TAG_PREFIX = "service_tag_prefix"
    PLATFORM_TAG_HINT = "platform_tag_hint"
    API_ALLOWED_PATTERN = "api_allowed_pattern"
    CUSTOMER_BODY_PATTERN = "customer_body_pattern"
    SPAM_SUBJECT_PATTERN = "spam_subject_pattern"
    SPAM_BODY_PATTERN = "spam_body_pattern"


@dataclass
class FilterConfig:
    """
    Immutable configuration object used by TicketsFilter.

    It is built from a collection of rule DTOs and contains:
       system domains and system addresses,
       address hints (address_hints),
       subject patterns (subject_patterns),
       service tags,
       platform tag hints,
       API-allowed patterns and spam subject patterns.
    """
    system_domains: set[str]
    system_addresses: set[str]
    address_hints: tuple[str, ...]
    subject_patterns: tuple[ScopedPattern, ...]
    service_tags_exact: set[str]
    service_tags_prefixes: tuple[str, ...]
    platform_tag_hints: tuple[str, ...]
    api_allowed_patterns: tuple[ScopedPattern, ...]
    customer_body_patterns: tuple[ScopedPattern, ...]
    spam_subject_patterns: tuple[ScopedPattern, ...]
    spam_body_patterns: tuple[ScopedPattern, ...]

    @classmethod
    def from_rules(cls, rules: list[TicketsFilterRuleDTO]) -> Self:
        """
        Build a FilterConfig instance from a list of TicketsFilterRuleDTO.

        All rules with is_active = False or an empty value are ignored.
        For regex rules the value is used as-is; for plain-string rules
        the value is escaped before compilation.
        """

        system_domains: set[str] = set()
        system_addresses: set[str] = set()
        address_hints: set[str] = set()
        subject_patterns: dict[tuple[str, str | None], ScopedPattern] = {}
        service_tags_exact: set[str] = set()
        service_tags_prefixes: list[str] = []
        platform_tag_hints: set[str] = set()
        api_allowed_patterns: dict[tuple[str, str | None], ScopedPattern] = {}
        customer_body_patterns: dict[tuple[str, str | None], ScopedPattern] = {}
        spam_subject_patterns: dict[tuple[str, str | None], ScopedPattern] = {}
        spam_body_patterns: dict[tuple[str, str | None], ScopedPattern] = {}

        handlers: dict[
            TicketsFilterRuleKind,
            Callable[[TicketsFilterRuleDTO, str, str], None],
        ] = {
            TicketsFilterRuleKind.SYSTEM_DOMAIN:
                partial(_handler_add_to_set, system_domains),
            TicketsFilterRuleKind.SYSTEM_ADDRESS:
                partial(_handler_add_to_set, system_addresses),
            TicketsFilterRuleKind.ADDRESS_HINT:
                partial(_handler_add_to_set, address_hints),
            TicketsFilterRuleKind.SUBJECT_PATTERN:
                partial(_handler_add_subject_pattern, subject_patterns),
            TicketsFilterRuleKind.SERVICE_TAG_EXACT:
                partial(_handler_add_to_set, service_tags_exact),
            TicketsFilterRuleKind.SERVICE_TAG_PREFIX:
                partial(_handler_add_service_tag_prefix, service_tags_prefixes),
            TicketsFilterRuleKind.PLATFORM_TAG_HINT:
                partial(_handler_add_to_set, platform_tag_hints),
            TicketsFilterRuleKind.API_ALLOWED_PATTERN:
                partial(_handler_add_api_allowed_pattern, api_allowed_patterns),
            TicketsFilterRuleKind.CUSTOMER_BODY_PATTERN:
                partial(_handler_add_customer_body_pattern, customer_body_patterns),
            TicketsFilterRuleKind.SPAM_SUBJECT_PATTERN:
                partial(_handler_add_spam_subject_pattern, spam_subject_patterns),
            TicketsFilterRuleKind.SPAM_BODY_PATTERN:
                partial(_handler_add_spam_body_pattern, spam_body_patterns),
        }

        cls._process_rules(rules, handlers)

        return cls(
            system_domains=system_domains,
            system_addresses=system_addresses,
            address_hints=tuple(sorted(address_hints)),
            subject_patterns=tuple(subject_patterns.values()),
            service_tags_exact=service_tags_exact,
            service_tags_prefixes=tuple(service_tags_prefixes),
            platform_tag_hints=tuple(sorted(platform_tag_hints)),
            api_allowed_patterns=tuple(api_allowed_patterns.values()),
            customer_body_patterns=tuple(customer_body_patterns.values()),
            spam_subject_patterns=tuple(spam_subject_patterns.values()),
            spam_body_patterns=tuple(spam_body_patterns.values()),
        )

    @classmethod
    def _process_rules(
        cls,
        rules: list[TicketsFilterRuleDTO],
        handlers: dict[
            TicketsFilterRuleKind,
            Callable[[TicketsFilterRuleDTO, str, str], None],
        ],
    ) -> None:
        """Process all active rules using appropriate handlers."""
        for rule in rules:
            if not rule.is_active:
                continue

            value = (rule.value or "").strip()
            if not value:
                continue

            value_lower = value.lower() if not rule.is_regex else value
            handler = handlers.get(rule.kind)
            if handler is not None:
                handler(rule, value, value_lower)
