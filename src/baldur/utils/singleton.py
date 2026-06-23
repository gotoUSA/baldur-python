"""
Thread-safe singleton factory helper.

Replaces hand-rolled DCL (Double-Check Locking) singleton patterns with a
generic factory that delegates to :class:`baldur.runtime.BaldurRuntime`.

The triple ``(get_fn, configure_fn, reset_fn)`` returned by
:func:`make_singleton_factory` is the same shape as before — but each function
now reads/writes through the active ``BaldurRuntime``'s singleton store, so
test isolation, ``copy_context()`` scoping, and runtime swap-in just work.
"""

# Reference: docs/impl/450_SCOPED_RUNTIME_CONTEXT.md (D2 — Phase 2 delegation).

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

import structlog

T = TypeVar("T")

logger = structlog.get_logger()

_REGISTRY: dict[
    str, tuple[Callable[[], Any], Callable[[Any], None], Callable[..., None]]
] = {}


def CLEANUP_CLOSE(x: Any) -> None:
    x.close()


def CLEANUP_STOP(x: Any) -> None:
    x.stop()


def make_singleton_factory(
    name: str,
    create_fn: Callable[[], T],
    *,
    cleanup_fn: Callable[[T], None] | None = None,
) -> tuple[Callable[[], T], Callable[[T], None], Callable[..., None]]:
    """Create a runtime-scoped singleton factory.

    All three functions delegate to the active :class:`BaldurRuntime`:

    - ``get_fn()`` → ``runtime.get_singleton(name, create_fn)``
    - ``configure_fn(value)`` → ``runtime.set_singleton(name, value)``
    - ``reset_fn(*, cleanup=True)`` → ``runtime.reset_singleton(name)`` then
      ``cleanup_fn(old)`` outside the runtime lock.

    Returns:
        (get_fn, configure_fn, reset_fn) tuple.
        reset_fn accepts optional cleanup: bool = True keyword argument.
    """
    # Local import avoids a circular dependency: baldur.runtime imports nothing
    # from utils.singleton, but several baldur subpackages exercise this module
    # at import time before baldur.runtime has finished defining its accessors.
    from baldur.runtime import get_runtime

    def get_fn() -> T:
        return get_runtime().get_singleton(name, create_fn)  # type: ignore[no-any-return]

    def configure_fn(value: T) -> None:
        get_runtime().set_singleton(name, value)

    def reset_fn(*, cleanup: bool = True) -> None:
        was_present, old = get_runtime().reset_singleton(name)
        if was_present and cleanup and cleanup_fn is not None:
            try:
                cleanup_fn(old)
            except Exception:
                logger.warning("singleton.cleanup_failed", name=name, exc_info=True)

    triple = (get_fn, configure_fn, reset_fn)
    _REGISTRY[name] = triple
    return triple


__all__ = [
    "CLEANUP_CLOSE",
    "CLEANUP_STOP",
    "make_singleton_factory",
]
