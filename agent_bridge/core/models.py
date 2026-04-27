from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class CommandType(str, Enum):
    CHATGPT_PM_NEXT_TASK = "CHATGPT_PM_NEXT_TASK"
    GITHUB_REVIEW_FIX = "GITHUB_REVIEW_FIX"
    CI_FAILURE_FIX = "CI_FAILURE_FIX"
    USER_MANUAL_COMMAND = "USER_MANUAL_COMMAND"
    REQUEST_STATUS_REPORT = "REQUEST_STATUS_REPORT"
    STOP_AND_REPORT = "STOP_AND_REPORT"


COMMAND_PRIORITIES: dict[CommandType, int] = {
    CommandType.STOP_AND_REPORT: 100,
    CommandType.USER_MANUAL_COMMAND: 95,
    CommandType.CI_FAILURE_FIX: 80,
    CommandType.GITHUB_REVIEW_FIX: 70,
    CommandType.CHATGPT_PM_NEXT_TASK: 50,
    CommandType.REQUEST_STATUS_REPORT: 40,
}


class CommandStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


class BridgeStateName(str, Enum):
    IDLE = "IDLE"
    TASK_READY = "TASK_READY"
    WAIT_LOCAL_AGENT_REPORT = "WAIT_LOCAL_AGENT_REPORT"
    WAIT_PM_RESPONSE = "WAIT_PM_RESPONSE"
    QUEUE_READY = "QUEUE_READY"
    DISPATCHING = "DISPATCHING"
    PAUSED_FOR_USER_DECISION = "PAUSED_FOR_USER_DECISION"
    ERROR_RECOVERY = "ERROR_RECOVERY"


class Command(BaseModel):
    id: str
    type: CommandType
    priority: int | None = Field(default=None, validate_default=True)
    source: str = "unknown"
    created_at: str = Field(default_factory=utc_now_iso)
    task_id: str | None = None
    pr_number: int | None = None
    payload_path: str
    requires_user_approval: bool = False
    safety_flags: list[str] = Field(default_factory=list)
    dedupe_key: str
    status: CommandStatus = CommandStatus.PENDING
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("priority", mode="before")
    @classmethod
    def default_priority(cls, value, info):
        if value is not None:
            return value
        command_type = info.data.get("type")
        if isinstance(command_type, CommandType):
            return COMMAND_PRIORITIES[command_type]
        if isinstance(command_type, str):
            return COMMAND_PRIORITIES[CommandType(command_type)]
        return 0


class BridgeState(BaseModel):
    current_task_id: str | None = None
    current_pr: int | None = None
    state: BridgeStateName = BridgeStateName.IDLE
    cycle: int = 0
    max_cycles: int = 5
    max_runtime_seconds: int = 3600
    loop_started_at: str | None = None
    last_loop_event: str | None = None
    last_seen_review_ids: list[str] = Field(default_factory=list)
    last_seen_comment_ids: list[str] = Field(default_factory=list)
    last_seen_check_run_ids: list[str] = Field(default_factory=list)
    safety_pause: bool = False
    last_error: str | None = None
    started_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)

    def touch(self) -> None:
        self.updated_at = utc_now_iso()


class SafetyDecision(BaseModel):
    allowed: bool
    reason: str = ""
    matched_keywords: list[str] = Field(default_factory=list)


class ReviewActionItem(BaseModel):
    title: str
    severity: str = "unknown"
    file: str | None = None
    line: int | None = None
    original_comment: str = ""
    suggested_local_agent_action: str = ""
    requires_user_decision: bool = False


class ReviewDigest(BaseModel):
    source: str = "file_fixture"
    repository: str | None = None
    pr_number: int | None = None
    review_id: str | None = None
    detected_at: str = Field(default_factory=utc_now_iso)
    summary: str = ""
    action_items: list[ReviewActionItem] = Field(default_factory=list)
    raw_source_path: str | None = None
    dedupe_key: str


class CIFailureItem(BaseModel):
    job_name: str = ""
    step_name: str | None = None
    status: str = "failed"
    error_excerpt: str = ""
    suspected_cause: str = ""
    suggested_local_agent_action: str = ""
    requires_user_decision: bool = False


class CIDigest(BaseModel):
    source: str = "file_fixture"
    repository: str | None = None
    pr_number: int | None = None
    check_run_id: str | None = None
    detected_at: str = Field(default_factory=utc_now_iso)
    summary: str = ""
    failures: list[CIFailureItem] = Field(default_factory=list)
    raw_source_path: str | None = None
    dedupe_key: str
