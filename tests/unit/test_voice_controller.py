"""Unit tests for push-to-talk VoiceInteractionController, task pipeline routing, and UI."""

from __future__ import annotations

import logging
import secrets

import pytest
from PySide6.QtCore import QObject, QProcess, Qt, Signal
from PySide6.QtMultimedia import QAudio, QAudioFormat
from pytestqt.qtbot import QtBot

from octa.communication.server import WebSocketServer
from octa.core.config import AppConfig
from octa.desktop.controller import DesktopController
from octa.desktop.window import DiagnosticWindow
from octa.intelligence.tasks import ExpectedOutcome, TaskDefinition, TaskStep
from octa.process.manager import GodotProcessManager
from octa.voice.audio import (
    MockAudioCaptureBackend,
    PushToTalkRecorder,
    encode_pcm16_to_wav_bytes,
)
from octa.voice.consent import VoiceConsentManager
from octa.voice.controller import VoiceInteractionController, VoiceState
from octa.voice.exceptions import (
    ContinuousRecordingForbiddenError,
    PushToTalkRequiredError,
    VoiceConfigurationError,
)
from octa.voice.providers import (
    DisabledSTTProvider,
    DisabledTTSProvider,
    MockAudioPlaybackBackend,
    MockSTTProvider,
    MockTTSProvider,
    QtAudioPlaybackBackend,
    RemoteHTTPTTSProvider,
)


def _build_sample_task() -> TaskDefinition:
    return TaskDefinition(
        task_id="notepad-save-task",
        title="Save a File in Notepad",
        application="Notepad",
        goal="Save a new text document to disk.",
        steps=(
            TaskStep(
                step_id="open-file-menu",
                order=1,
                title="Open File Menu",
                instruction="Click the File menu in the top-left corner of Notepad.",
                expected_outcome=ExpectedOutcome(
                    description="File menu dropdown is open.",
                    required_elements=("Save As",),
                ),
                hints=("You can also press Alt+F on your keyboard.",),
            ),
            TaskStep(
                step_id="click-save-as",
                order=2,
                title="Click Save As",
                instruction="Click Save As in the File menu to open the Save dialog.",
                expected_outcome=ExpectedOutcome(
                    description="Save As dialog is visible.",
                    required_elements=("File name",),
                ),
                hints=("Shortcut: Ctrl+Shift+S opens Save As directly.",),
            ),
        ),
    )


