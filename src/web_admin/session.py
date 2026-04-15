from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from src.config import WebAdminSettings

SESSION_MAX_AGE_SECONDS = 60 * 60 * 12


@dataclass(frozen=True)
class SessionData:
    user_id: int


class SessionManager:

    def __init__(self, settings: WebAdminSettings) -> None:
        self._settings = settings
        self._serializer = URLSafeTimedSerializer(
            secret_key=settings.session_secret.get_secret_value(),
            salt="cs-web-admin-session",
        )

    @property
    def cookie_name(self) -> str:
        return "cs_admin_session"

    @property
    def max_age_seconds(self) -> int:
        return SESSION_MAX_AGE_SECONDS

    @property
    def cookie_secure(self) -> bool:
        return self._settings.cookie_secure

    def create(self, *, user_id: int) -> str:
        payload = {
            "user_id": user_id,
            "created_at": datetime.now(UTC).isoformat(),
        }
        return self._serializer.dumps(payload)

    def load(self, token: str) -> SessionData | None:
        try:
            payload: dict[str, Any] = self._serializer.loads(
                token,
                max_age=self.max_age_seconds,
            )
        except (BadSignature, SignatureExpired):
            return None

        user_id = payload.get("user_id")
        if not isinstance(user_id, int):
            return None

        return SessionData(user_id=user_id)
