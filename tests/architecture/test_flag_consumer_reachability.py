"""Impl 575 D4/D5/D8 — G32 dead-flag consumer-reachability guard.

A long-form ``*_enabled`` / ``enable_*`` settings flag whose every consumer read
is an echo-into-a-report-dict (or which has no read at all) advertises a
guarantee no production path gates on — the claim-wiring bug class
(``docs/laws/ADR-008_CLAIM_WIRING_INTEGRITY.md``). G32 classifies each long-form
flag's reads — static ``<obj>.F``, ``getattr(<obj>, "F", …)``, and string-key
dict reads (``<obj>["F"]`` / ``<obj>.get("F", …)``) across ``baldur`` +
``baldur_pro`` + ``baldur_dormant`` — into ``{gate | echo | ambiguous}`` and
flags a flag DEAD when it has no read or all reads are echo-subscript. The bare
``enabled`` master-toggle shape is excluded (D4-bare) — routed to the ADR-008
periodic audit.

Rule registry:
``docs/laws/ARCHITECTURAL_FITNESS_FUNCTIONS.md#g32-flag-consumer-reachability``
"""

from __future__ import annotations

import ast

import pytest

from tests.architecture._helpers import (
    classify_flag_in_source,
    classify_read,
    collect_long_form_flag_reads,
    collect_violations,
    discover_enable_fields,
    flag_is_dead,
    is_long_form_enable_field,
)
from tests.architecture.conftest import PROJECT_ROOT

_RULE_KEY = "flag_consumer_reachability"
_RULE_ANCHOR = "#g32-flag-consumer-reachability"


def _field_lineno(source_file: str, cls_name: str, field: str) -> int | None:
    """Best-effort ``AnnAssign`` line for a settings field (display only, D3).

    ``model_fields`` reflection exposes no source line, so G32 AST-derives it
    from the class source for a nicer message; ``None`` when not resolvable —
    the baseline match keys on ``(file, symbol)``, never the line.
    """
    path = PROJECT_ROOT / source_file
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == cls_name:
            for stmt in node.body:
                if (
                    isinstance(stmt, ast.AnnAssign)
                    and isinstance(stmt.target, ast.Name)
                    and stmt.target.id == field
                ):
                    return stmt.lineno
    return None


class TestFlagConsumerReachability:
    """575 D4 — no long-form enable flag is dead (no read, or echo-subscript only)."""

    def test_no_dead_long_form_flags(self):
        """Every long-form ``*_enabled`` flag has a gate/ambiguous consumer read.

        Burn-down: the current live dead set (surfaced by G32 itself, reviewed at
        generation per D6 to confirm no entry is a *live* flag) is allowlisted in
        ``baseline.yaml`` under ``flag_consumer_reachability:``. A NEW dead flag
        regresses on its first occurrence.
        """
        fields = [
            ef for ef in discover_enable_fields() if is_long_form_enable_field(ef.field)
        ]
        reads = collect_long_form_flag_reads({ef.field for ef in fields})
        raw: list[tuple] = []
        for ef in fields:
            classes = reads.get(ef.field, set())
            if not flag_is_dead(classes):
                continue
            verdict = "echo-only" if classes == {"echo"} else "no consumer read"
            raw.append(
                (
                    PROJECT_ROOT / ef.source_file,
                    _field_lineno(ef.source_file, ef.cls, ef.field),
                    f"{ef.cls}.{ef.field}",
                    f"dead long-form flag ({verdict}); advertised guarantee with no "
                    f"production gate — remove the flag (truth any compliance echo) "
                    f"or wire a consumer",
                )
            )
        violations = collect_violations(_RULE_KEY, raw, _RULE_ANCHOR)
        assert not violations, (
            f"{len(violations)} dead long-form enable flag(s) — a default-ON flag "
            f"no production path gates on (ADR-008 claim-wiring bug class). Remove "
            f"the flag or wire a behavioral consumer; if intentionally deferred, "
            f"baseline under `{_RULE_KEY}` with a ticket:\n" + "\n".join(violations)
        )


