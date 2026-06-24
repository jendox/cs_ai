from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from src import datetime_utils
from src.ai.amazon_mcp_client import AmazonMCPHttpClient
from src.brands import Brand
from src.config import get_app_settings
from src.db import session_local
from src.db.repositories import AcquireLockError, LocksRepository
from src.workflows.catalog_sync import (
    CATALOG_SYNC_LOCK_TTL,
    MarketplaceSyncOutcome,
    brand_sync_lock_name,
    sync_catalog_for_brand_marketplaces,
)
from src.workflows.schemes import MarketplaceId

logger = logging.getLogger("web_admin.catalog_sync")

CatalogSyncRunStatus = Literal["running", "completed", "failed"]
MarketplaceProgressStatus = Literal["pending", "running", "completed", "failed", "skipped"]


@dataclass
class MarketplaceProgress:
    marketplace: MarketplaceId
    label: str
    status: MarketplaceProgressStatus = "pending"
    row_count: int | None = None
    error: str | None = None


@dataclass
class CatalogSyncRunState:
    brand: Brand
    status: CatalogSyncRunStatus
    started_at: datetime
    started_by: str
    marketplaces: list[MarketplaceProgress] = field(default_factory=list)
    finished_at: datetime | None = None
    error: str | None = None
    scope: str = "all_eu"

    @property
    def completed_count(self) -> int:
        return sum(
            1
            for item in self.marketplaces
            if item.status in {"completed", "failed", "skipped"}
        )

    @property
    def total_count(self) -> int:
        return len(self.marketplaces)

    def to_dict(self) -> dict[str, object]:
        return {
            "brand": self.brand.value,
            "status": self.status,
            "scope": self.scope,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "started_by": self.started_by,
            "error": self.error,
            "completed_count": self.completed_count,
            "total_count": self.total_count,
            "marketplaces": [
                {
                    "marketplace": item.marketplace.value,
                    "label": item.label,
                    "status": item.status,
                    "row_count": item.row_count,
                    "error": item.error,
                }
                for item in self.marketplaces
            ],
        }


class CatalogSyncManager:
    def __init__(self) -> None:
        self._states: dict[str, CatalogSyncRunState] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._guard = asyncio.Lock()

    def get_state(self, brand: Brand) -> CatalogSyncRunState | None:
        return self._states.get(brand.value)

    def clear_state(self, brand: Brand) -> None:
        self._states.pop(brand.value, None)
        self._tasks.pop(brand.value, None)

    def is_running(self, brand: Brand) -> bool:
        state = self.get_state(brand)
        return state is not None and state.status == "running"

    async def start(
        self,
        brand: Brand,
        amazon_mcp_client: AmazonMCPHttpClient,
        *,
        started_by: str,
        marketplaces: list[MarketplaceId] | None = None,
    ) -> Literal["started", "already_running"]:
        async with self._guard:
            if self.is_running(brand):
                return "already_running"

            selected_marketplaces = sorted(
                marketplaces or MarketplaceId.eu_marketplaces(),
                key=lambda item: item.name,
            )
            brand_id = get_app_settings().brand.id_for(brand)
            lock_name = brand_sync_lock_name(brand_id)
            lock_holder = f"web_admin:{started_by}"

            async with session_local() as session:
                lock_repo = LocksRepository(session)
                async with session.begin():
                    try:
                        await lock_repo.acquire_lock(
                            name=lock_name,
                            holder=lock_holder,
                            ttl_seconds=CATALOG_SYNC_LOCK_TTL,
                        )
                    except AcquireLockError:
                        return "already_running"

            scope = selected_marketplaces[0].name if len(selected_marketplaces) == 1 else "all_eu"
            state = CatalogSyncRunState(
                brand=brand,
                status="running",
                started_at=datetime_utils.utcnow(),
                started_by=started_by,
                scope=scope,
                marketplaces=[
                    MarketplaceProgress(
                        marketplace=marketplace,
                        label=marketplace.name,
                    )
                    for marketplace in selected_marketplaces
                ],
            )
            self._states[brand.value] = state
            task = asyncio.create_task(
                self._run(
                    brand=brand,
                    lock_name=lock_name,
                    lock_holder=lock_holder,
                    amazon_mcp_client=amazon_mcp_client,
                    marketplaces=selected_marketplaces,
                ),
                name=f"catalog-sync-{brand.value}-{scope}",
            )
            self._tasks[brand.value] = task
            task.add_done_callback(lambda _: self._tasks.pop(brand.value, None))
            return "started"

    async def _run(
        self,
        *,
        brand: Brand,
        lock_name: str,
        lock_holder: str,
        amazon_mcp_client: AmazonMCPHttpClient,
        marketplaces: list[MarketplaceId],
    ) -> None:
        state = self._states[brand.value]
        try:
            outcomes = await sync_catalog_for_brand_marketplaces(
                brand,
                amazon_mcp_client,
                marketplaces=marketplaces,
                on_marketplace_started=self._marketplace_started_callback(state),
                on_marketplace_complete=self._marketplace_complete_callback(state),
            )
            self._finalize_state(state, outcomes)
        except Exception as exc:
            self._fail_state(state, exc)
        finally:
            state.finished_at = datetime_utils.utcnow()
            await self._release_brand_lock(lock_name, lock_holder)

    @staticmethod
    def _marketplace_started_callback(state: CatalogSyncRunState):
        async def on_marketplace_started(marketplace: MarketplaceId) -> None:
            for item in state.marketplaces:
                if item.marketplace == marketplace:
                    item.status = "running"
                    break

        return on_marketplace_started

    @staticmethod
    def _marketplace_complete_callback(state: CatalogSyncRunState):
        async def on_marketplace_complete(outcome: MarketplaceSyncOutcome) -> None:
            for item in state.marketplaces:
                if item.marketplace != outcome.marketplace:
                    continue
                item.status = outcome.status
                item.row_count = outcome.row_count or None
                item.error = outcome.error
                break

        return on_marketplace_complete

    @staticmethod
    def _finalize_state(
        state: CatalogSyncRunState,
        outcomes: list[MarketplaceSyncOutcome],
    ) -> None:
        failed = [item for item in outcomes if item.status == "failed"]
        state.status = "failed" if failed else "completed"
        if failed:
            state.error = f"{len(failed)} marketplace(s) failed"

    @staticmethod
    def _fail_state(state: CatalogSyncRunState, exc: Exception) -> None:
        logger.exception(
            "catalog_sync.background_failed",
            extra={"brand": state.brand.value, "error": str(exc)},
        )
        state.status = "failed"
        state.error = str(exc)
        for item in state.marketplaces:
            if item.status in {"pending", "running"}:
                item.status = "failed"
                item.error = str(exc)

    @staticmethod
    async def _release_brand_lock(lock_name: str, lock_holder: str) -> None:
        async with session_local() as session:
            lock_repo = LocksRepository(session)
            async with session.begin():
                await lock_repo.release_lock(name=lock_name, holder=lock_holder)
