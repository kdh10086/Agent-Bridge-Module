from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PORTABLE = ROOT / "portable_module" / ".agent-bridge"


def test_required_portable_scripts_docs_and_fixtures_exist():
    for script in [
        "self_test.sh",
        "ingest_review.sh",
        "ingest_ci.sh",
        "queue_list.sh",
        "dispatch_next.sh",
        "write_report.sh",
    ]:
        path = PORTABLE / "scripts" / script
        assert path.exists()
        assert path.stat().st_mode & 0o111

    for doc in [
        "OPERATING_MODEL.md",
        "TASK_BRIEF_TEMPLATE.md",
        "REPORT_TEMPLATE.md",
        "SAFETY.md",
    ]:
        assert (PORTABLE / "docs" / doc).exists()

    for fixture in [
        "fake_review_digest.md",
        "fake_ci_failure_digest.md",
        "risky_review_digest.md",
    ]:
        assert (PORTABLE / "fixtures" / fixture).exists()


def test_portable_workspace_layout_placeholders_exist():
    for subdir in ["state", "queue", "inbox", "outbox", "reports", "reviews", "logs"]:
        assert (PORTABLE / "workspace" / subdir).is_dir()
