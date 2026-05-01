from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent_bridge.gui.asset_state_machine import (
    AssetVisualStateDetector,
    VisualStateDetection,
    asset_profile_for_target,
)
from agent_bridge.gui.codex_ui_detector import CodexUIDetector
from agent_bridge.gui.gui_automation import (
    DIAGNOSTIC_ONLY_PM_PASTE_VARIANTS,
    pm_paste_backends_for_target,
    production_pm_paste_variants_for_target,
)
from agent_bridge.gui.macos_apps import AppActivationError, MacOSAppActivator, ManualStageTarget
from agent_bridge.gui.visual_pm_controller import (
    PMVisualAssetInventoryItem,
    VisualPMController,
    collect_visual_pm_asset_inventory,
    visual_pm_asset_directory,
)


@dataclass(frozen=True)
class PMVisualSequenceDiagnostic:
    target: ManualStageTarget
    profile: str
    backend: str
    asset_profile: str
    asset_directory: str
    asset_inventory: tuple[PMVisualAssetInventoryItem, ...]
    shared_controller_used: bool
    configured_paste_backends: tuple[str, ...]
    production_paste_variants: tuple[str, ...]
    diagnostic_only_paste_variants: tuple[str, ...]
    activation_succeeded: bool
    window_bounds: tuple[int, int, int, int] | None
    visual_state_result: VisualStateDetection | None
    composer_click_point: tuple[int, int] | None
    composer_click_point_safe: bool
    click_test_attempted: bool
    click_test_succeeded: bool | None
    failure_step: str | None
    error: str | None


def diagnose_pm_visual_sequence(
    *,
    target: ManualStageTarget,
    logs_dir: Path | None = None,
    click_test: bool = False,
) -> PMVisualSequenceDiagnostic:
    controller = VisualPMController.for_target(target)
    target = controller.target
    inventory = collect_visual_pm_asset_inventory(controller.profile.name)
    missing = [item.path for item in inventory if not item.exists]
    if target.visual_asset_profile != controller.profile.asset_profile:
        return _result(
            target=target,
            profile=controller.profile.name,
            asset_profile=target.visual_asset_profile or "",
            backend=target.backend or "",
            inventory=inventory,
            failure_step="asset_profile_verification",
            error=(
                "asset_profile_mismatch: "
                f"expected={controller.profile.asset_profile} "
                f"actual={target.visual_asset_profile}"
            ),
        )
    if controller.profile.name == "chatgpt_mac" and missing:
        return _result(
            target=target,
            profile=controller.profile.name,
            asset_profile=controller.profile.asset_profile,
            backend=controller.profile.backend,
            inventory=inventory,
            failure_step="asset_inventory",
            error=f"chatgpt_mac_asset_missing: {', '.join(missing)}",
        )

    try:
        MacOSAppActivator().activate(
            target.app_name,
            app_path=target.app_path,
            bundle_id=target.bundle_id,
        )
    except AppActivationError as exc:
        return _result(
            target=target,
            profile=controller.profile.name,
            asset_profile=controller.profile.asset_profile,
            backend=controller.profile.backend,
            inventory=inventory,
            failure_step="activate_app",
            error=str(exc),
        )

    window_bounds: tuple[int, int, int, int] | None = None
    try:
        window_bounds = CodexUIDetector().select_main_window(target).selected_bounds
    except Exception as exc:
        return _result(
            target=target,
            profile=controller.profile.name,
            asset_profile=controller.profile.asset_profile,
            backend=controller.profile.backend,
            inventory=inventory,
            activation_succeeded=True,
            failure_step="select_window",
            error=str(exc),
        )

    try:
        detection = AssetVisualStateDetector().detect(
            target=target,
            window_bounds=window_bounds,
            profile=asset_profile_for_target(target),
            logs_dir=logs_dir,
            write_debug=bool(logs_dir),
        )
    except Exception as exc:
        return _result(
            target=target,
            profile=controller.profile.name,
            asset_profile=controller.profile.asset_profile,
            backend=controller.profile.backend,
            inventory=inventory,
            activation_succeeded=True,
            window_bounds=window_bounds,
            failure_step="detect_visual_state",
            error=str(exc),
        )

    failure_step = None
    error = None
    if not detection.screenshot_captured:
        failure_step = "capture_screenshot"
        error = detection.error or "screenshot_unavailable"
    elif not detection.plus_anchor_found:
        failure_step = "detect_plus_anchor"
        error = "plus_anchor_not_found"
    elif not detection.composer_click_point_safe:
        failure_step = "compute_composer_click_point"
        error = "composer_click_point_unsafe"

    click_succeeded = None
    if click_test and failure_step is None and detection.computed_composer_click_point is not None:
        try:
            import pyautogui

            pyautogui.click(*detection.computed_composer_click_point)
            click_succeeded = True
        except Exception as exc:  # pragma: no cover - live diagnostic only.
            click_succeeded = False
            failure_step = "click_test"
            error = str(exc)

    return _result(
        target=target,
        profile=controller.profile.name,
        asset_profile=controller.profile.asset_profile,
        backend=controller.profile.backend,
        inventory=inventory,
        activation_succeeded=True,
        window_bounds=window_bounds,
        visual_state_result=detection,
        composer_click_point=detection.computed_composer_click_point,
        composer_click_point_safe=detection.composer_click_point_safe,
        click_test_attempted=click_test,
        click_test_succeeded=click_succeeded,
        failure_step=failure_step,
        error=error,
    )


