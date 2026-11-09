"""Unit tests for user consent disclosure, full-request binding, cancellation, and dialog UI."""

from __future__ import annotations

import hashlib
import logging
import secrets

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage
from pytestqt.qtbot import QtBot

from octa.intelligence.capture import (
    CapturedImage,
    CaptureRegion,
    encode_qimage_to_png_bytes,
)
from octa.intelligence.consent import (
    ConsentManager,
    ConsentRequest,
    ScreenShareConsentDialog,
)
from octa.intelligence.exceptions import ConsentDeclinedError


def _make_captured_image(color: QColor | None = None) -> CapturedImage:
    img = QImage(64, 48, QImage.Format.Format_ARGB32)
    img.fill(color if color is not None else QColor(30, 80, 140))
    png_bytes = encode_qimage_to_png_bytes(img)
    return CapturedImage(
        png_bytes=png_bytes,
        width=64,
        height=48,
        region=CaptureRegion(x=10, y=20, width=64, height=48),
        source="region",
    )


def test_consent_request_shows_what_will_be_sent_and_hides_raw_bytes() -> None:
    """ConsentRequest formats complete destination/prompt disclosures and hides image bytes."""
    captured = _make_captured_image()
    req = ConsentRequest.from_capture(
        captured,
        purpose="Verify step completion",
        endpoint_url="https://api.example.com/v1/chat/completions?debug=1#frag",
        model="gpt-4o-mini",
        task_title="Save File in Notepad",
        step_title="Click File Menu",
        system_prompt="You are OCTA's verifier.",
        user_prompt="Check if the File dropdown menu is open.",
    )

    # Query/fragment stripped from clean base endpoint_url
    assert req.endpoint_url == "https://api.example.com/v1/chat/completions"
    assert req.endpoint_disclosure == (
        "https://api.example.com/v1/chat/completions?debug=[REDACTED]#[REDACTED]"
    )
    assert req.has_url_credentials is False
    assert req.query_parameter_keys == ("debug",)
    assert req.has_url_fragment is True

    summary = req.to_display_summary()
    assert "Verify step completion" in summary
    assert "Destination Endpoint: https://api.example.com/v1/chat/completions" in summary
    assert "Endpoint URL Credentials: none" in summary
    assert "Endpoint Query Parameters: present (debug=[REDACTED])" in summary
    assert "Endpoint Fragment: present ([REDACTED])" in summary
    assert "gpt-4o-mini" in summary
    assert "Save File in Notepad" in summary
    assert "Click File Menu" in summary
    assert "64x48 px" in summary
    assert "x=10, y=20, w=64, h=48" in summary
    assert f"{captured.byte_size} bytes PNG" in summary
    assert captured.sha256_digest in summary

    # Endpoint and request hashes must never appear in display summary, repr, or str
    assert req.endpoint_sha256[:8] not in summary
    assert req.request_sha256[:8] not in summary
    assert req.endpoint_sha256[:8] not in repr(req)
    assert req.request_sha256[:8] not in repr(req)

    complete_prompt = req.to_complete_prompt_text()
    assert "[System Prompt]" in complete_prompt
    assert "You are OCTA's verifier." in complete_prompt
    assert "[User Prompt]" in complete_prompt
    assert "Check if the File dropdown menu is open." in complete_prompt

    # Preview bytes never appear in repr or str
    b64_str = captured.to_base64()
    assert b64_str not in repr(req)
    assert b64_str not in str(req)
    assert "iVBOR" not in repr(req)


