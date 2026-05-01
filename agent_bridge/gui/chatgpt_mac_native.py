from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from typing import Callable

from agent_bridge.gui.macos_apps import (
    CHATGPT_CHROME_APP_PROFILE,
    CHATGPT_MAC_PROFILE,
    ActivationResult,
    CHROME_BUNDLE_ID_PREFIXES,
    NATIVE_CHATGPT_MAC_BUNDLE_ID,
    MacOSAppActivator,
    ManualStageTarget,
    ensure_native_chatgpt_mac_target,
    format_activation_result,
    is_chatgpt_chrome_app_candidate_bundle,
    is_rejected_chatgpt_candidate_bundle,
    normalize_pm_target_profile,
)

Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class ChatGPTAppCandidate:
    name: str
    bundle_id: str
    frontmost: bool | None
    visible: bool | None
    window_count: int | None
    pid: str | None = None
    window_summaries: tuple[str, ...] = ()
    selected: bool = False
    rejected: bool = False
    rejection_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class ChatGPTAppTargetDiagnostic:
    expected_bundle_id: str
    configured_app_name: str
    configured_bundle_id: str | None
    selected_bundle_id: str | None
    native_available: bool
    chrome_pwa_candidates_rejected: int
    candidates: tuple[ChatGPTAppCandidate, ...]
    selected_profile: str = CHATGPT_MAC_PROFILE
    chrome_app_available: bool = False
    native_candidates_rejected: int = 0
    activation_attempted: bool = False
    activation_bundle_id: str | None = None
    activation_succeeded: bool | None = None
    activation_error: str | None = None
    reenumerated_after_activation: bool = False
    error: str | None = None


@dataclass(frozen=True)
class ChatGPTNativePreflightResult:
    target: ManualStageTarget
    activation_result: ActivationResult
    app_targets: ChatGPTAppTargetDiagnostic
    selected_native_bundle_id: str | None
    activation_method: str | None
    succeeded: bool
    error: str | None = None


@dataclass(frozen=True)
class AppWindowBoundsResult:
    target_app: str
    target_bundle_id: str | None
    requested_bounds: tuple[int, int, int, int]
    before_bounds: tuple[int, int, int, int] | None
    after_bounds: tuple[int, int, int, int] | None
    succeeded: bool
    error: str | None = None


