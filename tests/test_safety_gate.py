from agent_bridge.core.safety_gate import SafetyGate


def test_safety_gate_blocks_hard_stop():
    decision = SafetyGate().check_text("This needs NEEDS_USER_DECISION before continuing.")
    assert not decision.allowed
    assert "NEEDS_USER_DECISION" in decision.matched_keywords


def test_safety_gate_allows_safe_text():
    decision = SafetyGate().check_text("Add unit tests and keep scope small.")
    assert decision.allowed
