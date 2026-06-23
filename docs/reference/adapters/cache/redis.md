# RedisCacheAdapter — Redis Cache Backend

The production-default `CacheProviderInterface` implementation. Backs
distributed locks with Redlock-style primitives so cache coordination is safe
across processes and hosts.

::: baldur.adapters.cache.RedisCacheAdapter
