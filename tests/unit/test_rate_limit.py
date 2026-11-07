from app.core.rate_limit import RateLimiter


def test_allows_up_to_limit_then_blocks():
    limiter = RateLimiter()
    for i in range(3):
        assert limiter.hit("login:1.2.3.4", limit=3, window_seconds=60, now=100 + i) == 0
    retry = limiter.hit("login:1.2.3.4", limit=3, window_seconds=60, now=103)
    assert 0 < retry <= 60


def test_window_slides_and_frees_capacity():
    limiter = RateLimiter()
    for t in (0, 1, 2):
        limiter.hit("k", limit=3, window_seconds=10, now=t)
    assert limiter.hit("k", limit=3, window_seconds=10, now=5) > 0
    assert limiter.hit("k", limit=3, window_seconds=10, now=10.5) == 0  # first hit expired


def test_keys_are_independent():
    limiter = RateLimiter()
    limiter.hit("login:a", limit=1, window_seconds=60, now=0)
    assert limiter.hit("login:a", limit=1, window_seconds=60, now=1) > 0
    assert limiter.hit("login:b", limit=1, window_seconds=60, now=1) == 0
    assert limiter.hit("signup:a", limit=1, window_seconds=60, now=1) == 0


def test_disabled_limiter_never_blocks():
    limiter = RateLimiter(enabled=False)
    for t in range(100):
        assert limiter.hit("k", limit=1, window_seconds=60, now=t) == 0


def test_blocked_hits_are_not_counted():
    limiter = RateLimiter()
    limiter.hit("k", limit=1, window_seconds=10, now=0)
    for t in range(1, 9):
        limiter.hit("k", limit=1, window_seconds=10, now=t)  # all blocked
    assert limiter.hit("k", limit=1, window_seconds=10, now=10.1) == 0