def format_pm_visual_sequence_diagnostic(result: PMVisualSequenceDiagnostic) -> str:
    detection = result.visual_state_result
    lines = [
        "# PM Visual Sequence Diagnostic",
        "",
        f"Profile: {result.profile}",
        f"App: {result.target.app_name}",
        f"Bundle id: {result.target.bundle_id or 'unavailable'}",
        f"Backend: {result.backend}",
        f"Asset profile: {result.asset_profile}",
        f"Asset directory: {result.asset_directory}",
        f"Shared controller used: {_yes_no(result.shared_controller_used)}",
        "Shared matcher used: yes",
        "Effective threshold helper used: yes",
        f"Configured paste backend chain: {', '.join(result.configured_paste_backends)}",
        f"Production-safe paste variants: {', '.join(result.production_paste_variants) or 'none'}",
        (
            "Diagnostic-only paste variants: "
            f"{', '.join(result.diagnostic_only_paste_variants) or 'none'}"
        ),
        f"Activation succeeded: {_yes_no(result.activation_succeeded)}",
        f"Window bounds: {result.window_bounds or 'unavailable'}",
        "No prompt submitted: yes",
        "No paste attempted: yes",
        f"Click test attempted: {_yes_no(result.click_test_attempted)}",
        f"Click test succeeded: {_yes_no_unknown(result.click_test_succeeded)}",
        f"Failure step: {result.failure_step or 'none'}",
        f"Error: {result.error or 'none'}",
        "",
        "## Asset Inventory",
    ]
    for item in result.asset_inventory:
        size = f"{item.image_size[0]}x{item.image_size[1]}" if item.image_size else "unavailable"
        lines.append(
            f"- {item.role}: exists={_yes_no(item.exists)} size={size} path={item.path}"
        )
    lines.extend(["", "## Visual Detection"])
    if detection is None:
        lines.append("Visual detection: not run")
    else:
        lines.extend(
            [
                f"Screenshot captured: {_yes_no(detection.screenshot_captured)}",
                f"State: {detection.matched_state.value}",
                f"Matched asset: {detection.matched_asset_path or 'unavailable'}",
                f"Confidence: {_float_or_unavailable(detection.confidence)}",
                f"State reason: {detection.state_selection_reason}",
                f"Plus anchor found: {_yes_no(detection.plus_anchor_found)}",
                f"Plus bbox: {detection.plus_anchor_bbox or 'unavailable'}",
                f"Plus confidence: {_float_or_unavailable(detection.plus_anchor_confidence)}",
                (
                    "Composer offset dx/dy: "
                    f"({result.target.plus_anchor_x_offset}, -{result.target.plus_anchor_y_offset})"
                ),
                f"Composer click point: {detection.computed_composer_click_point or 'unavailable'}",
                f"Composer click point safe: {_yes_no(detection.composer_click_point_safe)}",
                "",
                "## Template Diagnostics",
            ]
        )
        for diagnostic in detection.template_diagnostics:
            lines.append(
                "- "
                f"{diagnostic.asset_kind.value} "
                f"{diagnostic.template_path}: "
                f"raw={_float_or_unavailable(diagnostic.best_match_confidence)} "
                f"edge={_float_or_unavailable(getattr(diagnostic, 'edge_score', None))} "
                f"glyph={_float_or_unavailable(getattr(diagnostic, 'glyph_score', None))} "
                f"appearance={_float_or_unavailable(diagnostic.appearance_score)} "
                f"composite={_float_or_unavailable(getattr(diagnostic, 'composite_score', None))} "
                f"configured={_float_or_unavailable(diagnostic.configured_threshold if diagnostic.configured_threshold is not None else diagnostic.threshold)} "
                f"effective={_float_or_unavailable(diagnostic.effective_threshold if diagnostic.effective_threshold is not None else diagnostic.threshold)} "
                f"cap_applied={_yes_no(diagnostic.threshold_cap_applied)} "
                f"threshold={_float_or_unavailable(diagnostic.threshold)} "
                f"accepted={_yes_no(diagnostic.accepted)} "
                f"reason={diagnostic.rejection_reason or 'none'}"
            )
    return "\n".join(lines)


def _result(
    *,
    target: ManualStageTarget,
    profile: str,
    backend: str,
    asset_profile: str,
    inventory: tuple[PMVisualAssetInventoryItem, ...],
    activation_succeeded: bool = False,
    window_bounds: tuple[int, int, int, int] | None = None,
    visual_state_result: VisualStateDetection | None = None,
    composer_click_point: tuple[int, int] | None = None,
    composer_click_point_safe: bool = False,
    click_test_attempted: bool = False,
    click_test_succeeded: bool | None = None,
    failure_step: str | None = None,
    error: str | None = None,
) -> PMVisualSequenceDiagnostic:
    return PMVisualSequenceDiagnostic(
        target=target,
        profile=profile,
        backend=backend,
        asset_profile=asset_profile,
        asset_directory=visual_pm_asset_directory(profile),
        asset_inventory=inventory,
        shared_controller_used=True,
        configured_paste_backends=pm_paste_backends_for_target(target),
        production_paste_variants=production_pm_paste_variants_for_target(target),
        diagnostic_only_paste_variants=DIAGNOSTIC_ONLY_PM_PASTE_VARIANTS,
        activation_succeeded=activation_succeeded,
        window_bounds=window_bounds,
        visual_state_result=visual_state_result,
        composer_click_point=composer_click_point,
        composer_click_point_safe=composer_click_point_safe,
        click_test_attempted=click_test_attempted,
        click_test_succeeded=click_test_succeeded,
        failure_step=failure_step,
        error=error,
    )


def _float_or_unavailable(value: float | None) -> str:
    return f"{value:.3f}" if value is not None else "unavailable"


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def _yes_no_unknown(value: bool | None) -> str:
    if value is None:
        return "unknown"
    return _yes_no(value)
