from pathlib import Path
from agent_bridge.core.models import SafetyDecision


DEFAULT_HARD_STOP_KEYWORDS = [
    "NEEDS_USER_DECISION",
    "APPROVAL_REQUIRED",
    "RISK_HIGH",
    "PAID_API",
    "LICENSE_UNKNOWN",
    "PRIVACY_RISK",
    "MAIN_MERGE",
    "DATA_MIGRATION",
    "ARCHITECTURE_CHANGE",
    "DELETE_OR_REWRITE_LARGE_SCOPE",
    "CI_FAILED_REPEATEDLY",
    "MAX_CYCLE_REACHED",
]


class SafetyGate:
    def __init__(self, hard_stop_keywords: list[str] | None = None):
        self.hard_stop_keywords = hard_stop_keywords or DEFAULT_HARD_STOP_KEYWORDS

    def check_text(self, text: str) -> SafetyDecision:
        upper = text.upper()
        matched = [kw for kw in self.hard_stop_keywords if kw.upper() in upper]
        if matched:
            return SafetyDecision(
                allowed=False,
                reason="Hard-stop keyword detected.",
                matched_keywords=matched,
            )
        return SafetyDecision(allowed=True)

    def write_decision_request(self, workspace_dir: Path, decision: SafetyDecision, source_text: str) -> None:
        inbox = workspace_dir / "inbox"
        outbox = workspace_dir / "outbox"
        inbox.mkdir(parents=True, exist_ok=True)
        outbox.mkdir(parents=True, exist_ok=True)
        (inbox / "user_decision_request.md").write_text(
            "# User Decision Required\n\n"
            f"## Reason\n\n{decision.reason}\n\n"
            f"## Matched Keywords\n\n{', '.join(decision.matched_keywords)}\n\n"
            f"## Source Text\n\n{source_text}\n",
            encoding="utf-8",
        )
        (outbox / "owner_decision_email.md").write_text(
            "Subject: [Agent Bridge Decision Required] Automation Paused\n\n"
            "## Summary\n\nAgent Bridge paused because a safety gate was triggered.\n\n"
            f"## Matched Keywords\n\n{', '.join(decision.matched_keywords)}\n",
            encoding="utf-8",
        )
