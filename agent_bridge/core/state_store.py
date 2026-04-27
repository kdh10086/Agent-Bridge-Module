from pathlib import Path
from agent_bridge.core.models import BridgeState


class StateStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> BridgeState:
        if not self.path.exists():
            return BridgeState()
        return BridgeState.model_validate_json(self.path.read_text(encoding="utf-8"))

    def save(self, state: BridgeState) -> None:
        state.touch()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(state.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def reset(self) -> BridgeState:
        state = BridgeState()
        self.save(state)
        return state