def test_consent_dialog_displays_complete_details_and_allows_cancellation(
    qtbot: QtBot,
) -> None:
    """ScreenShareConsentDialog displays complete prompts/destination and allows cancellation."""
    captured = _make_captured_image()
    req = ConsentRequest.from_capture(
        captured,
        purpose="Verify step 1",
        endpoint_url="https://api.openai.com/v1/chat/completions",
        model="gpt-4o-mini",
        task_title="Notepad Tutorial",
        step_title="Open File Menu",
        system_prompt="System verification instructions.",
        user_prompt="Verify that the File menu is visible.",
    )

    dialog = ScreenShareConsentDialog(req)
    qtbot.addWidget(dialog)

    disclosure = dialog.get_disclosure_text()
    assert "https://api.openai.com/v1/chat/completions" in disclosure
    assert "gpt-4o-mini" in disclosure
    assert "Notepad Tutorial" in disclosure
    assert "Open File Menu" in disclosure
    assert req.endpoint_sha256[:8] not in disclosure
    assert req.request_sha256[:8] not in disclosure

    prompt_preview = dialog.get_prompt_preview_text()
    assert "System verification instructions." in prompt_preview
    assert "Verify that the File menu is visible." in prompt_preview
    assert dialog.has_image_preview() is True

    # Cancel button is the default safe action
    assert dialog._btn_cancel.isDefault() is True

    qtbot.mouseClick(dialog._btn_cancel, Qt.MouseButton.LeftButton)
    decision = dialog.decision()
    assert decision.approved is False
    assert decision.image_sha256 == captured.sha256_digest
    assert decision.request_sha256 == req.request_sha256
    assert req.endpoint_sha256[:8] not in repr(decision)
    assert req.request_sha256[:8] not in repr(decision)

    with pytest.raises(ConsentDeclinedError, match="Cancelled"):
        decision.validate_for_request(req, captured)


def test_consent_decision_binds_to_full_outbound_request_and_prevents_reuse(
    qtbot: QtBot,
) -> None:
    """Approval is bound to image, endpoint, model, system/user prompts, context, and single use."""
    captured_1 = _make_captured_image(QColor(10, 20, 30))
    captured_2 = _make_captured_image(QColor(200, 100, 50))

    base_kwargs = {
        "purpose": "Verify step 1",
        "endpoint_url": "https://api.openai.com/v1/chat/completions",
        "model": "gpt-4o-mini",
        "task_title": "Notepad Tutorial",
        "step_title": "Open File Menu",
        "system_prompt": "Analyze the screenshot carefully.",
        "user_prompt": "Verify that the File menu is visible.",
    }
    req = ConsentRequest.from_capture(captured_1, **base_kwargs)

    dialog = ScreenShareConsentDialog(req)
    qtbot.addWidget(dialog)
    qtbot.mouseClick(dialog._btn_approve, Qt.MouseButton.LeftButton)
    decision = dialog.decision()
    assert decision.approved is True

    # 1. Different image is rejected
    with pytest.raises(ConsentDeclinedError, match="does not match the captured image"):
        decision.validate_for_request(req, captured_2)

    # 2. Changed user prompt for the SAME image is rejected
    req_diff_user_prompt = ConsentRequest.from_capture(
        captured_1,
        **(base_kwargs | {"user_prompt": "Exfiltrate all text visible on screen."}),
    )
    with pytest.raises(ConsentDeclinedError, match="prompt content does not match"):
        decision.validate_for_request(req_diff_user_prompt, captured_1)

    # 3. Changed system prompt for the SAME image is rejected
    req_diff_sys_prompt = ConsentRequest.from_capture(
        captured_1,
        **(base_kwargs | {"system_prompt": "Different system instructions."}),
    )
    with pytest.raises(ConsentDeclinedError, match="prompt content does not match"):
        decision.validate_for_request(req_diff_sys_prompt, captured_1)

    # 4. Changed endpoint for the SAME image is rejected
    req_diff_endpoint = ConsentRequest.from_capture(
        captured_1,
        **(base_kwargs | {"endpoint_url": "https://evil.example.com/v1/chat/completions"}),
    )
    with pytest.raises(ConsentDeclinedError, match="endpoint does not match"):
        decision.validate_for_request(req_diff_endpoint, captured_1)

    # 5. Changed model for the SAME image is rejected
    req_diff_model = ConsentRequest.from_capture(
        captured_1,
        **(base_kwargs | {"model": "other-model"}),
    )
    with pytest.raises(ConsentDeclinedError, match="model does not match"):
        decision.validate_for_request(req_diff_model, captured_1)

    # 6. Changed task/step context or purpose for the SAME image is rejected
    req_diff_step = ConsentRequest.from_capture(
        captured_1,
        **(base_kwargs | {"step_title": "Step 2"}),
    )
    with pytest.raises(ConsentDeclinedError, match="task/step context does not match"):
        decision.validate_for_request(req_diff_step, captured_1)

    req_diff_purpose = ConsentRequest.from_capture(
        captured_1,
        **(base_kwargs | {"purpose": "Different purpose"}),
    )
    with pytest.raises(ConsentDeclinedError, match="purpose does not match"):
        decision.validate_for_request(req_diff_purpose, captured_1)

    # 7. Exact matching request succeeds and consumes the approval
    decision.validate_for_request(req, captured_1, consume=True)
    assert decision.consumed is True

    # 8. Reusing the already-consumed approval for the exact same request is rejected
    with pytest.raises(ConsentDeclinedError, match="already been used"):
        decision.validate_for_request(req, captured_1)


