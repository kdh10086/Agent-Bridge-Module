# GUI Template Assets

This folder contains owner-reviewed visual templates for bounded GUI diagnostics.

Templates must be tight crops of generic composer controls only. Do not include private prompt text, project content, conversation text, or surrounding UI content.

## App-Specific Templates

Agent Bridge keeps control matching app-specific to avoid false positives between visually similar ChatGPT and Codex controls.

- `chatgpt_mac/chatgpt_mac_plus_button_light.png`
- `chatgpt_mac/chatgpt_mac_plus_button_dark.png`
- `chatgpt_mac/chatgpt_mac_send_disabled_button_light.png`
- `chatgpt_mac/chatgpt_mac_send_disabled_button_dark.png`
- `chatgpt_mac/chatgpt_mac_send_button_light.png`
- `chatgpt_mac/chatgpt_mac_send_button_dark.png`
- `chatgpt_mac/chatgpt_mac_stop_button_light.png`
- `chatgpt_mac/chatgpt_mac_stop_button_dark.png`
- `chatgpt_mac/chatgpt_mac_copy_response_button_light.png`
- `chatgpt_mac/chatgpt_mac_copy_response_button_dark.png`
- `chatgpt_mac/chatgpt_mac_scroll_down_button_light.png`
- `chatgpt_mac/chatgpt_mac_scroll_down_button_dark.png`
- `chatgpt_chrome_app/chatgpt_chrome_app_plus_button_light.png`
- `chatgpt_chrome_app/chatgpt_chrome_app_plus_button_dark.png`
- `chatgpt_chrome_app/chatgpt_chrome_app_voice_button_light.png`
- `chatgpt_chrome_app/chatgpt_chrome_app_voice_button_dark.png`
- `chatgpt_chrome_app/chatgpt_chrome_app_send_button_light.png`
- `chatgpt_chrome_app/chatgpt_chrome_app_send_button_dark.png`
- `chatgpt_chrome_app/chatgpt_chrome_app_stop_button_light.png`
- `chatgpt_chrome_app/chatgpt_chrome_app_stop_button_dark.png`
- `chatgpt_chrome_app/chatgpt_chrome_app_copy_response_button_light.png`
- `chatgpt_chrome_app/chatgpt_chrome_app_copy_response_button_dark.png`
- `chatgpt_chrome_app/chatgpt_chrome_app_scroll_down_button_light.png`
- `chatgpt_chrome_app/chatgpt_chrome_app_scroll_down_button_dark.png`
- `codex/codex_plus_button_light.png`
- `codex/codex_plus_button_dark.png`
- `codex/codex_send_disabled_button_light.png`
- `codex/codex_send_disabled_button_dark.png`
- `codex/codex_send_button_light.png`
- `codex/codex_send_button_dark.png`
- `codex/codex_stop_button_light.png`
- `codex/codex_stop_button_dark.png`

ChatGPT Mac targets must use only the `chatgpt_mac` templates. ChatGPT Chrome app diagnostics must use only the `chatgpt_chrome_app` templates. Codex targets must use only the `codex` templates. Matching is also scoped to the activated target app's selected main window bounds, not the whole screen. Chrome app matching uses selected-window-relative search regions and configurable grayscale multiscale matching so the same tight crops can work across different PWA window sizes. For ChatGPT Mac and Codex, send-disabled maps to idle, send maps to composer-has-text, stop maps to running, and plus is the composer anchor. For the ChatGPT Chrome app profile, voice maps to idle, send maps to composer-has-text, stop maps to running, plus is the composer anchor, copy-response is the response capture control, and scroll-down reveals latest response actions. ChatGPT response-copy templates must be tight crops of the response copy button only; scroll-down templates are tight crops of the down-arrow control used to reveal the latest response actions. Response capture first searches for the copy button, then may click the scroll-down control and retry within the same bounded app window.

Diagnostics inspect both light and dark templates for every state. Native ChatGPT Mac plus matching is scoped to the lower-left composer control strip, while native send-disabled/send/stop matching is scoped to the lower-right composer control strip so conversation content and response buttons do not compete with composer controls. For Codex, the state-button threshold can be stricter than the plus-anchor threshold because send-disabled/send/stop controls are visually similar; partial state-button matches should be rejected instead of inferred as a state.

## Safety

These templates are visual anchor aids only. They do not enable submit, do not bypass SafetyGate or CommandQueue, and do not replace prompt-presence or submit-confirmation checks. Diagnostic click/paste commands must remain bounded and must not press Enter/Return.
