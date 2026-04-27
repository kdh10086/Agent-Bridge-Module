from pathlib import Path
from agent_bridge.core.models import Command


class PromptBuilder:
    def __init__(self, template_dir: Path):
        self.template_dir = template_dir

    def _template(self, name: str) -> str:
        return (self.template_dir / name).read_text(encoding="utf-8")

    def build_local_agent_command(self, command: Command, payload: str) -> str:
        return self._template("codex_command_wrapper.md").format(
            command_type=command.type.value,
            source=command.source,
            payload=payload,
        )

    def build_pm_report_prompt(self, report: str) -> str:
        return self._template("pm_report_prompt.md").format(report=report)
