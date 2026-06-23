# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
