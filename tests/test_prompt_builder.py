from pathlib import Path
from agent_bridge.codex.prompt_builder import PromptBuilder
from agent_bridge.core.models import Command, CommandType


def test_prompt_builder_wraps_command(tmp_path: Path):
    template_dir = tmp_path / "templates"
    template_dir.mkdir()
    (template_dir / "codex_command_wrapper.md").write_text("Type={command_type}\nSource={source}\nPayload={payload}", encoding="utf-8")
    builder = PromptBuilder(template_dir)
    command = Command(id="cmd", type=CommandType.CHATGPT_PM_NEXT_TASK, source="test", payload_path="payload.md", dedupe_key="k")
    prompt = builder.build_local_agent_command(command, "hello")
    assert "CHATGPT_PM_NEXT_TASK" in prompt
    assert "hello" in prompt
