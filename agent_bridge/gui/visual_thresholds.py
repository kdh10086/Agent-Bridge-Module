from __future__ import annotations

VISUAL_CONFIDENCE_THRESHOLD_CAP = 0.70


def effective_visual_threshold(configured_threshold: float | None) -> float:
    """Return the shared effective confidence threshold for GUI template matching."""
    if configured_threshold is None:
        return VISUAL_CONFIDENCE_THRESHOLD_CAP
    configured = float(configured_threshold)
    if configured <= VISUAL_CONFIDENCE_THRESHOLD_CAP:
        return configured
    return VISUAL_CONFIDENCE_THRESHOLD_CAP


def visual_threshold_cap_applied(configured_threshold: float | None) -> bool:
    if configured_threshold is None:
        return False
    return float(configured_threshold) > VISUAL_CONFIDENCE_THRESHOLD_CAP