class TestFlagConsumerReachabilityAntiSilentPass:
    """G24-precedent: prove the classifier flags the dead shapes and clears live ones.

    Inline-fixture coverage so the gate cannot silently pass even if the live
    population is empty. ``F`` is the long-form flag name under test in each
    fixture source.
    """

    def test_none_dead_flagged(self):
        """A flag with no read anywhere is dead (the ``none`` class)."""
        source = "def f(cfg):\n    return cfg.other_attr\n"
        assert classify_flag_in_source(source, "feature_enabled") == set()
        assert flag_is_dead(set())

    def test_echo_subscript_only_dead_flagged(self):
        """A flag only echoed into a report subscript is dead (real `four_eyes_enabled` shape)."""
        source = 'def f(gov, details):\n    details["four_eyes_enabled"] = gov.four_eyes_enabled\n'
        classes = classify_flag_in_source(source, "four_eyes_enabled")
        assert classes == {"echo"}
        assert flag_is_dead(classes)

    def test_getattr_string_echo_flagged(self):
        """The getattr-echo variant (`d[k] = getattr(obj, "F", …)`) is also echo-dead."""
        source = 'def f(gov, details):\n    details["k"] = getattr(gov, "four_eyes_enabled", False)\n'
        classes = classify_flag_in_source(source, "four_eyes_enabled")
        assert classes == {"echo"}
        assert flag_is_dead(classes)

    def test_echo_and_gate_clean(self):
        """A flag both echoed AND gated is wired (not dead)."""
        source = (
            "def f(cfg, details):\n"
            '    details["x_enabled"] = cfg.x_enabled\n'
            "    if cfg.x_enabled:\n"
            "        return 1\n"
        )
        classes = classify_flag_in_source(source, "x_enabled")
        assert classes == {"echo", "gate"}
        assert not flag_is_dead(classes)

    def test_pure_gate_clean(self):
        """A flag gating a code path is wired (the real `graceful_degradation_enabled` shape)."""
        source = (
            "def f(self):\n"
            "    if not self._settings.graceful_degradation_enabled:\n"
            "        return True\n"
        )
        classes = classify_flag_in_source(source, "graceful_degradation_enabled")
        assert classes == {"gate"}
        assert not flag_is_dead(classes)

    def test_dict_literal_value_only_not_flagged(self):
        """A flag used only as a dict-literal value is ambiguous → not flagged (accepted-FN guard)."""
        source = (
            'def f(base):\n    return {"shadow_log_enabled": base.shadow_log_enabled}\n'
        )
        classes = classify_flag_in_source(source, "shadow_log_enabled")
        assert classes == {"ambiguous"}
        assert not flag_is_dead(classes)

    def test_getattr_string_return_not_flagged(self):
        """`return getattr(obj, "F", …)` is ambiguous → not flagged (live-flag FP guard)."""
        source = 'def is_enabled(settings):\n    return getattr(settings, "snapshot_logs_enabled", True)\n'
        classes = classify_flag_in_source(source, "snapshot_logs_enabled")
        assert classes == {"ambiguous"}
        assert not flag_is_dead(classes)

    def test_getattr_string_local_not_flagged(self):
        """`x = getattr(obj, "F", …)` (local assign) is ambiguous → not flagged."""
        source = 'def f(settings):\n    v = getattr(settings, "versioning_enabled", True)\n    return v\n'
        classes = classify_flag_in_source(source, "versioning_enabled")
        assert classes == {"ambiguous"}
        assert not flag_is_dead(classes)

    def test_string_key_dict_get_not_flagged(self):
        """`config.get("F")` (model_dump→runtime_config bridge) is a read → not flagged."""
        source = 'def f(config):\n    if not config.get("track1_enabled", True):\n        return\n'
        classes = classify_flag_in_source(source, "track1_enabled")
        assert classes == {"gate"}
        assert not flag_is_dead(classes)

    def test_flag_as_function_arg_inside_if_not_flagged(self):
        """`if is_active(s.F):` — function-arg role dominates the gate → ambiguous, not flagged."""
        source = "def f(s):\n    if is_active(s.feature_enabled):\n        return 1\n"
        classes = classify_flag_in_source(source, "feature_enabled")
        assert classes == {"ambiguous"}
        assert not flag_is_dead(classes)

    def test_bare_enabled_not_enumerated(self):
        """The bare ``enabled`` field shape is out of the long-form surface (D4-bare)."""
        assert not is_long_form_enable_field("enabled")
        assert is_long_form_enable_field("feature_enabled")
        assert is_long_form_enable_field("enable_dlx")

    def test_classify_read_priority_function_arg_over_gate(self):
        """`classify_read` returns the nearest classifying ancestor (function-arg over if)."""
        tree = ast.parse("if helper(s.feature_enabled):\n    pass\n")
        parent = {
            id(child): node
            for node in ast.walk(tree)
            for child in ast.iter_child_nodes(node)
        }
        read = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Attribute) and n.attr == "feature_enabled"
        )
        assert classify_read(read, parent) == "ambiguous"


