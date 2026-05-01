from __future__ import annotations

import json
import subprocess
from pathlib import Path

from typer.testing import CliRunner

import agent_bridge.cli as cli_module
from agent_bridge.gui.macos_apps import ManualStageTarget, load_gui_targets
from agent_bridge.gui.pm_backend import (
    PMAssistantBackend,
    merge_pm_target_override,
    preflight_pm_backend,
)


class FakeActivator:
    def __init__(self, *, should_succeed: bool = True):
        self.should_succeed = should_succeed
        self.calls: list[tuple[str, str | None, str | None]] = []

    def activate(
        self,
        app_name: str,
        *,
        app_path: str | None = None,
        bundle_id: str | None = None,
    ) -> None:
        self.calls.append((app_name, app_path, bundle_id))
        if not self.should_succeed:
            raise RuntimeError("activation failed")


class FakeDom:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.scripts: list[str] = []

    def evaluate_javascript(self, script: str) -> str:
        self.scripts.append(script)
        if self.fail:
            raise RuntimeError("Apple Events rejected JavaScript")
        if "document.readyState" in script:
            return "complete"
        return json.dumps(
            {
                "composer_empty": False,
                "send_ready": True,
                "streaming": False,
                "response_copy_ready": True,
                "copy_button_count": 1,
            }
        )


def target(**overrides) -> ManualStageTarget:
    defaults = {
        "app_name": "ChatGPT",
        "app_path": "/Applications/ChatGPT.app",
        "bundle_id": "com.openai.chat",
        "backend": "chrome_js",
        "require_backend_preflight": True,
        "window_hint": "ChatGPT",
    }
    defaults.update(overrides)
    return ManualStageTarget(**defaults)


def test_chrome_js_preflight_success():
    fake_dom = FakeDom()
    result = preflight_pm_backend(
        target=target(),
        activate=True,
        activator=FakeActivator(),
        dom_client_factory=lambda _: fake_dom,
        clipboard_tool_checker=lambda _: "/usr/bin/tool",
    )

    assert result.succeeded
    assert result.backend == PMAssistantBackend.CHROME_JS
    assert any(check.name == "dom_javascript" and check.succeeded for check in result.checks)
    assert any(check.name == "selector_query" and check.succeeded for check in result.checks)


def test_chrome_js_preflight_failure_reports_apple_events_hint():
    result = preflight_pm_backend(
        target=target(),
        activate=True,
        activator=FakeActivator(),
        dom_client_factory=lambda _: FakeDom(fail=True),
        clipboard_tool_checker=lambda _: "/usr/bin/tool",
    )

    assert not result.succeeded
    assert "Allow JavaScript from Apple Events" in (result.failure_reason or "")


def test_chatgpt_pwa_backend_is_not_selected_for_dom_js():
    fake_dom = FakeDom()

    result = preflight_pm_backend(
        target=target(backend="chatgpt_pwa_js"),
        activate=True,
        activator=FakeActivator(),
        dom_client_factory=lambda _: fake_dom,
        clipboard_tool_checker=lambda _: "/usr/bin/tool",
    )

    assert not result.succeeded
    assert "not supported for Chrome DOM JavaScript" in (result.failure_reason or "")
    assert fake_dom.scripts == []


def test_unsupported_backend_fails_without_dom_probe():
    fake_dom = FakeDom()

    result = preflight_pm_backend(
        target=target(backend="unsupported"),
        activate=False,
        dom_client_factory=lambda _: fake_dom,
        clipboard_tool_checker=lambda _: "/usr/bin/tool",
    )

    assert not result.succeeded
    assert "unsupported" in (result.failure_reason or "")
    assert fake_dom.scripts == []


def test_preflight_dry_run_does_not_activate_or_run_javascript():
    fake_dom = FakeDom()
    activator = FakeActivator()

    result = preflight_pm_backend(
        target=target(),
        dry_run=True,
        activate=True,
        activator=activator,
        dom_client_factory=lambda _: fake_dom,
        clipboard_tool_checker=lambda _: "/usr/bin/tool",
    )

    assert result.succeeded
    assert activator.calls == []
    assert fake_dom.scripts == []
    assert any(check.name == "dry_run" and check.skipped for check in result.checks)


def test_preflight_uses_nested_chrome_javascript_form(monkeypatch):
    captured: list[str] = []

    def fake_run(command, **kwargs):
        captured.append(command[2])
        if "document.readyState" in command[2]:
            return subprocess.CompletedProcess(command, 0, stdout="complete", stderr="")
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "composer_empty": False,
                    "send_ready": True,
                    "streaming": False,
                    "response_copy_ready": True,
                    "copy_button_count": 1,
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = preflight_pm_backend(
        target=target(app_name="Google Chrome", app_path=None, bundle_id=None),
        backend="chrome_js",
        activate=False,
        clipboard_tool_checker=lambda _: "/usr/bin/tool",
    )

    assert result.succeeded
    assert captured
    assert all("tell active tab of front window" in script for script in captured)
    assert all("in active tab of front window" not in script for script in captured)


def test_config_backend_is_loaded_and_override_preserves_metadata(tmp_path: Path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "default.yaml").write_text(
        """
apps:
  pm_assistant:
    app_name: "Chrome"
    backend: "chrome_js"
    require_backend_preflight: true
    window_hint: "ChatGPT"
""".lstrip(),
        encoding="utf-8",
    )
    (config_dir / "local.yaml").write_text(
        """
apps:
  pm_assistant:
    app_name: "ChatGPT"
    backend: "chatgpt_pwa_js"
""".lstrip(),
        encoding="utf-8",
    )

    loaded = load_gui_targets(config_dir).pm_assistant
    overridden = merge_pm_target_override(loaded, app_name="Google Chrome")

    assert loaded.app_name == "ChatGPT"
    assert loaded.backend == "chatgpt_pwa_js"
    assert loaded.require_backend_preflight
    assert overridden.app_name == "Google Chrome"
    assert overridden.backend == "chatgpt_pwa_js"


def test_preflight_pm_backend_cli_dry_run(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "workspace"
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "default.yaml").write_text(
        """
apps:
  pm_assistant:
    app_name: "ChatGPT"
    backend: "chrome_js"
    require_backend_preflight: true
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli_module, "WORKSPACE", workspace)
    monkeypatch.setattr(cli_module, "QUEUE_DIR", workspace / "queue")
    monkeypatch.setattr(cli_module, "STATE_PATH", workspace / "state" / "state.json")
    monkeypatch.setattr(cli_module, "LOG_PATH", workspace / "logs" / "bridge.jsonl")
    monkeypatch.setattr(cli_module, "CONFIG_DIR", config_dir)

    result = CliRunner().invoke(cli_module.app, ["preflight-pm-backend", "--dry-run"])

    assert result.exit_code == 0
    assert "PM Assistant Backend Preflight" in result.output
    assert "chrome_js" in result.output
    assert "Dry-run only" in result.output
