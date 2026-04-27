from __future__ import annotations

import subprocess
from pathlib import Path

from agent_bridge.portable.installer import (
    calculate_install_plan,
    install_portable,
    verify_portable,
)


def make_target(tmp_path: Path) -> Path:
    target = tmp_path / "target-project"
    target.mkdir()
    (target / "source.txt").write_text("do not touch\n", encoding="utf-8")
    return target


def test_dry_run_does_not_copy_files(tmp_path: Path):
    target = make_target(tmp_path)

    plan = install_portable(target, dry_run=True)

    assert not plan.blocked
    assert not (target / ".agent-bridge").exists()
    assert not (target / "AGENTS.agent-bridge.snippet.md").exists()
    assert (target / "source.txt").read_text(encoding="utf-8") == "do not touch\n"


def test_install_copies_bridge_and_agents_snippet_by_default(tmp_path: Path):
    target = make_target(tmp_path)

    plan = install_portable(target)
    result = verify_portable(target)

    assert not plan.blocked
    assert (target / ".agent-bridge" / "README.md").exists()
    assert (target / "AGENTS.agent-bridge.snippet.md").exists()
    assert result.ok
    assert result.agents_snippet_present
    assert not (target / ".agent-bridge" / "workspace" / "queue" / "pending_commands.jsonl").exists()
    assert (target / "source.txt").read_text(encoding="utf-8") == "do not touch\n"


def test_existing_bridge_blocks_without_force(tmp_path: Path):
    target = make_target(tmp_path)
    install_portable(target)

    plan = install_portable(target)

    assert plan.blocked
    assert ".agent-bridge" in (plan.blocked_reason or "")


def test_force_overwrites_existing_bridge(tmp_path: Path):
    target = make_target(tmp_path)
    install_portable(target)
    stale = target / ".agent-bridge" / "stale.txt"
    stale.write_text("stale\n", encoding="utf-8")

    plan = install_portable(target, force=True)

    assert not plan.blocked
    assert not stale.exists()
    assert (target / ".agent-bridge" / "README.md").exists()


def test_missing_target_path_fails(tmp_path: Path):
    plan = calculate_install_plan(tmp_path / "missing")

    assert plan.blocked
    assert "does not exist" in (plan.blocked_reason or "")


def test_target_file_path_fails(tmp_path: Path):
    target_file = tmp_path / "target-file"
    target_file.write_text("not a directory\n", encoding="utf-8")

    plan = calculate_install_plan(target_file)

    assert plan.blocked
    assert "not a directory" in (plan.blocked_reason or "")


def test_missing_source_portable_module_fails(tmp_path: Path):
    target = make_target(tmp_path)
    source_root = tmp_path / "missing-source"

    plan = calculate_install_plan(target, source_root=source_root)

    assert plan.blocked
    assert "Source portable module is missing" in (plan.blocked_reason or "")


def test_dangerous_root_target_is_blocked():
    plan = calculate_install_plan(Path("/"))

    assert plan.blocked
    assert "filesystem root" in (plan.blocked_reason or "")


def test_install_without_agents_snippet_respects_flag(tmp_path: Path):
    target = make_target(tmp_path)

    plan = install_portable(target, include_agents_snippet=False)

    assert not plan.blocked
    assert (target / ".agent-bridge").exists()
    assert not (target / "AGENTS.agent-bridge.snippet.md").exists()


def test_installed_portable_self_test_runs_from_target_root(tmp_path: Path):
    target = make_target(tmp_path)
    install_portable(target)

    result = subprocess.run(
        ["bash", ".agent-bridge/scripts/self_test.sh"],
        cwd=target,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert "Portable Agent Bridge self-test completed." in result.stdout
    assert (target / ".agent-bridge" / "workspace" / "outbox" / "next_local_agent_prompt.md").exists()
    assert (target / "source.txt").read_text(encoding="utf-8") == "do not touch\n"
