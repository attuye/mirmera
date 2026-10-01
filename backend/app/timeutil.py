from datetime import datetime, timezone


def utcnow() -> datetime:
    """Current UTC time as a naive datetime.

    Replaces datetime.utcnow(), which is deprecated from Python 3.12. It stays
    naive (no timezone attached) on purpose: the database stores naive UTC
    values and the frontend reads them as UTC, so existing data and the UI
    keep working unchanged.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)