"""Unit tests for framework-agnostic Runtime Config handlers (429 PR3-phase2a).

Target: ``baldur.api.handlers.config`` — generic get/update, reset, pending
changes, SLO special-case, logging runtime hot reload.

Verification techniques applied (§8):
  - §8.4 Side effects — runtime logger apply on logging PUT
  - §8.5 Dependency interaction — manager.update_with_strategy args
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from baldur.api.handlers.config import (
    _APPLY_OPTION_FIELDS,
    _split_apply_options,
    _status_to_http,
    all_config_get,
    cancel_pending_change,
    config_get,
    config_reset,
    config_update,
    logging_config_update,
    pending_changes_get,
    slo_config_delete,
    slo_config_update,
)
from baldur.interfaces.web_framework import HttpMethod, RequestContext


def _make_ctx(
    method="GET",
    path="/test/",
    query=None,
    path_params=None,
    json_body=None,
    user=None,
):
    return RequestContext(
        method=HttpMethod(method),
        path=path,
        query_params=query or {},
        path_params=path_params or {},
        json_body=json_body,
        user=user,
    )


# =============================================================================
# _split_apply_options — Contract
# =============================================================================


class TestSplitApplyOptionsContract:
    """Apply-option field names are the API design contract."""

    def test_apply_option_fields_expected_set(self):
        """Contract: apply options are apply_strategy + delay_seconds +
        grace_timeout_seconds + reason."""
        assert _APPLY_OPTION_FIELDS == {
            "apply_strategy",
            "delay_seconds",
            "grace_timeout_seconds",
            "reason",
        }

    def test_apply_options_extracted_from_body(self):
        body = {
            "apply_strategy": "delayed",
            "delay_seconds": 30,
            "grace_timeout_seconds": 120,
            "reason": "staged rollout",
            "max_retries": 5,
        }
        apply, changes = _split_apply_options(body)
        assert apply == {
            "strategy": "delayed",
            "delay_seconds": 30,
            "grace_timeout_seconds": 120,
            "reason": "staged rollout",
        }
        assert changes == {"max_retries": 5}

    def test_none_values_filtered_from_changes(self):
        """None values are not forwarded as config changes."""
        body = {"max_retries": 5, "timeout_ms": None}
        _, changes = _split_apply_options(body)
        assert changes == {"max_retries": 5}

    def test_missing_apply_keys_default_to_none_or_empty(self):
        apply, _ = _split_apply_options({"field": 1})
        assert apply["strategy"] is None
        assert apply["delay_seconds"] is None
        assert apply["grace_timeout_seconds"] is None
        assert apply["reason"] == ""


# =============================================================================
# _status_to_http — Contract (status→HTTP mapping is the design contract)
# =============================================================================


class TestStatusToHttpContract:
    def test_applied_maps_to_200(self):
        assert _status_to_http("applied") == 200

    def test_scheduled_maps_to_202(self):
        assert _status_to_http("scheduled") == 202

    def test_waiting_maps_to_202(self):
        assert _status_to_http("waiting") == 202

    def test_unknown_status_maps_to_400(self):
        assert _status_to_http("error") == 400
        assert _status_to_http("") == 400
        assert _status_to_http("bogus") == 400


# =============================================================================
# all_config_get / config_reset / pending_changes_get / cancel_pending_change
# =============================================================================


class TestAllConfigGetBehavior:
    def test_wraps_values_with_default_strategy(self):
        manager = MagicMock()
        manager.get_all_config.return_value = {"cb": {"max_retries": 3}}
        manager.get_default_strategy.return_value = {"strategy": "immediate"}
        manager.get_pending_changes.return_value = []
        with patch(
            "baldur_pro.services.runtime_config.get_runtime_config_manager",
            return_value=manager,
        ):
            resp = all_config_get(_make_ctx())
        assert resp.status_code == 200
        assert resp.body["config"]["cb"]["values"] == {"max_retries": 3}
        assert resp.body["config"]["cb"]["default_strategy"] == {
            "strategy": "immediate"
        }


class TestConfigResetBehavior:
    def test_invokes_reset_to_defaults(self):
        manager = MagicMock()
        manager.reset_to_defaults.return_value = {"cb": {}}
        with patch(
            "baldur_pro.services.runtime_config.get_runtime_config_manager",
            return_value=manager,
        ):
            resp = config_reset(
                _make_ctx(method="POST", user=SimpleNamespace(username="admin"))
            )
        manager.reset_to_defaults.assert_called_once()
        assert resp.status_code == 200


class TestPendingChangesGetBehavior:
    def test_forwards_config_type_query_param(self):
        manager = MagicMock()
        manager.get_pending_changes.return_value = [{"id": "p1"}]
        with patch(
            "baldur_pro.services.runtime_config.get_runtime_config_manager",
            return_value=manager,
        ):
            resp = pending_changes_get(_make_ctx(query={"config_type": "dlq"}))
        manager.get_pending_changes.assert_called_once_with("dlq")
        assert resp.body["count"] == 1


class TestCancelPendingChangeBehavior:
    def test_missing_pending_id_returns_400(self):
        with patch(
            "baldur_pro.services.runtime_config.get_runtime_config_manager"
        ) as mock_get:
            resp = cancel_pending_change(
                _make_ctx(method="POST", path_params={"pending_id": ""})
            )
        assert resp.status_code == 400
        mock_get.assert_not_called()

    def test_cancelled_status_returns_200(self):
        manager = MagicMock()
        manager.cancel_pending_change.return_value = {
            "status": "cancelled",
            "pending_id": "p1",
        }
        with patch(
            "baldur_pro.services.runtime_config.get_runtime_config_manager",
            return_value=manager,
        ):
            resp = cancel_pending_change(
                _make_ctx(method="POST", path_params={"pending_id": "p1"})
            )
        assert resp.status_code == 200
        assert resp.body["status"] == "cancelled"

    def test_not_cancelled_returns_404(self):
        manager = MagicMock()
        manager.cancel_pending_change.return_value = {
            "status": "error",
            "error": "not found",
        }
        with patch(
            "baldur_pro.services.runtime_config.get_runtime_config_manager",
            return_value=manager,
        ):
            resp = cancel_pending_change(
                _make_ctx(method="POST", path_params={"pending_id": "p1"})
            )
        assert resp.status_code == 404


# =============================================================================
# config_get / config_update (generic)
# =============================================================================


class TestConfigGetBehavior:
    def test_delegates_to_manager_get_config(self):
        manager = MagicMock()
        manager.get_config.return_value = {"max_retries": 3}
        manager.get_default_strategy.return_value = {"strategy": "immediate"}
        manager.get_pending_changes.return_value = []
        with patch(
            "baldur_pro.services.runtime_config.get_runtime_config_manager",
            return_value=manager,
        ):
            resp = config_get(_make_ctx(), config_name="dlq")
        manager.get_config.assert_called_once_with("dlq")
        assert resp.body["config_type"] == "dlq"
        assert resp.body["config"] == {"max_retries": 3}


class TestConfigUpdateBehavior:
    def test_empty_changes_returns_400(self):
        """Body with only apply-option fields -> 400 without invoking manager."""
        with patch(
            "baldur_pro.services.runtime_config.get_runtime_config_manager"
        ) as mock_get:
            resp = config_update(
                _make_ctx(
                    method="PUT",
                    json_body={"apply_strategy": "immediate", "reason": "x"},
                ),
                config_name="dlq",
            )
        assert resp.status_code == 400
        mock_get.assert_not_called()

    def test_applied_status_maps_to_200(self):
        manager = MagicMock()
        manager.update_with_strategy.return_value = {
            "status": "applied",
            "config": {},
        }
        with patch(
            "baldur_pro.services.runtime_config.get_runtime_config_manager",
            return_value=manager,
        ):
            resp = config_update(
                _make_ctx(method="PUT", json_body={"max_retries": 5}),
                config_name="dlq",
            )
        assert resp.status_code == 200

    def test_scheduled_status_maps_to_202(self):
        manager = MagicMock()
        manager.update_with_strategy.return_value = {"status": "scheduled"}
        with patch(
            "baldur_pro.services.runtime_config.get_runtime_config_manager",
            return_value=manager,
        ):
            resp = config_update(
                _make_ctx(
                    method="PUT",
                    json_body={
                        "max_retries": 5,
                        "apply_strategy": "delayed",
                        "delay_seconds": 30,
                    },
                ),
                config_name="dlq",
            )
        assert resp.status_code == 202

    def test_apply_options_forwarded_to_manager(self):
        manager = MagicMock()
        manager.update_with_strategy.return_value = {"status": "applied"}
        with patch(
            "baldur_pro.services.runtime_config.get_runtime_config_manager",
            return_value=manager,
        ):
            config_update(
                _make_ctx(
                    method="PUT",
                    json_body={
                        "max_retries": 5,
                        "apply_strategy": "graceful",
                        "grace_timeout_seconds": 120,
                    },
                    user=SimpleNamespace(username="admin"),
                ),
                config_name="dlq",
            )
        _, kwargs = manager.update_with_strategy.call_args
        assert kwargs["strategy"] == "graceful"
        assert kwargs["grace_timeout_seconds"] == 120
        assert kwargs["changed_by"] == "admin"


# =============================================================================
# logging_config_update
# =============================================================================


class TestLoggingConfigUpdateBehavior:
    """PUT /config/logging/ applies runtime logger changes after a successful
    update (283 DYNAMIC_LOG_LEVEL_API)."""

    def test_runtime_apply_invoked_on_success(self):
        """200 response -> _apply_component_log_levels is called."""
        manager = MagicMock()
        manager.update_with_strategy.return_value = {
            "status": "applied",
            "config": {},
        }

        with (
            patch(
                "baldur_pro.services.runtime_config.get_runtime_config_manager",
                return_value=manager,
            ),
            patch(
                "baldur.observability.structlog_config._apply_component_log_levels"
            ) as mock_apply,
            patch(
                "baldur.observability.structlog_config._COMPONENT_LOGGER_MAP",
                {"selfhealing": "baldur"},
            ),
            patch("baldur.settings.logging_settings.reset_logging_settings"),
            patch(
                "baldur.settings.logging_settings.get_logging_settings",
                return_value=MagicMock(selfhealing="DEBUG"),
            ),
        ):
            resp = logging_config_update(
                _make_ctx(method="PUT", json_body={"selfhealing": "DEBUG"})
            )

        assert resp.status_code == 200
        mock_apply.assert_called_once()

    def test_runtime_apply_graceful_on_failure(self):
        """Exception in runtime apply -> swallowed (logged), still returns 200."""
        manager = MagicMock()
        manager.update_with_strategy.return_value = {
            "status": "applied",
            "config": {},
        }

        with (
            patch(
                "baldur_pro.services.runtime_config.get_runtime_config_manager",
                return_value=manager,
            ),
            patch(
                "baldur.observability.structlog_config._apply_component_log_levels",
                side_effect=Exception("boom"),
            ),
            patch(
                "baldur.observability.structlog_config._COMPONENT_LOGGER_MAP",
                {"selfhealing": "baldur"},
            ),
            patch("baldur.settings.logging_settings.reset_logging_settings"),
            patch(
                "baldur.settings.logging_settings.get_logging_settings",
                return_value=MagicMock(selfhealing="DEBUG"),
            ),
        ):
            resp = logging_config_update(
                _make_ctx(method="PUT", json_body={"selfhealing": "DEBUG"})
            )

        # Exception inside runtime apply must not propagate
        assert resp.status_code == 200

    def test_runtime_apply_skipped_when_update_fails(self):
        """400 response -> runtime apply is NOT attempted."""
        manager = MagicMock()
        manager.update_with_strategy.return_value = {"status": "error"}

        with (
            patch(
                "baldur_pro.services.runtime_config.get_runtime_config_manager",
                return_value=manager,
            ),
            patch(
                "baldur.observability.structlog_config._apply_component_log_levels"
            ) as mock_apply,
        ):
            resp = logging_config_update(
                _make_ctx(method="PUT", json_body={"selfhealing": "DEBUG"})
            )

        assert resp.status_code == 400
        mock_apply.assert_not_called()


# =============================================================================
# SLO special-case handlers
# =============================================================================


class TestSloConfigUpdateBehavior:
    def test_forwards_slo_fields(self):
        manager = MagicMock()
        manager.update_slo_config.return_value = {"slos": {}}
        with patch(
            "baldur_pro.services.runtime_config.get_runtime_config_manager",
            return_value=manager,
        ):
            resp = slo_config_update(
                _make_ctx(
                    method="PUT",
                    json_body={
                        "default_target": 0.999,
                        "slo": {"name": "api_latency", "target": 0.99},
                    },
                    user=SimpleNamespace(username="admin"),
                )
            )
        _, kwargs = manager.update_slo_config.call_args
        assert kwargs["default_target"] == 0.999
        assert kwargs["slo"] == {"name": "api_latency", "target": 0.99}
        assert resp.status_code == 200


class TestSloConfigDeleteBehavior:
    def test_missing_name_query_returns_400(self):
        with patch(
            "baldur_pro.services.runtime_config.get_runtime_config_manager"
        ) as mock_get:
            resp = slo_config_delete(_make_ctx(method="DELETE"))
        assert resp.status_code == 400
        mock_get.assert_not_called()

    def test_deleted_status_returns_200(self):
        manager = MagicMock()
        manager.delete_slo.return_value = {
            "status": "deleted",
            "slo_name": "api_latency",
        }
        with patch(
            "baldur_pro.services.runtime_config.get_runtime_config_manager",
            return_value=manager,
        ):
            resp = slo_config_delete(
                _make_ctx(method="DELETE", query={"name": "api_latency"})
            )
        assert resp.status_code == 200

    def test_missing_slo_returns_404(self):
        manager = MagicMock()
        manager.delete_slo.return_value = {"status": "error"}
        with patch(
            "baldur_pro.services.runtime_config.get_runtime_config_manager",
            return_value=manager,
        ):
            resp = slo_config_delete(
                _make_ctx(method="DELETE", query={"name": "missing"})
            )
        assert resp.status_code == 404