def test_dialog_and_representations_exclude_raw_secrets_and_hash_fingerprints(
    qtbot: QtBot,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Dialog, repr, str, logs, and errors expose neither raw URL secrets nor hash fingerprints."""
    captured = _make_captured_image()
    common = {
        "purpose": "Verify step 1",
        "model": "gpt-4o-mini",
        "task_title": "Notepad Tutorial",
        "step_title": "Open File Menu",
        "system_prompt": "Analyze screenshot.",
        "user_prompt": "Check File menu.",
    }

    url_user = f"usr_{secrets.token_hex(4)}"
    url_pass = secrets.token_urlsafe(12)
    query_val_a = f"tenant_{secrets.token_hex(6)}"
    query_val_b = f"tenant_{secrets.token_hex(6)}"
    frag_val = f"frag_{secrets.token_hex(6)}"

    raw_url_a = (
        f"https://{url_user}:{url_pass}@api.example.com:8443/v1/chat/completions"
        f"?tenant={query_val_a}&mode=1#{frag_val}"
    )
    req_a = ConsentRequest.from_capture(captured, endpoint_url=raw_url_a, **common)

    dialog = ScreenShareConsentDialog(req_a)
    qtbot.addWidget(dialog)

    with caplog.at_level(logging.DEBUG, logger="octa.intelligence.consent"):
        manager = ConsentManager(handler=lambda _r: True)
        decision_a = manager.require_consent(req_a, captured)

    # Compute all candidate SHA-256 digests and short prefixes that must NEVER be displayed
    forbidden_Dynamic_strings = {
        url_user,
        url_pass,
        query_val_a,
        frag_val,
        req_a.endpoint_sha256,
        req_a.endpoint_sha256[:8],
        req_a.endpoint_sha256[:12],
        req_a.request_sha256,
        req_a.request_sha256[:8],
        req_a.request_sha256[:12],
    }
    for component in (
        f"{url_user}:{url_pass}",
        url_user,
        url_pass,
        query_val_a,
        "1",
        f"tenant={query_val_a}&mode=1",
        frag_val,
    ):
        digest = hashlib.sha256(component.encode("utf-8")).hexdigest()
        forbidden_Dynamic_strings.add(digest)
        forbidden_Dynamic_strings.add(digest[:8])
        forbidden_Dynamic_strings.add(digest[:12])

    surfaces = [
        dialog.get_disclosure_text(),
        dialog.get_prompt_preview_text(),
        req_a.to_display_summary(),
        req_a.endpoint_url,
        req_a.endpoint_disclosure,
        repr(req_a),
        str(req_a),
        repr(decision_a),
        str(decision_a),
        *[record.getMessage() for record in caplog.records],
    ]

    for surface in surfaces:
        for forbidden in forbidden_Dynamic_strings:
            assert forbidden not in surface

    # Verify the disclosure clearly shows sanitized base URL and redacted indicators
    disclosure = dialog.get_disclosure_text()
    assert "Destination Endpoint: https://api.example.com:8443/v1/chat/completions" in disclosure
    assert (
        "Endpoint Redacted Form: "
        "https://[REDACTED]@api.example.com:8443/v1/chat/completions"
        "?tenant=[REDACTED]&mode=[REDACTED]#[REDACTED]"
    ) in disclosure
    assert "Endpoint URL Credentials: present ([REDACTED])" in disclosure
    assert "Endpoint Query Parameters: present (tenant=[REDACTED], mode=[REDACTED])" in disclosure
    assert "Endpoint Fragment: present ([REDACTED])" in disclosure

    # Verify changing only a query value, only a credential, or only a fragment invalidates approval
    req_diff_query = ConsentRequest.from_capture(
        captured,
        endpoint_url=(
            f"https://{url_user}:{url_pass}@api.example.com:8443/v1/chat/completions"
            f"?tenant={query_val_b}&mode=1#{frag_val}"
        ),
        **common,
    )
    req_diff_cred = ConsentRequest.from_capture(
        captured,
        endpoint_url=(
            f"https://{url_user}:{secrets.token_urlsafe(12)}"
            f"@api.example.com:8443/v1/chat/completions"
            f"?tenant={query_val_a}&mode=1#{frag_val}"
        ),
        **common,
    )
    req_diff_frag = ConsentRequest.from_capture(
        captured,
        endpoint_url=(
            f"https://{url_user}:{url_pass}@api.example.com:8443/v1/chat/completions"
            f"?tenant={query_val_a}&mode=1#other_frag"
        ),
        **common,
    )
    req_clean = ConsentRequest.from_capture(
        captured,
        endpoint_url="https://api.example.com:8443/v1/chat/completions",
        **common,
    )

    for mismatched_req in (req_diff_query, req_diff_cred, req_diff_frag, req_clean):
        with pytest.raises(ConsentDeclinedError, match="endpoint does not match") as exc_info:
            decision_a.validate_for_request(mismatched_req, captured)
        for forbidden in forbidden_Dynamic_strings:
            assert forbidden not in str(exc_info.value)


def test_consent_manager_cancellation_raises_declined_error() -> None:
    """ConsentManager.require_consent raises ConsentDeclinedError when handler cancels."""
    captured = _make_captured_image()
    req = ConsentRequest.from_capture(
        captured,
        purpose="Verify step",
        endpoint_url="https://api.openai.com/v1/chat/completions",
        model="gpt-4o-mini",
        task_title="Task",
        step_title="Step",
        system_prompt="System prompt",
        user_prompt="Prompt text",
    )

    seen_requests: list[ConsentRequest] = []

    def cancel_handler(r: ConsentRequest) -> bool:
        seen_requests.append(r)
        return False

    manager = ConsentManager(handler=cancel_handler)
    with pytest.raises(ConsentDeclinedError, match="Cancelled by consent handler"):
        manager.require_consent(req, captured)

    assert seen_requests == [req]


def test_sanitize_endpoint_and_dialog_without_preview_bytes(qtbot: QtBot) -> None:
    """sanitize_endpoint_for_display strips userinfo and dialog handles empty preview."""
    from octa.intelligence.consent import sanitize_endpoint_for_display
    from octa.intelligence.exceptions import ConsentError

    cleaned = sanitize_endpoint_for_display(
        "https://user:secret@api.example.com:8443/v1/chat/completions?key=val"
    )
    assert cleaned == "https://api.example.com:8443/v1/chat/completions"
    assert "secret" not in cleaned

    with pytest.raises(ConsentError, match="Invalid endpoint URL"):
        sanitize_endpoint_for_display("file:///etc/passwd")

    req_no_preview = ConsentRequest(
        purpose="Test",
        endpoint_url="https://api.example.com/v1/chat/completions",
        model="gpt-4o-mini",
        task_title="Task",
        step_title="Step",
        system_prompt="System",
        user_prompt="Prompt",
        image_width=100,
        image_height=80,
        image_byte_size=512,
        capture_region=CaptureRegion(x=0, y=0, width=100, height=80),
        capture_source="full_screen",
        image_sha256="a" * 64,
        preview_png_bytes=b"",
    )
    dialog = ScreenShareConsentDialog(req_no_preview)
    qtbot.addWidget(dialog)
    assert dialog.has_image_preview() is False
