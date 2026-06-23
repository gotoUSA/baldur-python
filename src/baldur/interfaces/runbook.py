"""Runbook-side PRO Type Markers (519 PR 3 / C-d1 rule 1).

OSS-side Protocols for PRO types referenced in TYPE_CHECKING-only
positions by ``src/baldur/services/runbook/executor.py``. The realized
implementations live in :mod:`baldur_pro.services.coordination`; OSS
code only annotates these types and does not call methods on them
directly.

Distinct from :mod:`baldur.interfaces.coordination` style — the names
here mirror the PRO class names verbatim so the import flip is a
mechanical one-line rename.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

__all__ = ["DistributedRecoveryLock", "IdempotencyRecord"]


@runtime_checkable
class DistributedRecoveryLock(Protocol):
    """Type marker for the PRO multi-backend recovery lock facade.

    OSS code in the runbook executor holds a lock reference for type
    annotation but acquires/releases through PRO surface only.
    """

    def acquire(self, *args: Any, **kwargs: Any) -> Any: ...

    def release(self, *args: Any, **kwargs: Any) -> Any: ...

    def extend(self, *args: Any, **kwargs: Any) -> Any: ...


@runtime_checkable
class IdempotencyRecord(Protocol):
    """Type marker for the PRO idempotency record DTO.

    Carries idempotency key + step lifecycle status. OSS code in the
    runbook executor reads identity/status fields for routing but does
    not construct records directly.
    """

    idempotency_key: str
    session_id: str
    step_type: str
    status: Any
    started_at: str | None
    completed_at: str | None
    result: dict[str, Any] | None
    error_message: str | None
    retry_count: int

    def is_safe_to_execute(self) -> bool: ...

    def to_dict(self) -> dict[str, Any]: ...
