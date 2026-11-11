"""Unit tests for the Phase 6.0 End-to-End Tutorial Engine and UI integration."""

from __future__ import annotations

import json
import threading
import urllib.request
from typing import Any

import pytest
from PySide6.QtGui import QColor, QImage

from octa.core.config import AppConfig
from octa.desktop.controller import DesktopController
from octa.desktop.window import DiagnosticWindow
from octa.intelligence.capture import CaptureRegion, ScreenCapturer, encode_qimage_to_png_bytes
from octa.intelligence.consent import ConsentManager, ConsentRequest
from octa.intelligence.exceptions import (
    ConsentDeclinedError,
    TaskValidationError,
    UserActionRequiredError,
)
from octa.intelligence.llm import LLMConfig, OpenAICompatibleAdapter
from octa.intelligence.overlay import GuidanceOverlayWidget
from octa.intelligence.verifier import ProgressStatus, ProgressVerifier
from octa.tutorials.catalog import (
    build_notepad_save_tutorial,
    build_octa_welcome_tutorial,
)
from octa.tutorials.engine import TutorialEngine, TutorialSessionState
from octa.voice.audio import (
    MockAudioCaptureBackend,
    PushToTalkRecorder,
)
from octa.voice.controller import VoiceInteractionController
from octa.voice.providers import (
    MockAudioPlaybackBackend,
    MockSTTProvider,
    MockTTSProvider,
)


def _make_mock_capturer() -> ScreenCapturer:
    def _grabber(region: CaptureRegion | None) -> tuple[bytes, int, int, CaptureRegion]:
        actual = region if region is not None else CaptureRegion(0, 0, 800, 600)
        img = QImage(actual.width, actual.height, QImage.Format.Format_ARGB32)
        img.fill(QColor(40, 80, 120))
        return encode_qimage_to_png_bytes(img), actual.width, actual.height, actual

    return ScreenCapturer(grabber=_grabber, save_screenshots=False)


def _make_llm_response(content_obj: dict[str, Any]) -> bytes:
    payload = {
        "id": "chatcmpl-tutorial",
        "object": "chat.completion",
        "model": "gpt-4o-mini",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": json.dumps(content_obj)},
                "finish_reason": "stop",
            }
        ],
    }
    return json.dumps(payload).encode("utf-8")


def test_text_based_tutorial_flow_without_ai_or_voice(qapp: object) -> None:
    """When AI and voice are unconfigured, the text-based tutorial flow works end-to-end."""
    config = AppConfig(
        llm_api_key="",
        voice_stt_provider="disabled",
        voice_tts_provider="disabled",
    )
    overlay = GuidanceOverlayWidget()
    engine = TutorialEngine.from_app_config(
        config,
        capturer=_make_mock_capturer(),
        overlay=overlay,
        overlay_enabled=True,
    )

    try:
        assert not engine.is_ai_configured
        assert not engine.is_voice_configured
        assert engine.state == TutorialSessionState.ACTIVE
        assert "Text / Manual Flow Active" in engine.get_verification_mode_summary()

        # Select Notepad 3-step tutorial
        inst = engine.select_tutorial("windows-notepad-save")
        assert engine.active_task.task_id == "windows-notepad-save"
        assert engine.current_step_index == 0
        assert engine.total_steps == 3
        assert "Step 1: Type a Short Note" in inst.instruction_text
        assert len(overlay.highlights) == 1
        assert len(overlay.arrows) == 0

        # Request hint in text flow
        hint_inst = engine.request_hint()
        assert "Hint for Step 1" in hint_inst.instruction_text
        assert "Win+R" in hint_inst.instruction_text

        # Navigate forward and backward
        step2_inst = engine.next_step()
        assert engine.current_step_index == 1
        assert "Step 2: Open the File Menu" in step2_inst.instruction_text
        assert len(overlay.highlights) == 0
        assert len(overlay.arrows) == 1

        back_inst = engine.previous_step()
        assert engine.current_step_index == 0
        assert "Step 1: Type a Short Note" in back_inst.instruction_text

        # Manual completion requires user_initiated=True
        with pytest.raises(UserActionRequiredError):
            engine.complete_current_step_manually(user_initiated=False)

        companion_states: list[str] = []
        completed_events: list[tuple[str, str]] = []
        engine.companion_state_requested.connect(companion_states.append)
        engine.tutorial_completed.connect(lambda tid, title: completed_events.append((tid, title)))

        # Complete all 3 steps manually via the text flow
        engine.complete_current_step_manually(user_initiated=True)
        assert engine.current_step_index == 1
        assert engine.step_records[0].completed is True
        assert engine.step_records[0].completion_mode == "manual_text"
        assert engine.completed_steps_count == 1

        engine.complete_current_step_manually(user_initiated=True)
        assert engine.current_step_index == 2
        assert engine.step_records[1].completed is True
        assert engine.completed_steps_count == 2

        final_inst = engine.complete_current_step_manually(user_initiated=True)
        assert engine.state == TutorialSessionState.COMPLETED
        assert engine.completed_steps_count == 3
        assert "Tutorial Complete!" in final_inst.instruction_text
        assert completed_events == [
            ("windows-notepad-save", "Create and Save a Note in Windows Notepad")
        ]
        assert "SUCCESS" in companion_states
        # Overlay is cleared upon tutorial completion
        assert len(overlay.highlights) == 0
        assert len(overlay.arrows) == 0

        # Reset returns to Step 1 with all steps uncompleted
        engine.reset_tutorial()
        assert engine.state == TutorialSessionState.ACTIVE
        assert engine.current_step_index == 0
        assert engine.completed_steps_count == 0
    finally:
        engine.shutdown()