def diagnose_chatgpt_app_targets(
    *,
    target: ManualStageTarget,
    profile: str | None = None,
    runner: Runner = subprocess.run,
    osascript_executable: str = "osascript",
    open_executable: str = "open",
    activate_chrome_app_if_needed: bool = True,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> ChatGPTAppTargetDiagnostic:
    selected_profile = normalize_pm_target_profile(profile or target.profile)
    native_target = (
        ensure_native_chatgpt_mac_target(target)
        if selected_profile == CHATGPT_MAC_PROFILE
        else target
    )
    completed = _run_target_enumeration(runner, osascript_executable)
    if completed.returncode != 0:
        output = (completed.stderr or completed.stdout or "").strip()
        return ChatGPTAppTargetDiagnostic(
            selected_profile=selected_profile,
            expected_bundle_id=_expected_bundle_id_for_profile(selected_profile, native_target),
            configured_app_name=native_target.app_name,
            configured_bundle_id=native_target.bundle_id,
            selected_bundle_id=None,
            native_available=False,
            chrome_pwa_candidates_rejected=0,
            candidates=(),
            error=output or "ChatGPT app target enumeration failed.",
        )
    candidates = tuple(_parse_app_candidates(completed.stdout))
    activation_attempted = False
    activation_bundle_id: str | None = None
    activation_succeeded: bool | None = None
    activation_error: str | None = None
    reenumerated_after_activation = False
    evaluation = _evaluate_candidates(
        candidates=candidates,
        selected_profile=selected_profile,
        target=native_target,
    )
    if (
        selected_profile == CHATGPT_CHROME_APP_PROFILE
        and evaluation.selected_bundle_id is None
        and activate_chrome_app_if_needed
    ):
        activation_candidate = _chrome_app_candidate_for_activation(candidates, native_target)
        if activation_candidate is not None:
            activation_attempted = True
            activation_bundle_id = activation_candidate.bundle_id
            activation = runner(
                [open_executable, "-b", activation_candidate.bundle_id],
                check=False,
                text=True,
                capture_output=True,
            )
            activation_succeeded = activation.returncode == 0
            if activation.returncode != 0:
                activation_error = (activation.stderr or activation.stdout or "").strip()
            else:
                sleep_fn(1.0)
                repeated = _run_target_enumeration(runner, osascript_executable)
                if repeated.returncode == 0:
                    reenumerated_after_activation = True
                    candidates = tuple(_parse_app_candidates(repeated.stdout))
                    evaluation = _evaluate_candidates(
                        candidates=candidates,
                        selected_profile=selected_profile,
                        target=native_target,
                    )
                else:
                    activation_error = (
                        (repeated.stderr or repeated.stdout or "").strip()
                        or "ChatGPT app target re-enumeration failed after activation."
                    )
    return ChatGPTAppTargetDiagnostic(
        selected_profile=selected_profile,
        expected_bundle_id=_expected_bundle_id_for_profile(selected_profile, native_target),
        configured_app_name=native_target.app_name,
        configured_bundle_id=native_target.bundle_id,
        selected_bundle_id=evaluation.selected_bundle_id,
        native_available=evaluation.native_available,
        chrome_pwa_candidates_rejected=evaluation.chrome_pwa_candidates_rejected,
        chrome_app_available=(
            selected_profile == CHATGPT_CHROME_APP_PROFILE
            and evaluation.selected_chrome_bundle_id is not None
        ),
        native_candidates_rejected=evaluation.native_candidates_rejected,
        candidates=evaluation.evaluated,
        activation_attempted=activation_attempted,
        activation_bundle_id=activation_bundle_id,
        activation_succeeded=activation_succeeded,
        activation_error=activation_error,
        reenumerated_after_activation=reenumerated_after_activation,
        error=(
            activation_error
            if activation_error and evaluation.selected_bundle_id is None
            else _target_error_for_profile(selected_profile, evaluation.selected_bundle_id)
        ),
    )


@dataclass(frozen=True)
class _CandidateEvaluation:
    evaluated: tuple[ChatGPTAppCandidate, ...]
    selected_bundle_id: str | None
    selected_chrome_bundle_id: str | None
    native_available: bool
    chrome_pwa_candidates_rejected: int
    native_candidates_rejected: int


def _evaluate_candidates(
    *,
    candidates: tuple[ChatGPTAppCandidate, ...],
    selected_profile: str,
    target: ManualStageTarget,
) -> _CandidateEvaluation:
    selected_bundle_id = None
    selected_chrome_bundle_id = None
    evaluated: list[ChatGPTAppCandidate] = []
    chrome_candidates = sorted(
        (
            candidate
            for candidate in candidates
            if is_chatgpt_chrome_app_candidate_bundle(candidate.bundle_id)
            and _candidate_matches_configured_bundle(candidate, target)
            and _candidate_has_windows(candidate)
        ),
        key=_chrome_app_candidate_sort_key,
        reverse=True,
    )
    for candidate in candidates:
        reasons: list[str] = []
        selected = False
        if selected_profile == CHATGPT_MAC_PROFILE:
            if candidate.bundle_id == NATIVE_CHATGPT_MAC_BUNDLE_ID:
                selected = selected_bundle_id is None
                selected_bundle_id = selected_bundle_id or candidate.bundle_id
            elif is_rejected_chatgpt_candidate_bundle(candidate.bundle_id):
                reasons.append("chrome_or_pwa_bundle_rejected")
            else:
                reasons.append("not_native_chatgpt_bundle")
        else:
            if candidate.bundle_id == NATIVE_CHATGPT_MAC_BUNDLE_ID:
                reasons.append("native_chatgpt_bundle_rejected")
            elif is_chatgpt_chrome_app_candidate_bundle(candidate.bundle_id):
                if not _candidate_matches_configured_bundle(candidate, target):
                    reasons.append("not_configured_chrome_app_bundle")
                elif not _candidate_has_windows(candidate):
                    reasons.append("no_visible_chrome_app_window")
                else:
                    selected = (
                        selected_chrome_bundle_id is None
                        and bool(chrome_candidates)
                        and candidate == chrome_candidates[0]
                    )
                    if selected:
                        selected_chrome_bundle_id = candidate.bundle_id
                        selected_bundle_id = candidate.bundle_id
            elif is_rejected_chatgpt_candidate_bundle(candidate.bundle_id):
                reasons.append("chrome_browser_bundle_rejected")
            else:
                reasons.append("not_chrome_app_bundle")
        evaluated.append(
            ChatGPTAppCandidate(
                name=candidate.name,
                bundle_id=candidate.bundle_id,
                pid=candidate.pid,
                frontmost=candidate.frontmost,
                visible=candidate.visible,
                window_count=candidate.window_count,
                window_summaries=candidate.window_summaries,
                selected=selected,
                rejected=bool(reasons),
                rejection_reasons=tuple(reasons),
            )
        )
    return _CandidateEvaluation(
        evaluated=tuple(evaluated),
        selected_bundle_id=selected_bundle_id,
        selected_chrome_bundle_id=selected_chrome_bundle_id,
        native_available=any(
            candidate.bundle_id == NATIVE_CHATGPT_MAC_BUNDLE_ID for candidate in candidates
        ),
        chrome_pwa_candidates_rejected=sum(
            1
            for candidate in evaluated
            if any(reason == "chrome_or_pwa_bundle_rejected" for reason in candidate.rejection_reasons)
        ),
        native_candidates_rejected=sum(
            1
            for candidate in evaluated
            if any(reason == "native_chatgpt_bundle_rejected" for reason in candidate.rejection_reasons)
        ),
    )


def preflight_chatgpt_mac_native_target(
    *,
    target: ManualStageTarget,
    activator: MacOSAppActivator | None = None,
    runner: Runner = subprocess.run,
    osascript_executable: str = "osascript",
) -> ChatGPTNativePreflightResult:
    native_target = ensure_native_chatgpt_mac_target(target)
    activator = activator or MacOSAppActivator()
    activation = activator.activate_with_result(
        native_target.app_name,
        app_path=native_target.app_path,
        bundle_id=native_target.bundle_id,
    )
    app_targets = diagnose_chatgpt_app_targets(
        target=native_target,
        profile=CHATGPT_MAC_PROFILE,
        runner=runner,
        osascript_executable=osascript_executable,
    )
    expected = native_target.bundle_id or NATIVE_CHATGPT_MAC_BUNDLE_ID
    succeeded = (
        activation.succeeded
        and app_targets.selected_bundle_id == expected
        and app_targets.native_available
    )
    error = None
    if not activation.succeeded:
        error = "Native ChatGPT Mac activation failed."
    elif app_targets.selected_bundle_id != expected:
        error = "Native ChatGPT Mac bundle id was not selected after activation."
    return ChatGPTNativePreflightResult(
        target=native_target,
        activation_result=activation,
        app_targets=app_targets,
        selected_native_bundle_id=app_targets.selected_bundle_id,
        activation_method=activation.winning_strategy,
        succeeded=succeeded,
        error=error,
    )


def set_app_window_bounds(
    *,
    target: ManualStageTarget,
    bounds: tuple[int, int, int, int],
    runner: Runner = subprocess.run,
    osascript_executable: str = "osascript",
) -> AppWindowBoundsResult:
    if not target.bundle_id:
        return AppWindowBoundsResult(
            target_app=target.app_name,
            target_bundle_id=target.bundle_id,
            requested_bounds=bounds,
            before_bounds=None,
            after_bounds=None,
            succeeded=False,
            error="A bundle id is required to set app window bounds safely.",
        )
    completed = runner(
        [osascript_executable, "-e", _set_window_bounds_script(target.bundle_id, bounds)],
        check=False,
        text=True,
        capture_output=True,
    )
    output = (completed.stdout or completed.stderr or "").strip()
    if completed.returncode != 0:
        return AppWindowBoundsResult(
            target_app=target.app_name,
            target_bundle_id=target.bundle_id,
            requested_bounds=bounds,
            before_bounds=None,
            after_bounds=None,
            succeeded=False,
            error=output or "Setting app window bounds failed.",
        )
    if output.startswith("ERROR\t"):
        return AppWindowBoundsResult(
            target_app=target.app_name,
            target_bundle_id=target.bundle_id,
            requested_bounds=bounds,
            before_bounds=None,
            after_bounds=None,
            succeeded=False,
            error=output.split("\t", 1)[1] or "Setting app window bounds failed.",
        )
    parts = output.split("\t")
    if len(parts) != 9 or parts[0] != "OK":
        return AppWindowBoundsResult(
            target_app=target.app_name,
            target_bundle_id=target.bundle_id,
            requested_bounds=bounds,
            before_bounds=None,
            after_bounds=None,
            succeeded=False,
            error=output or "Unexpected app window bounds response.",
        )
    before = _parse_bounds_parts(parts[1:5])
    after = _parse_bounds_parts(parts[5:9])
    return AppWindowBoundsResult(
        target_app=target.app_name,
        target_bundle_id=target.bundle_id,
        requested_bounds=bounds,
        before_bounds=before,
        after_bounds=after,
        succeeded=after == bounds,
        error=None if after == bounds else "Window bounds did not match requested bounds after update.",
    )


def format_app_window_bounds_result(result: AppWindowBoundsResult) -> str:
    return "\n".join(
        [
            "# App Window Bounds Diagnostic",
            "",
            f"Target app: {result.target_app}",
            f"Target bundle id: {result.target_bundle_id or 'unavailable'}",
            f"Requested bounds: {result.requested_bounds}",
            f"Before bounds: {result.before_bounds or 'unavailable'}",
            f"After bounds: {result.after_bounds or 'unavailable'}",
            f"Succeeded: {'yes' if result.succeeded else 'no'}",
            f"Error: {result.error or 'none'}",
        ]
    )


def format_chatgpt_app_target_diagnostic(result: ChatGPTAppTargetDiagnostic) -> str:
    lines = [
        "# ChatGPT App Target Diagnostic",
        "",
        f"Selected PM profile: {result.selected_profile}",
        f"Expected bundle id: {result.expected_bundle_id}",
        f"Configured app name: {result.configured_app_name}",
        f"Configured bundle id: {result.configured_bundle_id or 'unspecified'}",
        f"Selected bundle id: {result.selected_bundle_id or 'unavailable'}",
        f"Selected native bundle id: {result.selected_bundle_id if result.selected_profile == CHATGPT_MAC_PROFILE and result.selected_bundle_id else 'unavailable'}",
        f"Selected Chrome app bundle id: {result.selected_bundle_id if result.selected_profile == CHATGPT_CHROME_APP_PROFILE and result.selected_bundle_id else 'unavailable'}",
        f"Native ChatGPT available: {'yes' if result.native_available else 'no'}",
        f"Chrome app available: {'yes' if result.chrome_app_available else 'no'}",
        f"Chrome/PWA candidates rejected: {result.chrome_pwa_candidates_rejected}",
        f"Native candidates rejected: {result.native_candidates_rejected}",
        f"Chrome app activation attempted: {'yes' if result.activation_attempted else 'no'}",
        f"Chrome app activation bundle id: {result.activation_bundle_id or 'unavailable'}",
        f"Chrome app activation succeeded: {_format_optional_bool(result.activation_succeeded)}",
        f"Re-enumerated after activation: {'yes' if result.reenumerated_after_activation else 'no'}",
        f"Activation error: {result.activation_error or 'none'}",
        f"Error: {result.error or 'none'}",
        "",
        "## Candidates",
    ]
    if not result.candidates:
        lines.append("No ChatGPT app candidates were reported.")
    for candidate in result.candidates:
        status = "selected" if candidate.selected else ("rejected" if candidate.rejected else "candidate")
        reasons = ", ".join(candidate.rejection_reasons) if candidate.rejection_reasons else "none"
        lines.append(
            "- "
            f"status={status}, name={candidate.name or 'unknown'}, "
            f"bundle_id={candidate.bundle_id or 'unknown'}, "
            f"pid={candidate.pid or 'unknown'}, "
            f"frontmost={_format_optional_bool(candidate.frontmost)}, "
            f"visible={_format_optional_bool(candidate.visible)}, "
            f"windows={candidate.window_count if candidate.window_count is not None else 'unknown'}, "
            f"window_details={_format_window_summaries(candidate.window_summaries)}, "
            f"rejection_reasons={reasons}"
        )
    return "\n".join(lines)


def format_chatgpt_native_preflight(result: ChatGPTNativePreflightResult) -> str:
    lines = [
        "# ChatGPT Mac Native Target Preflight",
        "",
        f"Target app: {result.target.app_name}",
        f"Target bundle id: {result.target.bundle_id or 'unspecified'}",
        f"Target app path: {result.target.app_path or 'unspecified'}",
        f"Activation method: {result.activation_method or 'none'}",
        f"Selected native bundle id: {result.selected_native_bundle_id or 'unavailable'}",
        f"Succeeded: {'yes' if result.succeeded else 'no'}",
        f"Error: {result.error or 'none'}",
        "",
        "## Activation Result",
        format_activation_result(result.activation_result),
        "",
        format_chatgpt_app_target_diagnostic(result.app_targets),
    ]
    return "\n".join(lines)


def _enumerate_chatgpt_targets_script() -> str:
    chrome_checks = " or ".join(
        f'bundleValue starts with "{prefix}"' for prefix in CHROME_BUNDLE_ID_PREFIXES
    )
    return f"""
tell application "System Events"
  set outputValue to ""
  set matchedCount to 0
    repeat with targetProcess in application processes
    set nameValue to ""
    set bundleValue to ""
    set pidValue to "unknown"
    set frontmostValue to "unknown"
    set visibleValue to "unknown"
    set windowCountValue to "unknown"
    try
      set nameValue to name of targetProcess as text
    end try
    try
      set bundleValue to bundle identifier of targetProcess as text
    end try
    try
      set pidValue to unix id of targetProcess as text
    end try
    try
      set frontmostValue to frontmost of targetProcess as text
    end try
    try
      set visibleValue to visible of targetProcess as text
    end try
    try
      set windowCountValue to count of windows of targetProcess as text
    end try
    set windowSummaryValue to ""
    try
      set targetWindowCount to count of windows of targetProcess
      repeat with i from 1 to targetWindowCount
        set targetWindow to window i of targetProcess
        set titleValue to ""
        set xValue to ""
        set yValue to ""
        set widthValue to ""
        set heightValue to ""
        set minimizedValue to "unknown"
        set fullscreenValue to "unknown"
        try
          set titleValue to name of targetWindow as text
        end try
        try
          set posValue to position of targetWindow
          set xValue to item 1 of posValue as text
          set yValue to item 2 of posValue as text
        end try
        try
          set sizeValue to size of targetWindow
          set widthValue to item 1 of sizeValue as text
          set heightValue to item 2 of sizeValue as text
        end try
        try
          set minimizedValue to value of attribute "AXMinimized" of targetWindow as text
        end try
        try
          set fullscreenValue to value of attribute "AXFullScreen" of targetWindow as text
        end try
        if i is greater than 1 then set windowSummaryValue to windowSummaryValue & " || "
        set windowSummaryValue to windowSummaryValue & "title=" & titleValue & ",bounds=(" & xValue & "," & yValue & "," & widthValue & "," & heightValue & "),minimized=" & minimizedValue & ",fullscreen=" & fullscreenValue
      end repeat
    end try
    set includeProcess to false
    if bundleValue is "{NATIVE_CHATGPT_MAC_BUNDLE_ID}" then set includeProcess to true
    if {chrome_checks} then set includeProcess to true
    ignoring case
      if nameValue contains "ChatGPT" then set includeProcess to true
    end ignoring
    if includeProcess then
      if matchedCount is greater than 0 then set outputValue to outputValue & linefeed
      set outputValue to outputValue & nameValue & tab & bundleValue & tab & pidValue & tab & frontmostValue & tab & visibleValue & tab & windowCountValue & tab & windowSummaryValue
      set matchedCount to matchedCount + 1
    end if
  end repeat
  return outputValue
end tell
""".strip()


def _set_window_bounds_script(
    bundle_id: str,
    bounds: tuple[int, int, int, int],
) -> str:
    escaped_bundle_id = bundle_id.replace("\\", "\\\\").replace('"', '\\"')
    x, y, width, height = bounds
    return f"""
tell application "System Events"
  try
    set matchingProcesses to every application process whose bundle identifier is "{escaped_bundle_id}"
    if (count of matchingProcesses) is 0 then
      return "ERROR" & tab & "No running process found for bundle id {escaped_bundle_id}."
    end if
    set targetProcess to item 1 of matchingProcesses
    set bestWindowCount to -1
    repeat with candidateProcess in matchingProcesses
      set candidateProcessRef to contents of candidateProcess
      set candidateWindowCount to 0
      try
        set candidateWindowCount to count of (windows of candidateProcessRef)
      end try
      if candidateWindowCount is greater than bestWindowCount then
        set targetProcess to candidateProcessRef
        set bestWindowCount to candidateWindowCount
      end if
    end repeat
    tell targetProcess
      if (count of windows) is 0 then
        return "ERROR" & tab & "No windows found for bundle id {escaped_bundle_id}."
      end if
      set selectedWindow to window 1
      set selectedArea to -1
      repeat with candidateWindow in windows
        set candidateArea to 0
        try
          set candidateSize to size of candidateWindow
          set candidateArea to (item 1 of candidateSize) * (item 2 of candidateSize)
        end try
        if candidateArea is greater than selectedArea then
          set selectedWindow to candidateWindow
          set selectedArea to candidateArea
        end if
      end repeat
      set beforePosition to position of selectedWindow
      set beforeSize to size of selectedWindow
      try
        set value of attribute "AXMinimized" of selectedWindow to false
      end try
      set position of selectedWindow to {{{x}, {y}}}
      set size of selectedWindow to {{{width}, {height}}}
      delay 0.2
      set afterPosition to position of selectedWindow
      set afterSize to size of selectedWindow
      return "OK" & tab & (item 1 of beforePosition as text) & tab & (item 2 of beforePosition as text) & tab & (item 1 of beforeSize as text) & tab & (item 2 of beforeSize as text) & tab & (item 1 of afterPosition as text) & tab & (item 2 of afterPosition as text) & tab & (item 1 of afterSize as text) & tab & (item 2 of afterSize as text)
    end tell
  on error errorMessage
    return "ERROR" & tab & errorMessage
  end try
end tell
""".strip()


def _parse_app_candidates(output: str) -> list[ChatGPTAppCandidate]:
    candidates: list[ChatGPTAppCandidate] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        while len(parts) < 6:
            parts.append("")
        if len(parts) >= 7:
            name = parts[0]
            bundle_id = parts[1]
            pid = parts[2]
            frontmost = parts[3]
            visible = parts[4]
            window_count_raw = parts[5]
            window_summary = parts[6]
        else:
            name = parts[0]
            bundle_id = parts[1]
            pid = None
            frontmost = parts[2]
            visible = parts[3]
            window_count_raw = parts[4]
            window_summary = parts[5]
        window_count = None
        try:
            window_count = (
                int(window_count_raw)
                if window_count_raw and window_count_raw != "unknown"
                else None
            )
        except ValueError:
            window_count = None
        candidates.append(
            ChatGPTAppCandidate(
                name=name,
                bundle_id=bundle_id,
                frontmost=_parse_optional_bool(frontmost),
                visible=_parse_optional_bool(visible),
                window_count=window_count,
                pid=pid,
                window_summaries=_parse_window_summaries(window_summary),
            )
        )
    return candidates


def _parse_bounds_parts(parts: list[str]) -> tuple[int, int, int, int] | None:
    try:
        if len(parts) != 4:
            return None
        x, y, width, height = (int(float(part)) for part in parts)
        return (x, y, width, height)
    except ValueError:
        return None


def _run_target_enumeration(
    runner: Runner,
    osascript_executable: str,
) -> subprocess.CompletedProcess[str]:
    return runner(
        [osascript_executable, "-e", _enumerate_chatgpt_targets_script()],
        check=False,
        text=True,
        capture_output=True,
    )


def _chrome_app_candidate_for_activation(
    candidates: tuple[ChatGPTAppCandidate, ...],
    target: ManualStageTarget,
) -> ChatGPTAppCandidate | None:
    chrome_candidates = [
        candidate
        for candidate in candidates
        if is_chatgpt_chrome_app_candidate_bundle(candidate.bundle_id)
        and _candidate_matches_configured_bundle(candidate, target)
    ]
    if not chrome_candidates:
        return None
    return sorted(
        chrome_candidates,
        key=_chrome_app_candidate_sort_key,
        reverse=True,
    )[0]


def _expected_bundle_id_for_profile(profile: str, target: ManualStageTarget) -> str:
    if profile == CHATGPT_CHROME_APP_PROFILE:
        return target.bundle_id or "com.google.Chrome.app.*"
    return NATIVE_CHATGPT_MAC_BUNDLE_ID


def _target_error_for_profile(profile: str, selected_bundle_id: str | None) -> str | None:
    if selected_bundle_id:
        return None
    if profile == CHATGPT_CHROME_APP_PROFILE:
        return "Chrome/PWA ChatGPT app target was not found."
    return "Native ChatGPT Mac app target was not found."


def _candidate_matches_configured_bundle(
    candidate: ChatGPTAppCandidate,
    target: ManualStageTarget,
) -> bool:
    return target.bundle_id is None or candidate.bundle_id == target.bundle_id


def _candidate_has_windows(candidate: ChatGPTAppCandidate) -> bool:
    return (candidate.window_count or 0) > 0


def _chrome_app_candidate_sort_key(candidate: ChatGPTAppCandidate) -> tuple[int, int, int, int]:
    window_count = candidate.window_count or 0
    return (
        1 if window_count > 0 else 0,
        1 if candidate.frontmost else 0,
        1 if candidate.visible else 0,
        window_count,
    )


def _parse_window_summaries(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(" || ") if part.strip())


def _format_window_summaries(window_summaries: tuple[str, ...]) -> str:
    if not window_summaries:
        return "none"
    return " | ".join(window_summaries)


def _parse_optional_bool(value: str) -> bool | None:
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    return None


def _format_optional_bool(value: bool | None) -> str:
    if value is None:
        return "unknown"
    return "yes" if value else "no"
