from pathlib import Path
from agent_bridge.core.models import BridgeStateName
from agent_bridge.core.state_store import StateStore


def test_state_store_roundtrip(tmp_path: Path):
    store = StateStore(tmp_path / "state.json")
    state = store.load()
    state.state = BridgeStateName.TASK_READY
    state.current_task_id = "T-1"
    store.save(state)

    loaded = store.load()
    assert loaded.state == BridgeStateName.TASK_READY
    assert loaded.current_task_id == "T-1"
