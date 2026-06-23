"""
Unit tests for #412 Daily Report Data Completeness — Formatter layer.

Test targets:
  - format_report_for_slack() — adaptive DLQ/Chaos/LS/EB sections
  - format_report_for_pagerduty() — actionable items English summary
"""

from __future__ import annotations

from baldur.services.daily_report.formatters import (
    format_report_for_pagerduty,
    format_report_for_slack,
)
from baldur.services.daily_report.models import (
    ChaosReportSummary,
    DailyAutonomousReport,
    ErrorBudgetGateSummary,
    LoadSheddingSummary,
)

# =============================================================================
# format_report_for_slack() — Behavior Tests
# =============================================================================


class TestSlackFormatterAdaptiveSectionsBehavior:
    """Verify Slack formatter renders sections conditionally."""

    def test_dlq_section_shown_when_pending_count_nonzero(self):
        """DLQ section renders when dlq_pending_count > 0 (new 428 compact format)."""
        report = DailyAutonomousReport()
        report.dlq_pending_count = 5

        result = format_report_for_slack(report)

        assert "📬 DLQ:" in result
        assert "5 pending" in result

    def test_dlq_section_hidden_when_all_zero(self):
        """DLQ section omitted when all DLQ counters are 0."""
        report = DailyAutonomousReport()

        result = format_report_for_slack(report)

        assert "DLQ" not in result

    def test_dlq_sub_line_shown_when_resolution_details_exist(self):
        """DLQ sub-line (manual/TTL/retries) shown when any > 0."""
        report = DailyAutonomousReport()
        report.dlq_new_entries_count = 1
        report.dlq_manual_resolutions = 3
        report.dlq_ttl_expired = 2

        result = format_report_for_slack(report)

        assert "Manual 3" in result
        assert "TTL expired 2" in result

    def test_dlq_sub_line_hidden_when_no_resolution_details(self):
        """DLQ sub-line omitted when manual/TTL/retries all 0."""
        report = DailyAutonomousReport()
        report.dlq_new_entries_count = 1

        result = format_report_for_slack(report)

        assert "📬 DLQ:" in result
        assert "Manual" not in result

    def test_chaos_section_shown_when_summary_present(self):
        """Chaos section renders when chaos_summary is set."""
        report = DailyAutonomousReport()
        report.chaos_summary = ChaosReportSummary(
            grade="B",
            grade_trend="improving",
            experiments_total=10,
            experiments_passed=8,
        )

        result = format_report_for_slack(report)

        assert "Chaos Resilience" in result
        assert "Grade: B (improving)" in result
        assert "8/10 passed" in result

    def test_chaos_section_hidden_when_summary_none(self):
        """Chaos section omitted when chaos_summary is None."""
        report = DailyAutonomousReport()

        result = format_report_for_slack(report)

        assert "Chaos" not in result

    def test_chaos_sla_sub_line_shown_when_sla_breaches(self):
        """Chaos SLA sub-line shown when sla_breaches > 0."""
        report = DailyAutonomousReport()
        report.chaos_summary = ChaosReportSummary(
            grade="C",
            grade_trend="declining",
            experiments_total=5,
            sla_breaches=2,
            error_budget_consumed_pct=15.5,
        )

        result = format_report_for_slack(report)

        assert "SLA breaches: 2" in result
        assert "15.5%" in result

    def test_load_shedding_section_shown_when_summary_present(self):
        """Load shedding section renders when load_shedding_summary is set."""
        report = DailyAutonomousReport()
        report.load_shedding_summary = LoadSheddingSummary(
            level="medium",
            dropped_total=100,
            processed_total=900,
        )

        result = format_report_for_slack(report)

        assert "Load Shedding" in result
        assert "Level: medium" in result
        assert "Dropped: 100" in result

    def test_load_shedding_tier_breakdown_shown_when_present(self):
        """LS tier breakdown sub-line shown when dropped_by_tier has values."""
        report = DailyAutonomousReport()
        report.load_shedding_summary = LoadSheddingSummary(
            level="high",
            dropped_total=50,
            processed_total=500,
            dropped_by_tier={"non_essential": 30, "standard": 20},
        )

        result = format_report_for_slack(report)

        assert "By tier:" in result
        assert "non_essential: 30" in result

    def test_error_budget_section_shown_when_summary_present(self):
        """Error budget section renders with block and warning counts."""
        report = DailyAutonomousReport()
        report.error_budget_summary = ErrorBudgetGateSummary(blocks=4, warnings=12)

        result = format_report_for_slack(report)

        assert "Error Budget Gate" in result
        assert "4 blocks" in result
        assert "12 warnings" in result

    def test_error_budget_section_hidden_when_summary_none(self):
        """Error budget section omitted when error_budget_summary is None."""
        report = DailyAutonomousReport()

        result = format_report_for_slack(report)

        assert "Error Budget" not in result

    def test_all_sections_rendered_simultaneously(self):
        """All adaptive sections render together without interference."""
        report = DailyAutonomousReport()
        report.dlq_pending_count = 5
        report.dlq_new_entries_count = 3
        report.chaos_summary = ChaosReportSummary(grade="A", grade_trend="stable")
        report.load_shedding_summary = LoadSheddingSummary(level="none")
        report.error_budget_summary = ErrorBudgetGateSummary(blocks=1, warnings=2)

        result = format_report_for_slack(report)

        assert "DLQ" in result
        assert "Chaos" in result
        assert "Load Shedding" in result
        assert "Error Budget" in result