def test_ai_verification_runs_off_ui_thread_after_single_use_consent(qapp: object) -> None:
    """Consent is obtained on the UI thread; LLM verification executes on a worker QThread."""
    ui_thread_id = threading.get_ident()
    consent_thread_ids: list[int] = []
    transport_thread_ids: list[int] = []

    def _consent_prompt(req: ConsentRequest) -> bool:
        consent_thread_ids.append(threading.get_ident())
        # Consent discloses the exact outbound request without exposing the API key
        assert req.image_sha256
        assert req.task_title == "Getting Started with OCTA"
        assert "sk-test-secret-do-not-log" not in repr(req)
        return True

    consent_manager = ConsentManager(handler=_consent_prompt)

    responses = [
        _make_llm_response(
            {
                "status": "complete",
                "confidence": 0.94,
                "reasoning": "The application window is open and visible.",
                "observed_elements": ["Application window"],
                "missing_elements": [],
                "next_instruction": "Proceed to Step 2.",
                "overlay_cues": [],
            }
        ),
        _make_llm_response(
            {
                "status": "complete",
                "confidence": 0.96,
                "reasoning": "The guided action is complete.",
                "observed_elements": ["Completed state"],
                "missing_elements": [],
                "next_instruction": "",
                "overlay_cues": [],
            }
        ),
    ]

    def _mock_transport(_req: urllib.request.Request, _timeout: float) -> bytes:
        transport_thread_ids.append(threading.get_ident())
        return responses.pop(0)

    llm_config = LLMConfig(
        endpoint="https://api.example.com/v1/chat/completions",
        api_key="sk-test-secret-do-not-log",
        model="gpt-4o-mini",
    )
    adapter = OpenAICompatibleAdapter(
        llm_config,
        consent_manager=consent_manager,
        transport=_mock_transport,
    )
    verifier = ProgressVerifier(adapter)
    overlay = GuidanceOverlayWidget()

    engine = TutorialEngine(
        capturer=_make_mock_capturer(),
        consent_manager=consent_manager,
        llm_adapter=adapter,
        verifier=verifier,
        overlay=overlay,
        tutorials=(build_octa_welcome_tutorial(),),
        overlay_enabled=True,
    )

    try:
        assert engine.is_ai_configured is True

        # Must require explicit user action
        with pytest.raises(UserActionRequiredError):
            engine.verify_current_step(user_initiated=False)

        # Verify Step 1
        op_id = engine.verify_current_step(user_initiated=True)
        assert op_id > 0
        assert engine.wait_for_active_operation(timeout_ms=3000) is True

        assert consent_thread_ids == [ui_thread_id]
        assert len(transport_thread_ids) == 1
        assert transport_thread_ids[0] != ui_thread_id
        assert engine.last_worker_thread_id == transport_thread_ids[0]

        assert engine.current_step_index == 1
        assert engine.step_records[0].completed is True
        assert engine.step_records[0].completion_mode == "verified_ai"
        assert engine.last_verification_result is not None
        assert engine.last_verification_result.status == ProgressStatus.COMPLETE

        # Verify Step 2 (final step)
        engine.verify_current_step(user_initiated=True)
        assert engine.wait_for_active_operation(timeout_ms=3000) is True

        assert engine.state == TutorialSessionState.COMPLETED
        assert engine.completed_steps_count == 2
    finally:
        engine.shutdown()


