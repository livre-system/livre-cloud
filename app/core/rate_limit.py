from collections import OrderedDict, deque
from threading import Lock
from time import monotonic

from fastapi import HTTPException, Request, status


class SlidingWindowLimiter:
    """Límite por proceso con limpieza acotada y respuesta Retry-After."""

    def __init__(self, limit: int, window_seconds: int, max_keys: int = 10000):
        if limit < 1 or window_seconds < 1 or max_keys < 1:
            raise ValueError("limit, window_seconds y max_keys deben ser positivos")
        self.limit = limit
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self._events = OrderedDict()
        self._lock = Lock()

    def check(self, key: str) -> None:
        now = monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            bucket = self._events.get(key)
            if bucket is None:
                bucket = deque()
                self._events[key] = bucket
            else:
                while bucket and bucket[0] <= cutoff:
                    bucket.popleft()
                self._events.move_to_end(key)
            if len(bucket) >= self.limit:
                retry_after = max(1, int(bucket[0] + self.window_seconds - now))
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Demasiadas solicitudes. Probá nuevamente en unos segundos.",
                    headers={"Retry-After": str(retry_after)},
                )
            bucket.append(now)
            while len(self._events) > self.max_keys:
                self._events.popitem(last=False)


def client_key(request: Request, suffix: str = "") -> str:
    # Railway termina el TLS y entrega X-Forwarded-For con el cliente original.
    forwarded = request.headers.get("x-forwarded-for", "")
    address = forwarded.split(",", 1)[0].strip() or (request.client.host if request.client else "unknown")
    return f"{suffix}:{address}"


login_limiter = SlidingWindowLimiter(limit=10, window_seconds=60)
driver_login_limiter = SlidingWindowLimiter(limit=10, window_seconds=60)
trip_action_limiter = SlidingWindowLimiter(limit=30, window_seconds=60)
gps_limiter = SlidingWindowLimiter(limit=120, window_seconds=60)
