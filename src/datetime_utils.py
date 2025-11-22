from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def iso_to_unix(s: str) -> int:
    # 'YYYY-MM-DDTHH:MM:SSZ' -> epoch seconds (UTC)
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def iso_to_utc_dt(s: str) -> datetime:
    # "2025-10-16T19:52:57Z" -> aware UTC datetime
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC)


def dt_to_iso(dt: datetime) -> str:
    # aware datetime -> "YYYY-MM-DDTHH:MM:SSZ"
    return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