def test_declined_consent_aborts_before_background_network_call(qapp: object) -> None:
    """Declining single-use consent raises ConsentDeclinedError and never calls transport."""
    transport_called = False

    def _mock_transport(_req: urllib.request.Request, _timeout: float) -> bytes:
        nonlocal transport_called
        transport_called = True
        return b""

    consent_manager = ConsentManager(handler=lambda _req: False)
    llm_config = LLMConfig(
        endpoint="https://api.example.com/v1/chat/completions",
        api_key="sk-test-secret",
        model="gpt-4o-mini",
    )
    adapter = OpenAICompatibleAdapter(
        llm_config,
        consent_manager=consent_manager,
        transport=_mock_transport,
    )
    engine = TutorialEngine(
        capturer=_make_mock_capturer(),
        consent_manager=consent_manager,
        llm_adapter=adapter,
        verifier=ProgressVerifier(adapter),
    )

    try:
        with pytest.raises(ConsentDeclinedError):
            engine.verify_current_step(user_initiated=True)

        assert transport_called is False
        assert engine.state == TutorialSessionState.ACTIVE
        assert engine.current_step_index == 0
    finally:
        engine.shutdown()


def test_incomplete_and_uncertain_verification_keep_step_active_with_feedback(
    qapp: object,
) -> None:
    """INCOMPLETE or UNCERTAIN results stay on the current step and display corrective cues."""
    consent_manager = ConsentManager(handler=lambda _req: True)
    responses = [
        _make_llm_response(
            {
                "status": "incomplete",
                "confidence": 0.88,
                "reasoning": "Notepad is not open yet.",
                "missing_elements": ["Notepad"],
                "next_instruction": "Launch Notepad first.",
                "overlay_cues": [
                    {
                        "kind": "highlight",
                        "label": "Taskbar Search",
                        "color": "#FF5252",
                        "bounds": {"x": 20, "y": 500, "width": 200, "height": 50},
                    }
                ],
            }
        ),
        _make_llm_response(
            {
                "status": "complete",
                "confidence": 0.45,  # Low confidence triggers conservative downgrade to UNCERTAIN
                "reasoning": "Cannot clearly tell if text was typed.",
                "missing_elements": [],
                "next_instruction": "Type visible text in the editor.",
                "overlay_cues": [],
            }
        ),
    ]

    llm_config = LLMConfig(
        endpoint="https://api.example.com/v1/chat/completions",
        api_key="sk-test-secret",
        model="gpt-4o-mini",
    )
    adapter = OpenAICompatibleAdapter(
        llm_config,
        consent_manager=consent_manager,
        transport=lambda _req, _timeout: responses.pop(0),
    )
    overlay = GuidanceOverlayWidget()
    engine = TutorialEngine(
        capturer=_make_mock_capturer(),
        consent_manager=consent_manager,
        llm_adapter=adapter,
        verifier=ProgressVerifier(adapter),
        overlay=overlay,
        tutorials=(build_notepad_save_tutorial(),),
        overlay_enabled=True,
    )

    try:
        # 1. Incomplete result stays on Step 1 and applies corrective highlight cue
        engine.verify_current_step(user_initiated=True)
        assert engine.wait_for_active_operation(timeout_ms=3000) is True
        assert engine.state == TutorialSessionState.ACTIVE
        assert engine.current_step_index == 0
        assert engine.step_records[0].completed is False
        assert engine.last_verification_result is not None
        assert engine.last_verification_result.status == ProgressStatus.INCOMPLETE
        assert "Notepad is not open yet" in engine.current_instruction.instruction_text
        assert "Missing: Notepad" in engine.current_instruction.instruction_text
        assert len(overlay.highlights) == 1
        assert overlay.highlights[0].label == "Taskbar Search"

        # 2. Low-confidence "complete" is downgraded to UNCERTAIN and stays on Step 1
        engine.verify_current_step(user_initiated=True)
        assert engine.wait_for_active_operation(timeout_ms=3000) is True
        assert engine.state == TutorialSessionState.ACTIVE
        assert engine.current_step_index == 0
        assert engine.last_verification_result.status == ProgressStatus.UNCERTAIN
        assert "Uncertain" in engine.current_instruction.instruction_text
    finally:
        engine.shutdown()


