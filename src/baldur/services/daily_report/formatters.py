"""
Report Formatting for Various Channels.

Provides formatting functions for Slack and PagerDuty output.
"""

from __future__ import annotations

from .models import DailyAutonomousReport


def format_report_for_slack(report: DailyAutonomousReport) -> str:  # noqa: C901, PLR0912, PLR0915
    """
    Format report as Slack message with adaptive sections (D10).

    Core summary line is always shown (confirms system is running).
    Detail sections expand only when their counts > 0 (reduce noise).

    Args:
        report: DailyAutonomousReport instance

    Returns:
        Formatted Slack message string
    """
    total_operations = (
        report.archived_count
        + report.expired_count
        + report.purged_count
        + report.recovered_count
    )

    status_emoji = "✅" if report.task_failures == 0 else "⚠️"

    # Core summary line (always shown)
    lines = [
        f"{status_emoji} *Autonomous Daily Report* ({report.date:%Y-%m-%d})",
    ]

    # Build the detail sections first (D10). Each section appends its lines to
    # `detail`; the quiet-day short-circuit then keys off whether `detail` is
    # empty — so a section added later is counted by construction, with no
    # separate condition chain to keep in sync.
    detail: list[str] = []

    # Auto-Processing Summary (conditional — any count > 0, D10)
    if (
        report.archived_count
        or report.expired_count
        or report.purged_count
        or report.recovered_count
    ):
        detail.extend(
            [
                "",
                "*📊 Auto-Processing Summary*",
                f"• Archived: {report.archived_count} / Expired: {report.expired_count} / Purged: {report.purged_count} / Recovered: {report.recovered_count}",
            ]
        )

    # Alerts (conditional — drift or approval_expired > 0, D10)
    if report.drift_warnings_count > 0 or report.approval_expired_count > 0:
        detail.extend(
            [
                "",
                "*🔔 Alerts*",
                f"• Drift warnings: {report.drift_warnings_count} / Approval expired: {report.approval_expired_count}",
            ]
        )

    # Circuit breaker stats if any
    if report.circuit_transitions > 0:
        detail.extend(
            [
                "",
                "*⚡ Circuit Breaker*",
                f"• Transitions: {report.circuit_transitions} (Open: {report.circuits_opened} / Close: {report.circuits_closed})",
            ]
        )

    # DLQ section (D9 — with failure_type breakdown and recommended action)
    if (
        report.dlq_new_entries_count > 0
        or report.dlq_resolved_count > 0
        or report.dlq_pending_count > 0
    ):
        detail.extend(
            [
                "",
                f"📬 DLQ: {report.dlq_new_entries_count} new / {report.dlq_resolved_count} auto-resolved / {report.dlq_pending_count} pending",
            ]
        )
        if (
            report.dlq_manual_resolutions
            or report.dlq_ttl_expired
            or report.dlq_max_retries_exhausted
        ):
            detail.append(
                f"  └ Manual {report.dlq_manual_resolutions} / TTL expired {report.dlq_ttl_expired} / Max retries {report.dlq_max_retries_exhausted}"
            )
        # Per-failure-type breakdown with action guidance (D9)
        if (
            report.dlq_pending_breakdown
            and report.dlq_pending_breakdown.by_failure_type
        ):
            detail.append("  Needs attention:")
            for ft, bd in report.dlq_pending_breakdown.by_failure_type.items():
                domains_str = ", ".join(bd.domains) if bd.domains else "unknown"
                detail.append(f"  • {domains_str}: {bd.count} {ft} — {bd.action}")

    # Chaos section
    if report.chaos_summary:
        chaos = report.chaos_summary
        detail.extend(
            [
                "",
                "*🧪 Chaos Resilience*",
                f"• Grade: {chaos.grade} ({chaos.grade_trend}) | Experiments: {chaos.experiments_passed}/{chaos.experiments_total} passed",
            ]
        )
        if chaos.sla_breaches > 0 or chaos.error_budget_consumed_pct > 0:
            detail.append(
                f"• SLA breaches: {chaos.sla_breaches} | Error budget consumed: {chaos.error_budget_consumed_pct:.1f}%"
            )

    # Load shedding section
    if report.load_shedding_summary:
        shed = report.load_shedding_summary
        detail.extend(
            [
                "",
                "*🛡️ Load Shedding*",
                f"• Level: {shed.level} | Dropped: {shed.dropped_total} / Processed: {shed.processed_total}",
            ]
        )
        if shed.dropped_by_tier:
            tier_parts = [f"{k}: {v}" for k, v in shed.dropped_by_tier.items() if v > 0]
            if tier_parts:
                detail.append(f"  └ By tier: {' / '.join(tier_parts)}")

    # Error budget section
    if report.error_budget_summary:
        eb = report.error_budget_summary
        detail.extend(
            [
                "",
                f"*🚦 Error Budget Gate* — {eb.blocks} blocks, {eb.warnings} warnings",
            ]
        )

    # Automated Actions section (Phase 3.2, D7) — conditional; omitted for OSS
    if report.automated_actions_summary:
        auto = report.automated_actions_summary
        detail.extend(["", "*🤖 Automated Actions*"])
        if auto.auto_replay_batches > 0:
            detail.append(
                f"• Auto-replay: {auto.auto_replay_batches} batches, "
                f"{auto.auto_replay_recovered} recovered / {auto.auto_replay_failed} failed"
            )
        if auto.canary_completed > 0 or auto.canary_rolled_back > 0:
            detail.append(
                f"• Canary: {auto.canary_completed} completed / {auto.canary_rolled_back} rolled back"
            )
        if auto.auto_tuning_applied > 0:
            detail.append(f"• Auto-tuning applied: {auto.auto_tuning_applied}")
        if auto.emergency_level_changes > 0:
            detail.append(f"• Emergency level changes: {auto.emergency_level_changes}")
        if auto.saga_completed > 0 or auto.saga_compensated > 0:
            detail.append(
                f"• Saga: {auto.saga_completed} completed / {auto.saga_compensated} compensated"
            )
        if auto.governance_blocked > 0:
            detail.append(f"• Governance blocked: {auto.governance_blocked}")

    # Error summary if any
    if report.task_failures > 0 or report.critical_alerts > 0:
        detail.extend(
            [
                "",
                "*❌ Errors*",
                f"• Task failures: {report.task_failures}",
                f"• Critical alerts: {report.critical_alerts}",
            ]
        )

    # Custom metrics
    for key, value in report.custom_counts.items():
        if value > 0:
            detail.append(f"• {key}: {value}")

    # Shadow PRO insights (427 §4.6, visibility policy: impl 452)
    if report.shadow_pro_summary:
        shadow = report.shadow_pro_summary
        detail.extend(["", "*💡 PRO Insights*"])
        if shadow.cb_trips_without_auto_degradation > 0:
            detail.append(
                f"• {shadow.cb_trips_without_auto_degradation} CB trips without auto-degradation"
            )
        if shadow.failed_ops_without_dlq > 0:
            detail.append(
                f"• {shadow.failed_ops_without_dlq} operations failed permanently (no DLQ)"
            )
        if shadow.drift_warnings_manual_only > 0:
            detail.append(
                f"• {shadow.drift_warnings_manual_only} drift warnings, manual resolution only"
            )
        detail.append(
            "_To adjust frequency: "
            "BALDUR_DAILY_REPORT_SHADOW_PRO_MODE=auto|daily|weekly|off_"
        )

    # Quiet-day short-circuit: no processing, no alerts, no incidents — i.e.
    # no section contributed any line.
    if not detail:
        lines.append("📊 All quiet — 0 processed, 0 alerts")
        return "\n".join(lines)

    lines.extend(detail)

    # Summary
    lines.extend(
        [
            "",
            f"📈 *Total processed: {total_operations}* | Task executions: {len(report.entries)}",
        ]
    )

    return "\n".join(lines)


def format_report_for_pagerduty(report: DailyAutonomousReport) -> str:
    """Format daily report for PagerDuty — actionable items only, English.

    DLQ stats intentionally excluded from summary — not a trigger condition.
    DLQ data is available in PD custom_details via report.to_dict() -> metadata.
    """
    items = []
    if report.error_budget_summary and report.error_budget_summary.blocks > 0:
        items.append(f"ErrorBudget: {report.error_budget_summary.blocks} blocks")
    if report.chaos_summary and report.chaos_summary.grade in ("D", "F"):
        chaos = report.chaos_summary
        items.append(
            f"Chaos grade {chaos.grade} ({chaos.experiments_failed}/{chaos.experiments_total} failed)"
        )
    if report.load_shedding_summary and report.load_shedding_summary.level in (
        "high",
        "critical",
    ):
        shed = report.load_shedding_summary
        items.append(f"LoadShedding {shed.level}: {shed.dropped_total} dropped")
    if report.critical_alerts > 0:
        items.append(f"{report.critical_alerts} critical alerts")
    if report.task_failures >= 5:
        items.append(f"{report.task_failures} task failures")
    return " | ".join(items)


__all__ = [
    "format_report_for_slack",
    "format_report_for_pagerduty",
]
