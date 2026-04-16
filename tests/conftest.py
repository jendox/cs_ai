"""Pytest configuration and shared fixtures."""

from __future__ import annotations

import os

import pytest

# Zendesk client brand IDs are read at import time; keep tests deterministic in CI.
os.environ.setdefault("BRAND__SUPERSELF_ID", "23064017794844")
os.environ.setdefault("BRAND__SMARTPARTS_ID", "23063999037340")
os.environ.setdefault("BRAND__CLEOCORA_ID", "23063999037000")
os.environ.setdefault("BRAND__SUPPORTED", "SUPERSELF")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("CS_INTEGRATION") == "1":
        return
    skip_integration = pytest.mark.skip(
        reason=(
            "Integration tests skipped. Start infra: "
            "docker compose -f deploy/docker-compose.test.yml up -d "
            "then CS_INTEGRATION=1 uv run pytest tests/integration -q"
        ),
    )
    for item in items:
        path = getattr(item, "path", None)
        if path is not None and "/tests/integration/" in path.as_posix():
            item.add_marker(skip_integration)