def test_missing_credentials_guides_user_to_text_flow_without_exposing_secrets(
    qapp: object,
) -> None:
    """When LLM API key is empty, verify_current_step reports a clear fallback message."""
    consent_manager = ConsentManager(handler=lambda _req: True)
    llm_config = LLMConfig(
        endpoint="https://api.example.com/v1/chat/completions",
        api_key=None,
        model="gpt-4o-mini",
    )
    adapter = OpenAICompatibleAdapter(llm_config, consent_manager=consent_manager)
    engine = TutorialEngine(
        capturer=_make_mock_capturer(),
        consent_manager=consent_manager,
        llm_adapter=adapter,
        verifier=ProgressVerifier(adapter),
    )

    try:
        engine.verify_current_step(user_initiated=True)
        assert engine.wait_for_active_operation(timeout_ms=3000) is True
        assert engine.state == TutorialSessionState.ACTIVE
        assert "OCTA_LLM_API_KEY is not configured" in engine.last_error
        assert "Complete Step (Text Flow)" in engine.last_error
    finally:
        engine.shutdown()


def test_cancellation_and_clean_shutdown_of_slow_background_verification(
    qapp: object,
) -> None:
    """In-flight background verification can be cancelled immediately and shut down cleanly."""
    worker_entered = threading.Event()
    release_worker = threading.Event()

    def _slow_transport(_req: urllib.request.Request, _timeout: float) -> bytes:
        worker_entered.set()
        release_worker.wait(timeout=2.0)
        return _make_llm_response(
            {
                "status": "complete",
                "confidence": 0.95,
                "reasoning": "Late response after cancellation.",
                "missing_elements": [],
                "next_instruction": "",
                "overlay_cues": [],
            }
        )

    consent_manager = ConsentManager(handler=lambda _req: True)
    llm_config = LLMConfig(
        endpoint="https://api.example.com/v1/chat/completions",
        api_key="sk-test-secret",
        model="gpt-4o-mini",
    )
    adapter = OpenAICompatibleAdapter(
        llm_config,
        consent_manager=consent_manager,
        transport=_slow_transport,
    )
    engine = TutorialEngine(
        capturer=_make_mock_capturer(),
        consent_manager=consent_manager,
        llm_adapter=adapter,
        verifier=ProgressVerifier(adapter),
    )

    try:
        engine.verify_current_step(user_initiated=True)
        assert engine.state == TutorialSessionState.VERIFYING
        assert engine.is_busy is True
        assert worker_entered.wait(timeout=1.5) is True

        # Cancel while background worker is still blocked
        cancelled = engine.cancel_active_operation()
        assert cancelled is True
        assert engine.state == TutorialSessionState.ACTIVE
        assert engine.is_busy is False

        # Release the worker and wait for it to finish; cancelled result must be ignored
        release_worker.set()
        assert engine.wait_for_active_operation(timeout_ms=2000) is True
        assert engine.current_step_index == 0
        assert engine.step_records[0].completed is False
    finally:
        release_worker.set()
        engine.shutdown(timeout_ms=1000)
        assert engine.state == TutorialSessionState.IDLE


