import json
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PORTABLE = ROOT / "portable_module" / ".agent-bridge"


def copy_portable(tmp_path: Path) -> Path:
    project = tmp_path / "target-project"
    project.mkdir()
    shutil.copytree(PORTABLE, project / ".agent-bridge")
    (project / "README.md").write_text("target source placeholder\n", encoding="utf-8")
    return project


def run_script(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", *args],
        cwd=project,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def test_portable_self_test_runs_and_creates_ci_prompt(tmp_path: Path):
    project = copy_portable(tmp_path)

    result = run_script(project, ".agent-bridge/scripts/self_test.sh")

    prompt = project / ".agent-bridge" / "workspace" / "outbox" / "next_local_agent_prompt.md"
    queue = project / ".agent-bridge" / "workspace" / "queue" / "pending_commands.jsonl"
    assert "Portable Agent Bridge self-test completed." in result.stdout
    assert queue.exists()
    assert prompt.exists()
    assert "CI_FAILURE_FIX" in prompt.read_text(encoding="utf-8")
    assert (project / "README.md").read_text(encoding="utf-8") == "target source placeholder\n"


def test_portable_risky_digest_triggers_safety_pause(tmp_path: Path):
    project = copy_portable(tmp_path)
    workspace = project / ".agent-bridge" / "workspace"
    for path in [
        workspace / "queue" / "pending_commands.jsonl",
        workspace / "outbox" / "next_local_agent_prompt.md",
        workspace / "inbox" / "user_decision_request.md",
        workspace / "outbox" / "owner_decision_email.md",
    ]:
        path.unlink(missing_ok=True)

    run_script(
        project,
        ".agent-bridge/scripts/ingest_review.sh",
        ".agent-bridge/fixtures/risky_review_digest.md",
    )
    result = run_script(project, ".agent-bridge/scripts/dispatch_next.sh", "--dry-run")

    state = json.loads((workspace / "state" / "state.json").read_text(encoding="utf-8"))
    assert "Safety pause triggered" in result.stdout
    assert state["safety_pause"] is True
    assert "NEEDS_USER_DECISION" in state["matched_keywords"]
    assert (workspace / "inbox" / "user_decision_request.md").exists()
    assert (workspace / "outbox" / "owner_decision_email.md").exists()
    assert not (workspace / "outbox" / "next_local_agent_prompt.md").exists()
