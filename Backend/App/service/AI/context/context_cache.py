from collections import OrderedDict
from threading import RLock
from time import monotonic
from typing import Callable, TypeVar, cast


T = TypeVar("T")


class BoundedContextCache:
    def __init__(self, max_entries: int = 128, ttl_seconds: float = 30.0):
        if max_entries < 1 or ttl_seconds <= 0:
            raise ValueError("Cache size and expiry must be positive.")
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self._values: OrderedDict[tuple[object, ...], tuple[float, object]] = (
            OrderedDict()
        )
        self._lock = RLock()

    def get_or_create(
        self,
        key: tuple[object, ...],
        factory: Callable[[], T],
    ) -> T:
        now = monotonic()
        with self._lock:
            cached = self._values.get(key)
            if cached is not None and cached[0] > now:
                self._values.move_to_end(key)
                return cast(T, cached[1])
            self._values.pop(key, None)

        value = factory()
        with self._lock:
            self._values[key] = (now + self.ttl_seconds, value)
            self._values.move_to_end(key)
            while len(self._values) > self.max_entries:
                self._values.popitem(last=False)
        return value

    def invalidate_project(self, project_id: int) -> None:
        with self._lock:
            keys = [
                key
                for key in self._values
                if len(key) > 1 and key[1] == project_id
            ]
            for key in keys:
                self._values.pop(key, None)


project_structure_cache = BoundedContextCache()
