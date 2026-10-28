import pytest

import app.core.rate_limit as rate_limit
from app.core.rate_limit import SlidingWindowLimiter


def test_rate_limiter_rejects_only_the_excess_key():
    limiter = SlidingWindowLimiter(limit=1, window_seconds=60, max_keys=10)
    limiter.check("one")
    with pytest.raises(Exception) as error:
        limiter.check("one")
    assert error.value.status_code == 429
    limiter.check("two")


def test_rate_limiter_removes_expired_events_from_deque(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(rate_limit, "monotonic", lambda: now[0])
    limiter = SlidingWindowLimiter(limit=2, window_seconds=60, max_keys=10)
    limiter.check("driver")
    now[0] = 161.0
    limiter.check("driver")


def test_rate_limiter_eviction_keeps_existing_limits_isolated():
    limiter = SlidingWindowLimiter(limit=1, window_seconds=60, max_keys=2)
    limiter.check("one")
    limiter.check("two")
    limiter.check("three")
    with pytest.raises(Exception) as error:
        limiter.check("two")
    assert error.value.status_code == 429