# =============================================================================
# format_report_for_pagerduty() — Behavior Tests
# =============================================================================


class TestPagerDutyFormatterBehavior:
    """Verify PagerDuty formatter outputs actionable items in English."""

    def test_empty_report_returns_empty_string(self):
        """Empty report with no actionable items returns empty string."""
        report = DailyAutonomousReport()

        result = format_report_for_pagerduty(report)

        assert result == ""

    def test_error_budget_blocks_included(self):
        """ErrorBudget blocks appear in PD summary."""
        report = DailyAutonomousReport()
        report.error_budget_summary = ErrorBudgetGateSummary(blocks=3)

        result = format_report_for_pagerduty(report)

        assert "ErrorBudget: 3 blocks" in result

    def test_chaos_grade_d_or_f_included(self):
        """Chaos grade D or F appears in PD summary."""
        report = DailyAutonomousReport()
        report.chaos_summary = ChaosReportSummary(
            grade="F",
            experiments_total=10,
            experiments_failed=7,
        )

        result = format_report_for_pagerduty(report)

        assert "Chaos grade F" in result
        assert "7/10 failed" in result

    def test_chaos_grade_a_excluded(self):
        """Chaos grade A is not actionable — excluded from PD summary."""
        report = DailyAutonomousReport()
        report.chaos_summary = ChaosReportSummary(grade="A")

        result = format_report_for_pagerduty(report)

        assert "Chaos" not in result

    def test_load_shedding_high_or_critical_included(self):
        """Load shedding high/critical level appears in PD summary."""
        report = DailyAutonomousReport()
        report.load_shedding_summary = LoadSheddingSummary(
            level="critical",
            dropped_total=500,
        )

        result = format_report_for_pagerduty(report)

        assert "LoadShedding critical" in result
        assert "500 dropped" in result

    def test_load_shedding_low_excluded(self):
        """Load shedding low level is not actionable — excluded."""
        report = DailyAutonomousReport()
        report.load_shedding_summary = LoadSheddingSummary(level="low")

        result = format_report_for_pagerduty(report)

        assert "LoadShedding" not in result

    def test_critical_alerts_included(self):
        """Critical alerts count appears in PD summary."""
        report = DailyAutonomousReport()
        report.critical_alerts = 3

        result = format_report_for_pagerduty(report)

        assert "3 critical alerts" in result

    def test_task_failures_below_threshold_excluded(self):
        """Task failures < 5 are not actionable — excluded."""
        report = DailyAutonomousReport()
        report.task_failures = 4

        result = format_report_for_pagerduty(report)

        assert "task failures" not in result

    def test_task_failures_at_threshold_included(self):
        """Task failures >= 5 appear in PD summary."""
        report = DailyAutonomousReport()
        report.task_failures = 5

        result = format_report_for_pagerduty(report)

        assert "5 task failures" in result

    def test_multiple_items_joined_with_pipe(self):
        """Multiple actionable items are joined with ' | '."""
        report = DailyAutonomousReport()
        report.critical_alerts = 2
        report.task_failures = 10
        report.error_budget_summary = ErrorBudgetGateSummary(blocks=1)

        result = format_report_for_pagerduty(report)

        assert " | " in result
        assert result.count(" | ") == 2  # 3 items = 2 pipes

    def test_dlq_excluded_from_pagerduty(self):
        """DLQ stats intentionally excluded from PD summary (not a trigger condition)."""
        report = DailyAutonomousReport()
        report.dlq_pending_count = 100
        report.dlq_new_entries_count = 50

        result = format_report_for_pagerduty(report)

        assert "DLQ" not in result
        assert "dlq" not in result
