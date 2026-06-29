"""
Cache provider adapters for the baldur system.

This module contains concrete implementations of CacheProviderInterface
for different cache backends.

Available Adapters:
    - RedisCacheAdapter: Redis-based caching with distributed locks
    - InMemoryCacheAdapter: In-memory caching for testing

Status: Public
"""

from baldur.adapters.cache.memory_adapter import (
    InMemoryCacheAdapter,
)
from baldur.adapters.cache.metrics_decorator import (
    MetricsAwareCacheAdapter,
)
from baldur.adapters.cache.redis_adapter import (
    RedisCacheAdapter,
)

__all__ = [
    "RedisCacheAdapter",
    "InMemoryCacheAdapter",
    "MetricsAwareCacheAdapter",
]