def test_push_to_talk_connects_spoken_requests_to_task_and_instruction_pipeline(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Spoken push-to-talk requests route through STT to TaskDefinition/TaskStep instructions."""
    task = _build_sample_task()
    secret_phrase = f"spoken_token_{secrets.token_hex(4)}"
    stt = MockSTTProvider(
        responses=[
            f"What is the current step? {secret_phrase}",
            "Can you give me a hint?",
            "Go to the next step please",
            "Go back to the previous step",
        ]
    )
    tts = MockTTSProvider()
    playback = MockAudioPlaybackBackend(auto_finish=True)
    recorder = PushToTalkRecorder(backend=MockAudioCaptureBackend())

    controller = VoiceInteractionController(
        recorder=recorder,
        stt_provider=stt,
        tts_provider=tts,
        playback_backend=playback,
        task=task,
    )

    mic_events: list[bool] = []
    controller.microphone_active_changed.connect(mic_events.append)

    ready_instructions: list[tuple[str, str]] = []
    controller.instruction_ready.connect(lambda sid, txt: ready_instructions.append((sid, txt)))

    with caplog.at_level(logging.DEBUG):
        # 1. Ask for current step
        controller.start_push_to_talk(user_initiated=True)
        assert controller.is_microphone_active is True
        assert controller.state == VoiceState.RECORDING

        inst_1 = controller.stop_push_to_talk_and_process(auto_speak_response=True)
        assert controller.is_microphone_active is False
        assert inst_1 is not None
        assert inst_1.step_id == "open-file-menu"
        assert "Click the File menu" in inst_1.instruction_text
        assert tts.call_count == 1
        assert playback.play_count == 1

        # 2. Ask for a hint on step 1
        controller.start_push_to_talk(user_initiated=True)
        inst_hint = controller.stop_push_to_talk_and_process()
        assert inst_hint is not None
        assert inst_hint.step_id == "open-file-menu"
        assert "Alt+F" in inst_hint.instruction_text

        # 3. Advance to next step via voice
        controller.start_push_to_talk(user_initiated=True)
        inst_next = controller.stop_push_to_talk_and_process()
        assert inst_next is not None
        assert inst_next.step_id == "click-save-as"
        assert controller.current_step_index == 1
        assert "Click Save As" in inst_next.instruction_text

        # 4. Return to previous step via voice
        controller.start_push_to_talk(user_initiated=True)
        inst_prev = controller.stop_push_to_talk_and_process()
        assert inst_prev is not None
        assert inst_prev.step_id == "open-file-menu"
        assert controller.current_step_index == 0

    assert mic_events == [True, False, True, False, True, False, True, False]
    assert len(ready_instructions) == 4

    # Verify spoken transcript phrase is never written to logs
    for rec in caplog.records:
        assert secret_phrase not in rec.getMessage()


def test_voice_controller_immediate_cancel_and_wake_word_prohibition() -> None:
    """cancel_or_stop() immediately stops recording or playback; wake-word is forbidden."""
    capture_backend = MockAudioCaptureBackend()
    recorder = PushToTalkRecorder(backend=capture_backend)
    stt = MockSTTProvider()
    tts = MockTTSProvider()
    playback = MockAudioPlaybackBackend(auto_finish=False)

    controller = VoiceInteractionController(
        recorder=recorder,
        stt_provider=stt,
        tts_provider=tts,
        playback_backend=playback,
        task=_build_sample_task(),
    )

    # Require user_initiated=True
    with pytest.raises(PushToTalkRequiredError):
        controller.start_push_to_talk(user_initiated=False)

    # Wake-word and continuous recording are forbidden
    with pytest.raises(ContinuousRecordingForbiddenError):
        controller.enable_wake_word()
    with pytest.raises(ContinuousRecordingForbiddenError):
        controller.enable_continuous_recording()

    # Start push-to-talk, then immediately cancel
    controller.start_push_to_talk(user_initiated=True)
    assert controller.is_microphone_active is True
    assert controller.state == VoiceState.RECORDING

    controller.cancel_or_stop()
    assert controller.is_microphone_active is False
    assert controller.state == VoiceState.IDLE
    assert capture_backend.cancel_count == 1
    assert stt.call_count == 0

    # Start speaking instruction aloud, then immediately cancel/stop playback
    controller.speak_current_instruction(user_initiated=True)
    assert controller.is_speaking is True
    assert controller.state == VoiceState.SPEAKING

    controller.cancel_or_stop()
    assert controller.is_speaking is False
    assert playback.is_playing is False
    assert controller.state == VoiceState.IDLE


def test_desktop_window_makes_microphone_activity_visible_and_supports_cancel(
    qtbot: QtBot,
) -> None:
    """DiagnosticWindow visibly shows live microphone activity, reads aloud, and cancels."""
    mock_process = QProcess()
    process_mgr = GodotProcessManager(process=mock_process)
    server = WebSocketServer(port=0)

    stt = MockSTTProvider(responses=["Next step"])
    tts = MockTTSProvider()
    playback = MockAudioPlaybackBackend(auto_finish=False)
    voice_ctrl = VoiceInteractionController(
        recorder=PushToTalkRecorder(backend=MockAudioCaptureBackend()),
        stt_provider=stt,
        tts_provider=tts,
        playback_backend=playback,
        task=_build_sample_task(),
    )

    controller = DesktopController(
        process_manager=process_mgr,
        server=server,
        voice_controller=voice_ctrl,
    )
    window = DiagnosticWindow(controller, tray_enabled=True)
    qtbot.addWidget(window)

    # Initial state: microphone is inactive
    assert window.is_microphone_indicator_active() is False
    assert "INACTIVE" in window.get_microphone_status()
    assert window.get_voice_state() == "Voice State: IDLE"
    assert "Click the File menu" in window.get_current_instruction_text()

    # Click Push to Talk button -> microphone becomes visibly RECORDING (LIVE)
    qtbot.mouseClick(window._btn_ptt, Qt.MouseButton.LeftButton)
    assert window.is_microphone_indicator_active() is True
    assert "RECORDING" in window.get_microphone_status()
    assert window.get_voice_state() == "Voice State: RECORDING"

    # Click Push to Talk again to release/finish -> transcribes and advances to step 2
    qtbot.mouseClick(window._btn_ptt, Qt.MouseButton.LeftButton)
    assert window.is_microphone_indicator_active() is False
    assert "INACTIVE" in window.get_microphone_status()
    assert window.get_voice_state() == "Voice State: IDLE"
    assert "Click Save As" in window.get_current_instruction_text()

    # Click Read Instruction Aloud -> transitions to SPEAKING
    qtbot.mouseClick(window._btn_read_aloud, Qt.MouseButton.LeftButton)
    assert window.get_voice_state() == "Voice State: SPEAKING"
    assert controller.is_speaking is True

    # Click Stop / Cancel Voice -> immediately stops TTS playback and returns to IDLE
    qtbot.mouseClick(window._btn_voice_cancel, Qt.MouseButton.LeftButton)
    assert controller.is_speaking is False
    assert window.is_microphone_indicator_active() is False
    assert window.get_voice_state() == "Voice State: IDLE"


class _FakeQtAudioOutputDevice:
    """Test double for QAudioDevice verifying WAV format support in QtAudioPlaybackBackend."""

    def isNull(self) -> bool:  # noqa: N802
        return False

    def isFormatSupported(self, fmt: QAudioFormat) -> bool:  # noqa: N802
        return (
            fmt.sampleRate() > 0
            and fmt.channelCount() in (1, 2)
            and fmt.sampleFormat() == QAudioFormat.SampleFormat.Int16
        )


class _FakeQAudioSink(QObject):
    """Test double for QAudioSink capturing the in-memory QBuffer PCM stream."""

    stateChanged = Signal(object)

    def __init__(self, device: object, fmt: QAudioFormat) -> None:
        super().__init__()
        self.device = device
        self.format = fmt
        self.started_with_bytes = b""
        self.start_count = 0
        self.stop_count = 0
        self.reset_count = 0

    def start(self, io_device: object) -> None:
        self.start_count += 1
        read_all = getattr(io_device, "readAll", None)
        if callable(read_all):
            self.started_with_bytes = bytes(read_all().data())
        self.stateChanged.emit(QAudio.State.ActiveState)

    def reset(self) -> None:
        self.reset_count += 1

    def stop(self) -> None:
        self.stop_count += 1


def test_production_from_app_config_wires_qt_playback_and_plays_remote_wav_speech(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """from_app_config wires QtAudioPlaybackBackend and plays remote WAV audio end-to-end."""
    # 1. Default controller and from_app_config both inject QtAudioPlaybackBackend (not mock)
    default_ctrl = VoiceInteractionController()
    assert isinstance(default_ctrl.playback_backend, QtAudioPlaybackBackend)

    cfg = AppConfig(
        voice_stt_provider="disabled",
        voice_tts_provider="remote_http",
        voice_tts_endpoint="https://speech.example.com/v1/audio/speech",
        voice_tts_model="tts-1",
        voice_tts_voice="alloy",
        voice_tts_api_key="prod-tts-secret-key",
    )
    prod_ctrl = VoiceInteractionController.from_app_config(
        cfg,
        consent_manager=VoiceConsentManager(handler=lambda _r: True),
        task=_build_sample_task(),
    )
    assert isinstance(prod_ctrl.tts_provider, RemoteHTTPTTSProvider)
    assert isinstance(prod_ctrl.playback_backend, QtAudioPlaybackBackend)

    # 2. Wire QtAudioPlaybackBackend to inspectable Qt sink & device and mock HTTPS WAV reply
    expected_pcm = b"\x24\x00" * 1200  # 2400 bytes of 16-bit PCM frames
    wav_response = encode_pcm16_to_wav_bytes(expected_pcm, sample_rate=24000, channels=1)

    created_sinks: list[_FakeQAudioSink] = []

    def fake_sink_factory(device: object, fmt: QAudioFormat) -> _FakeQAudioSink:
        sink = _FakeQAudioSink(device, fmt)
        created_sinks.append(sink)
        return sink

    prod_ctrl.playback_backend._device_provider = _FakeQtAudioOutputDevice
    prod_ctrl.playback_backend._sink_factory = fake_sink_factory
    monkeypatch.setattr(
        prod_ctrl.tts_provider,
        "_transport",
        lambda _req, _timeout: wav_response,
    )

    # Trigger Read Instruction Aloud on the configured production controller
    speech = prod_ctrl.speak_current_instruction(user_initiated=True)
    assert speech.byte_size == len(wav_response)
    assert len(created_sinks) == 1
    sink = created_sinks[0]
    assert sink.start_count == 1
    assert sink.format.sampleRate() == 24000
    assert sink.format.channelCount() == 1
    assert sink.started_with_bytes == expected_pcm
    assert prod_ctrl.is_speaking is True
    assert prod_ctrl.state == VoiceState.SPEAKING

    # Simulate QAudioSink finishing playback naturally (IdleState) -> returns to IDLE
    sink.stateChanged.emit(QAudio.State.IdleState)
    assert prod_ctrl.is_speaking is False
    assert prod_ctrl.playback_backend.is_playing is False
    assert prod_ctrl.state == VoiceState.IDLE


def test_disabled_providers_are_clearly_indicated_in_ui_and_never_pretend_to_speak(
    qtbot: QtBot,
) -> None:
    """Disabled STT/TTS providers show disabled status in UI and never record or play audio."""
    mock_process = QProcess()
    process_mgr = GodotProcessManager(process=mock_process)
    server = WebSocketServer(port=0)
    playback = MockAudioPlaybackBackend(auto_finish=False)

    # Construct controller from default AppConfig (both STT and TTS are 'disabled')
    default_cfg = AppConfig()
    voice_ctrl = VoiceInteractionController.from_app_config(
        default_cfg,
        playback_backend=playback,
        task=_build_sample_task(),
    )
    assert isinstance(voice_ctrl.stt_provider, DisabledSTTProvider)
    assert isinstance(voice_ctrl.tts_provider, DisabledTTSProvider)
    assert voice_ctrl.is_stt_enabled is False
    assert voice_ctrl.is_tts_enabled is False

    controller = DesktopController(
        config=default_cfg,
        process_manager=process_mgr,
        server=server,
        voice_controller=voice_ctrl,
    )
    window = DiagnosticWindow(controller, tray_enabled=True)
    qtbot.addWidget(window)

    # 1. UI clearly indicates both STT and TTS providers are disabled
    provider_status = window.get_voice_provider_status()
    assert "STT=disabled (Disabled)" in provider_status
    assert "TTS=disabled (Disabled)" in provider_status

    # 2. Clicking Read Instruction Aloud when TTS is disabled never plays audio or enters SPEAKING
    qtbot.mouseClick(window._btn_read_aloud, Qt.MouseButton.LeftButton)
    assert playback.play_count == 0
    assert controller.is_speaking is False
    assert "ERROR" in window.get_voice_state()
    assert "No Text-to-Speech (TTS) provider is configured" in window.get_voice_state()
    assert "No Text-to-Speech (TTS) provider is configured" in window.get_log_text()

    # 3. Clicking Push to Talk when STT is disabled never activates the microphone
    qtbot.mouseClick(window._btn_ptt, Qt.MouseButton.LeftButton)
    assert controller.is_microphone_active is False
    assert window.is_microphone_indicator_active() is False
    assert "ERROR" in window.get_voice_state()
    assert "Speech-to-Text (STT) is disabled" in window.get_voice_state()
    assert "Speech-to-Text (STT) is disabled" in window.get_log_text()

    # Direct API calls also raise VoiceConfigurationError without recording or speaking
    with pytest.raises(VoiceConfigurationError, match="No Text-to-Speech"):
        voice_ctrl.speak_current_instruction(user_initiated=True)
    assert playback.play_count == 0
