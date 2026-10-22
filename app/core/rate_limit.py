"""Rate limiting local para endpoints autenticados y sensibles.

Es deliberadamente fail-closed para una instancia: en despliegues con varias
réplicas conviene poner el mismo límite en Railway/proxy o migrar el contador a
Redis. No se usa en endpoints de webhooks.
"""
from collections import defaultdict, deque
from threading import Lock
from time import monotonic

from fastapi import HTTPException, Request, status


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: int):
        self.limit = limit
        self.window_seconds = window_seconds
        self._events = defaultdict(deque)
        self._lock = Lock()

    def check(self, key: str) -> None:
        now = monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            bucket = self._events[key]
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self.limit:
                retry_after = max(1, int(bucket[0] + self.window_seconds - now))
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Demasiadas solicitudes. Probá nuevamente en unos segundos.",
                    headers={"Retry-After": str(retry_after)},
                )
            bucket.append(now)
            if len(self._events) > 10000:
                self._events.clear()


login_limiter = SlidingWindowLimiter(limit=10, window_seconds=60)
driver_login_limiter = SlidingWindowLimiter(limit=10, window_seconds=60)
trip_action_limiter = SlidingWindowLimiter(limit=30, window_seconds=60)
gps_limiter = SlidingWindowLimiter(limit=120, window_seconds=60)


def client_key(request: Request, suffix: str = "") -> str:
    # Railway/proxies normalizan X-Forwarded-For; tomamos solo el primer hop.
    forwarded = request.headers.get("x-forwarded-for", "")
    address = forwarded.split(",", 1)[0].strip() or (request.client.host if request.client else "unknown")
    return f"{suffix}:{address}"
