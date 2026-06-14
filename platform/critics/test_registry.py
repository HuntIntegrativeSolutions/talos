"""
Unit tests for the critic registry.

test_meta_critic_invariant_safety_class_not_waivable is the CI blocker:
the build must fail if someone registers a safety critic as waivable.

Each test that calls register() uses the isolated_registry fixture to avoid
contaminating the module-level singleton used by integration tests.
"""

from __future__ import annotations

import pytest

import platform.critics.registry as reg_module
from platform.critics.citations_resolvable import CriticResult
from platform.critics.registry import CriticSpec


@pytest.fixture()
def isolated_registry():
    """Save and restore the global _registry around each test."""
    original = dict(reg_module._registry)
    yield
    reg_module._registry.clear()
    reg_module._registry.update(original)


def _pass_critic(deliverable, nexus_client=None):
    return CriticResult(passed=True, reason="always passes")


def _fail_critic(deliverable, nexus_client=None):
    return CriticResult(passed=False, reason="always fails")


def test_meta_critic_invariant_safety_class_not_waivable(isolated_registry):
    with pytest.raises(ValueError, match="waivable=False"):
        reg_module.register(CriticSpec(
            name="bad_safety_critic",
            fn=_pass_critic,
            required=True,
            safety_class=True,
            waivable=True,
        ))


def test_register_safety_critic_non_waivable(isolated_registry):
    spec = CriticSpec(
        name="good_safety_critic",
        fn=_pass_critic,
        required=True,
        safety_class=True,
        waivable=False,
    )
    reg_module.register(spec)  # must not raise


def test_run_all_returns_verdict_per_critic(isolated_registry):
    reg_module.register(CriticSpec(
        name="test_pass_critic",
        fn=_pass_critic,
        required=True,
        safety_class=False,
        waivable=True,
    ))
    reg_module.register(CriticSpec(
        name="test_fail_critic",
        fn=_fail_critic,
        required=True,
        safety_class=False,
        waivable=True,
    ))
    results = reg_module.run_all({"citations": [{"finding_id": "X", "status": "confirmed"}]})
    by_name = {r["name"]: r for r in results}
    assert by_name["test_pass_critic"]["verdict"] == "pass"
    assert by_name["test_fail_critic"]["verdict"] == "fail"


def test_learned_critic_required_false(isolated_registry):
    reg_module.register(CriticSpec(
        name="advisory_critic",
        fn=_fail_critic,
        required=False,   # advisory — failure must not block the gate
        safety_class=False,
        waivable=True,
    ))
    results = reg_module.run_all({"citations": []})
    advisory = next(r for r in results if r["name"] == "advisory_critic")
    assert advisory["required"] is False
    assert advisory["verdict"] == "fail"
    # Gate satisfaction is computed by v_gate_status using required=False rows —
    # this test confirms the registry faithfully records required=False.