# Read-shape × syntactic-context matrix. The three name-matched read shapes —
# static ``<obj>.F``, ``getattr(obj, "F", …)``, and string-key dict
# (``obj["F"]`` / ``obj.get("F", …)``) — MUST classify identically under the
# same enclosing context (575 D4/D5). The getattr-string and dict-key arms are
# load-bearing: without them a live flag consumed only dynamically (the
# ``model_dump()`` → runtime-config bridge, the ``is_enabled()`` getters)
# false-positives as ``none``-dead. ``F`` is always ``feature_enabled``.
def _wrap(body: str) -> str:
    """Wrap a one-statement body into a single-arg function for classification."""
    return f"def f(s, d, k, other):\n    {body}\n"


_GATE_BODIES = {
    "static_attr": "if s.feature_enabled:\n        return 1",
    "getattr_string": 'if getattr(s, "feature_enabled", True):\n        return 1',
    "subscript": 'if s["feature_enabled"]:\n        return 1',
    "dict_get": 'if s.get("feature_enabled", True):\n        return 1',
    "while_test": "while s.feature_enabled:\n        break",
    "boolop": "x = s.feature_enabled and other\n    return x",
    "assert_test": "assert s.feature_enabled",
}

_ECHO_BODIES = {
    "static_attr": "d[k] = s.feature_enabled",
    "getattr_string": 'd[k] = getattr(s, "feature_enabled", False)',
    "subscript": 'd[k] = s["feature_enabled"]',
    "dict_get": 'd[k] = s.get("feature_enabled", False)',
}

_AMBIGUOUS_BODIES = {
    "local_assign": "x = s.feature_enabled\n    return x",
    "return_attr": "return s.feature_enabled",
    "func_arg": "return helper(s.feature_enabled)",
    "dict_literal_value": 'return {"feature_enabled": s.feature_enabled}',
    "getattr_return": 'return getattr(s, "feature_enabled", True)',
    "getattr_local": 'x = getattr(s, "feature_enabled", True)\n    return x',
    "func_arg_inside_if": "if helper(s.feature_enabled):\n        return 1",
}


class TestFlagReadClassificationMatrix:
    """575 D4 — read-shape × syntactic-context → classification, exhaustively."""

    @pytest.mark.parametrize("shape", list(_GATE_BODIES), ids=list(_GATE_BODIES))
    def test_gate_context_classifies_gate_across_read_shapes(self, shape):
        classes = classify_flag_in_source(_wrap(_GATE_BODIES[shape]), "feature_enabled")
        assert classes == {"gate"}
        assert not flag_is_dead(classes)

    @pytest.mark.parametrize("shape", list(_ECHO_BODIES), ids=list(_ECHO_BODIES))
    def test_echo_context_classifies_echo_across_read_shapes(self, shape):
        classes = classify_flag_in_source(_wrap(_ECHO_BODIES[shape]), "feature_enabled")
        assert classes == {"echo"}
        assert flag_is_dead(classes)  # echo-only → dead

    @pytest.mark.parametrize(
        "shape", list(_AMBIGUOUS_BODIES), ids=list(_AMBIGUOUS_BODIES)
    )
    def test_ambiguous_context_not_flagged_across_read_shapes(self, shape):
        classes = classify_flag_in_source(
            _wrap(_AMBIGUOUS_BODIES[shape]), "feature_enabled"
        )
        assert classes == {"ambiguous"}
        assert not flag_is_dead(classes)  # conservatively not-dead (FN over FP, D4)

    def test_no_read_anywhere_is_none_dead(self):
        """A flag name absent from the source is the ``none`` class → dead."""
        classes = classify_flag_in_source(
            _wrap("return s.other_attr"), "feature_enabled"
        )
        assert classes == set()
        assert flag_is_dead(classes)


class TestFlagIsDeadContract:
    """575 D4 — dead iff every read-classification is in {echo} (or there are none)."""

    @pytest.mark.parametrize(
        ("classes", "expected_dead"),
        [
            (set(), True),  # none class
            ({"echo"}, True),  # echo-only
            ({"gate"}, False),
            ({"ambiguous"}, False),
            ({"echo", "gate"}, False),  # echo AND a real gate → wired
            ({"echo", "ambiguous"}, False),
            ({"gate", "ambiguous"}, False),
        ],
    )
    def test_flag_is_dead_predicate(self, classes, expected_dead):
        assert flag_is_dead(classes) is expected_dead
