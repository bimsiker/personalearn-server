from datetime import UTC, datetime


def utcnow() -> datetime:
    """Timezone-aware 'now' in UTC. Use everywhere instead of datetime.now()."""
    return datetime.now(UTC)
