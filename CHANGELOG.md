# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Runtime Config editor — a web console panel to view and change runtime-tunable settings (retry attempts, circuit-breaker thresholds, DLQ limits, and more) from the browser, with a per-section apply-strategy selector, a `current → proposed` diff confirmation before applying, an out-of-range clamp notice, and an audit-status badge. Replaces hand-crafting `PUT` calls with `curl` or editing env vars and restarting. (PRO)
- Runtime-config REST surface — read responses now include a per-section `version`, and `PUT /config/{section}` accepts an optional `expected_version` for optimistic-concurrency control. A stale write returns HTTP 409 with the expected/actual versions and the current config so the client can merge and retry; omitting `expected_version` preserves the previous unconditional write behavior. (PRO)

### Removed

- `BALDUR_SCALING_LOAD_SHEDDING_ENABLED` — removed this environment variable and its backing `ScalingSettings`. It was inert (no code read it), so setting it never had any effect; the active request-path load-shedding gate is the default-off backpressure controller.

### Fixed

- Scheduled maintenance now runs out-of-box on a single host. The built-in jobs (applying delayed config changes, circuit-breaker recovery checks, DLQ archival, expired-config cleanup, SLA-drift detection, daily report) previously stayed dormant unless you separately enabled distributed leader election or wired Celery beat — a plain single-host install ran none of them. One process per host now elects itself via a local lock and runs them; multi-host single-execution still needs distributed leader election or Celery beat (see the multi-worker-coherence runbook).
- Delayed and graceful runtime-config changes now actually apply. Editing a section whose default apply strategy is delayed (circuit breaker, DLQ, retry, idempotency, security) and clicking **Apply** previously returned "scheduled" but the value never changed and no clamp or audit was recorded; the change now takes effect after its delay, with the out-of-range clamp surfaced and the change audited. (PRO)
- Config history rollback — rolling back a saved configuration version now applies correctly for every section (retry, circuit breaker, DLQ, and the rest), instead of failing for every Pydantic-backed section and silently leaving the configuration unchanged. (PRO)
- FastAPI and Flask apps no longer load the Django integration at import time. Previously, if Django happened to be installed but unconfigured (a common transitive dependency), a non-Django app would pull in the entire Django API on startup and spawn a background thread that logged repeated errors on every tick; the Django layer is now imported lazily, only when actually used.
- Constructing a PRO-tier resilience preset or policy (`ha_pipeline`, `HedgingPolicy`, `AsyncHedgingPolicy`) without a PRO license now raises a clear error naming the required tier, instead of an opaque `'NoneType' object is not callable`.
- Daily Report — the Slack digest now delivers the full multi-section report instead of a body cut off at 500 characters and padded with unreadable raw-metadata fields; sections for features not in the current release are no longer shown, and operator-defined custom metrics render under their own heading instead of trailing the error list. (PRO)
- Audit trail — a delayed or graceful configuration change is now attributed to the operator who requested it, instead of being recorded as an internal worker. The "who changed what" record is now accurate for every configuration change regardless of apply strategy. (PRO)
- Concurrent runtime-config edits are no longer silently lost. Previously, two operators (or pods) editing the same configuration section within one read→write window resolved last-writer-wins with no signal — the first operator's change vanished. Edits are now guarded by an optimistic-concurrency version: a stale edit is rejected with HTTP 409 (carrying the current version and values to retry against) instead of overwriting a fresher change, and a scheduled/rollback apply that races an unrelated change auto-retries rather than dropping. A pending (delayed/graceful) change created while another change is being applied is also no longer dropped. (PRO)

### Security

- Pool Circuit Breaker — the HTTP 503 returned when the database connection pool is exhausted no longer echoes the raw database/driver exception text in its response body (the detail is still logged server-side), preventing internal database information from leaking to API callers.

## [1.0.0] - 2026-06-23

This is the inaugural release. The changelog begins at v1.0; pre-release internal
changes are intentionally omitted. Entries marked `(PRO)` require a PRO license;
unmarked entries are part of the free core.

### Added

- Circuit Breaker — stops calling a failing dependency after repeated failures and automatically probes for recovery.
- Retry — automatically re-runs a failed operation with growing backoff between attempts.
- Idempotency — blocks duplicate runs of must-happen-once operations (card charge, email, shipment) when retries, double-clicks, or duplicate webhooks replay the same request.
- Bulkhead — isolates each dependency in its own fixed slice of capacity. (PRO)
- Dead Letter Queue + Replay — captures operations that fail on a downed dependency with the context needed to re-run them, and replays the backlog once it recovers. (PRO)
- Emergency Mode — sheds non-critical traffic in deliberate steps under stress and holds until the system stabilizes. (PRO)
- Adaptive Throttle — caps admitted requests and moves the cap up or down automatically as the service speeds up or slows down. (PRO)
- Graceful Shutdown — drains in-flight requests and flushes subsystems on a shutdown signal before the process exits.
- Health Check — ready-made liveness and readiness endpoints that signal Kubernetes and load balancers when a pod should receive traffic.
- System Control — runtime kill switch that halts every automated intervention, plus an observe-only dry-run mode; no redeploy needed and the setting persists across restarts and servers.
- Web Console — built-in zero-config browser UI to view self-healing state and run control actions (DLQ replay, circuit-breaker reset, emergency, canary) from one page.
- Canary Recovery — rolls a configuration change out to a small slice of the fleet first and automatically restores the previous config if the rollout degrades or stalls. (PRO)
- Governance — a safety gate every automated recovery action must pass before running, recording why each action was allowed or blocked. (PRO)
- Meta-Watchdog — monitors Baldur's own healing subsystems and pages a human if they stall or go quiet. (PRO)
- Metrics — auto-recorded Prometheus-format metrics for the self-healing layer, with a cardinality guard that prevents label explosion.
- Dashboard — one read-only call that aggregates the full self-healing picture (failures, recoveries, current health) into a single snapshot for monitoring screens.
- Daily Report — rolls a day of self-healing activity (recoveries, circuit-breaker trips, errors) into a single digest, formatted for Slack.
- Precomputed Cache — serves Baldur's own status endpoints (health, connection-pool) from a background-warmed cache.
- Unified Notification — one hub that routes, deduplicates, rate-limits, and records every alert Baldur raises, delivering to Slack. (PRO)
- Audit Trail — tamper-evident, append-only record of every configuration change and healing decision (who, what, when, why) with masked client IPs. (PRO)
