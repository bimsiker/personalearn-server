from datetime import UTC, datetime


def utcnow() -> datetime:
    """Timezone-aware 'now' in UTC, rounded down to milliseconds.

    MongoDB stores milliseconds, so rounding here keeps the value we return on create
    identical to the value read back later. Use everywhere instead of datetime.now().
    """
    now = datetime.now(UTC)
    return now.replace(microsecond=now.microsecond // 1000 * 1000)