def test_voice_navigation_synchronizes_with_tutorial_engine(qapp: object) -> None:
    """Push-to-talk spoken commands synchronize step and instruction state with TutorialEngine."""
    recorder = PushToTalkRecorder(backend=MockAudioCaptureBackend())
    stt = MockSTTProvider(responses=["next step"])
    tts = MockTTSProvider()
    playback = MockAudioPlaybackBackend(auto_finish=True)
    voice_ctrl = VoiceInteractionController(
        recorder=recorder,
        stt_provider=stt,
        tts_provider=tts,
        playback_backend=playback,
    )

    engine = TutorialEngine(
        capturer=_make_mock_capturer(),
        voice_controller=voice_ctrl,
        tutorials=(build_notepad_save_tutorial(),),
    )

    try:
        assert engine.is_voice_configured is True
        assert engine.current_step_index == 0

        # Use Push-to-Talk to say "next step"
        voice_ctrl.start_push_to_talk(user_initiated=True)
        voice_ctrl.stop_push_to_talk_and_process(auto_speak_response=True)

        assert engine.current_step_index == 1
        assert "Step 2" in engine.current_instruction.instruction_text
        assert playback.play_count == 1
    finally:
        engine.shutdown()


def test_diagnostic_window_and_desktop_controller_tutorial_flow(qapp: object) -> None:
    """DiagnosticWindow controls drive TutorialEngine through DesktopController."""
    config = AppConfig(
        llm_api_key="",
        voice_stt_provider="disabled",
        voice_tts_provider="disabled",
    )
    controller = DesktopController(config=config, headless=True)
    window = DiagnosticWindow(controller, tray_enabled=False)

    try:
        assert window.get_selected_tutorial_id() == "octa-welcome-task"
        assert "Getting Started with OCTA" in window.get_tutorial_status()
        assert "Text / Manual Flow Active" in window.get_verification_status()

        # Select Windows File Explorer tutorial via combo box
        window._combo_tutorial.setCurrentIndex(2)
        assert window.get_selected_tutorial_id() == "windows-explorer-new-folder"
        assert "Windows File Explorer" in window.get_tutorial_status()

        # Click Show Hint
        window._btn_hint.click()
        assert "Win+E" in window.get_current_instruction_text()

        # Click Toggle Overlay
        assert controller.tutorial_engine.is_overlay_enabled is False
        window._btn_toggle_overlay.click()
        assert controller.tutorial_engine.is_overlay_enabled is True
        assert window._btn_toggle_overlay.text() == "Disable Overlay"

        # Click Complete Step (Text Flow) for both steps of Explorer tutorial
        window._btn_complete_manual.click()
        assert "Step 2/2" in window.get_tutorial_status()

        window._btn_complete_manual.click()
        assert "[COMPLETED]" in window.get_tutorial_status()
        assert "Tutorial Complete!" in window.get_current_instruction_text()

        # Reset tutorial via button
        window._btn_reset_tutorial.click()
        assert "Step 1/2" in window.get_tutorial_status()
        assert "[ACTIVE]" in window.get_tutorial_status()
    finally:
        window.close()
        controller.stop(timeout_ms=500)


def test_engine_adopts_custom_task_preloaded_in_voice_controller(qapp: object) -> None:
    """A custom task already loaded into the voice controller is kept, not overwritten."""
    custom_task = build_notepad_save_tutorial()
    voice_ctrl = VoiceInteractionController(
        recorder=PushToTalkRecorder(backend=MockAudioCaptureBackend()),
        stt_provider=MockSTTProvider(),
        tts_provider=MockTTSProvider(),
        playback_backend=MockAudioPlaybackBackend(auto_finish=True),
        task=custom_task,
    )
    engine = TutorialEngine(
        capturer=_make_mock_capturer(),
        voice_controller=voice_ctrl,
        tutorials=(build_octa_welcome_tutorial(),),
    )
    try:
        assert engine.active_task.task_id == "windows-notepad-save"
        assert engine.available_tutorials[0].task_id == "windows-notepad-save"
        assert voice_ctrl.active_task.task_id == "windows-notepad-save"
        assert "Type a Short Note" in engine.current_instruction.instruction_text
    finally:
        engine.shutdown()


def test_invalid_tutorial_catalog_or_selection_raises_validation_error(qapp: object) -> None:
    with pytest.raises(TaskValidationError, match="at least one TaskDefinition"):
        TutorialEngine(tutorials=())

    engine = TutorialEngine(capturer=_make_mock_capturer())
    try:
        with pytest.raises(TaskValidationError, match="Unknown tutorial task_id"):
            engine.select_tutorial("unknown-id")
    finally:
        engine.shutdown()
