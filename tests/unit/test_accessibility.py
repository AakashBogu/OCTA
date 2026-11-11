"""Unit tests for OCTA Phase 8.3 accessibility adaptations.

Covers configuration parsing, high-contrast overlay cues, non-color shape indicators,
text scaling, tutorial completion audio cue synthesis, sound player enable/disable,
and UI accessibility properties and keyboard navigation.
"""

from __future__ import annotations

import io
import wave
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QProcess, Qt
from pytestqt.qtbot import QtBot

from octa.communication.server import WebSocketServer
from octa.core.config import AppConfig, ConfigError
from octa.desktop.controller import DesktopController
from octa.desktop.window import DiagnosticWindow, TutorialFeedbackDialog
from octa.intelligence.overlay import (
    ArrowSpec,
    GuidanceOverlayWidget,
    HighlightSpec,
    OverlayBounds,
)
from octa.process.manager import GodotProcessManager
from octa.tutorials.catalog import build_octa_welcome_tutorial
from octa.tutorials.engine import TutorialEngine
from octa.tutorials.sound import (
    TutorialSoundPlayer,
    create_completion_speech_cue,
    synthesize_completion_chime_wav,
)
from octa.voice.providers import MockAudioPlaybackBackend

# ──────────────────────────────────────────
# Configuration Tests
# ──────────────────────────────────────────


def test_accessibility_config_defaults() -> None:
    """AppConfig defaults accessibility settings to safe, standard values."""
    config = AppConfig()
    assert config.accessibility_high_contrast is False
    assert config.accessibility_text_scale == 1.0
    assert config.accessibility_sound_cues is False


def test_accessibility_config_overrides_and_validation() -> None:
    """AppConfig validates text scale range and boolean inputs."""
    # Valid overrides
    cfg = AppConfig(
        accessibility_high_contrast=True,
        accessibility_text_scale=1.5,
        accessibility_sound_cues=True,
    )
    assert cfg.accessibility_high_contrast is True
    assert cfg.accessibility_text_scale == 1.5
    assert cfg.accessibility_sound_cues is True

    # Scale out of range (< 0.5)
    with pytest.raises(ConfigError, match="accessibility_text_scale"):
        AppConfig(accessibility_text_scale=0.2)

    # Scale out of range (> 3.0)
    with pytest.raises(ConfigError, match="accessibility_text_scale"):
        AppConfig(accessibility_text_scale=3.5)

    # Invalid boolean
    with pytest.raises(ConfigError, match="accessibility_high_contrast"):
        AppConfig(accessibility_high_contrast="invalid_bool")  # type: ignore[arg-type]


def test_accessibility_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """AppConfig.load parses OCTA_ACCESSIBILITY_* environment variables."""
    monkeypatch.setenv("OCTA_ACCESSIBILITY_HIGH_CONTRAST", "true")
    monkeypatch.setenv("OCTA_ACCESSIBILITY_TEXT_SCALE", "1.75")
    monkeypatch.setenv("OCTA_ACCESSIBILITY_SOUND_CUES", "1")

    cfg = AppConfig.load()
    assert cfg.accessibility_high_contrast is True
    assert cfg.accessibility_text_scale == 1.75
    assert cfg.accessibility_sound_cues is True


# ──────────────────────────────────────────
# Overlay High-Contrast & Text Scaling Tests
# ──────────────────────────────────────────


def test_overlay_high_contrast_and_text_scale_properties(qtbot: QtBot) -> None:
    """GuidanceOverlayWidget supports toggling high contrast and adjusting text scale."""
    bounds = OverlayBounds(x=0, y=0, width=800, height=600)
    overlay = GuidanceOverlayWidget(screen_bounds=bounds, high_contrast=False, text_scale=1.0)
    qtbot.addWidget(overlay)

    assert overlay.high_contrast is False
    assert overlay.text_scale == 1.0

    overlay.set_high_contrast(True)
    assert overlay.high_contrast is True

    # Clamping behavior
    overlay.set_text_scale(0.1)
    assert overlay.text_scale == 0.5

    overlay.set_text_scale(5.0)
    assert overlay.text_scale == 3.0

    overlay.set_text_scale(1.25)
    assert overlay.text_scale == 1.25


def test_overlay_renders_high_contrast_annotations(qtbot: QtBot) -> None:
    """GuidanceOverlayWidget renders non-empty images with cues in high-contrast mode."""
    bounds = OverlayBounds(x=0, y=0, width=640, height=480)
    overlay = GuidanceOverlayWidget(screen_bounds=bounds, high_contrast=True, text_scale=1.5)
    qtbot.addWidget(overlay)

    overlay.add_highlight(
        HighlightSpec(x=50, y=50, width=200, height=100, label="Target Zone", stroke_width=3)
    )
    overlay.add_arrow(
        ArrowSpec(
            start_x=300, start_y=200, end_x=450, end_y=350, label="Click Here", stroke_width=3
        )
    )

    image = overlay.render_to_image()
    assert not image.isNull()
    assert image.width() == 640
    assert image.height() == 480

    # Ensure rendered image contains opaque or colored pixels
    has_rendered_pixels = False
    for y in range(50, 150, 10):
        for x in range(50, 250, 10):
            pixel = image.pixelColor(x, y)
            if pixel.alpha() > 0:
                has_rendered_pixels = True
                break
        if has_rendered_pixels:
            break
    assert has_rendered_pixels, "High-contrast highlight was not drawn onto image."


# ──────────────────────────────────────────
# Sound Cue Synthesis & Player Tests
# ──────────────────────────────────────────


def test_synthesize_completion_chime_wav_format() -> None:
    """synthesize_completion_chime_wav generates a valid RIFF PCM WAV buffer."""
    wav_bytes = synthesize_completion_chime_wav()
    assert len(wav_bytes) > 44  # WAV header is 44 bytes

    # Verify standard WAV header decoding
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == 16000
        nframes = wf.getnframes()
        assert nframes > 0
        duration_s = nframes / wf.getframerate()
        assert 0.35 <= duration_s <= 0.50  # ~420ms


def test_create_completion_speech_cue() -> None:
    """create_completion_speech_cue wraps chime in a valid SynthesizedSpeech instance."""
    cue = create_completion_speech_cue()
    assert cue.media_format == "wav"
    assert cue.provider_name == "accessibility_completion_chime"
    assert cue.sample_rate == 16000
    assert cue.channels == 1
    assert cue.duration_ms > 300
    assert len(cue.audio_bytes) > 0


def test_tutorial_sound_player_enable_disable() -> None:
    """TutorialSoundPlayer strictly respects user preference when playing completion cues."""
    mock_backend = MockAudioPlaybackBackend()
    player = TutorialSoundPlayer(enabled=False, backend=mock_backend)

    # Disabled: does not play
    assert player.enabled is False
    assert player.play_completion_cue() is False
    assert mock_backend.play_count == 0
    assert player.play_count == 0

    # Enabled: plays cue
    player.set_enabled(True)
    assert player.enabled is True
    assert player.play_completion_cue() is True
    assert mock_backend.play_count == 1
    assert player.play_count == 1
    assert mock_backend.last_speech is not None
    assert mock_backend.last_speech.provider_name == "accessibility_completion_chime"

    # Stop safety
    player.stop()
    assert mock_backend.stop_count >= 1


def test_tutorial_sound_player_error_handling() -> None:
    """TutorialSoundPlayer handles backend failures safely without raising exceptions."""
    broken_backend = MagicMock()
    broken_backend.play.side_effect = RuntimeError("Audio device disconnected")
    player = TutorialSoundPlayer(enabled=True, backend=broken_backend)

    # Fails safely and returns False
    result = player.play_completion_cue()
    assert result is False
    assert player.play_count == 0


# ──────────────────────────────────────────
# Tutorial Engine Integration Tests
# ──────────────────────────────────────────


def test_tutorial_engine_completion_sound_cue_integration(qtbot: QtBot) -> None:
    """TutorialEngine plays completion sound cue when tutorial finishes if enabled."""
    mock_backend = MockAudioPlaybackBackend()
    sound_player = TutorialSoundPlayer(enabled=True, backend=mock_backend)

    task = build_octa_welcome_tutorial()
    engine = TutorialEngine(
        tutorials=[task],
        sound_player=sound_player,
    )
    qtbot.addWidget(engine.overlay)

    assert engine.sound_cues_enabled is True
    assert mock_backend.play_count == 0

    # Complete all steps manually
    for _ in range(engine.total_steps):
        engine.complete_current_step_manually(user_initiated=True)

    assert engine.state.value == "COMPLETED"
    assert mock_backend.play_count == 1

    # Shutdown cleans up playback
    engine.shutdown()
    assert mock_backend.stop_count >= 1


def test_tutorial_engine_sound_cue_disabled_by_default(qtbot: QtBot) -> None:
    """TutorialEngine does not play sound cue when sound cues are disabled."""
    mock_backend = MockAudioPlaybackBackend()
    sound_player = TutorialSoundPlayer(enabled=False, backend=mock_backend)

    task = build_octa_welcome_tutorial()
    engine = TutorialEngine(
        tutorials=[task],
        sound_player=sound_player,
    )
    qtbot.addWidget(engine.overlay)

    for _ in range(engine.total_steps):
        engine.complete_current_step_manually(user_initiated=True)

    assert engine.state.value == "COMPLETED"
    assert mock_backend.play_count == 0


# ──────────────────────────────────────────
# Desktop Controller Accessibility Tests
# ──────────────────────────────────────────


def test_desktop_controller_accessibility_delegation(qtbot: QtBot) -> None:
    """DesktopController delegates accessibility changes and signals."""
    mock_process = QProcess()
    process_mgr = GodotProcessManager(process=mock_process)
    server = WebSocketServer(port=0)
    controller = DesktopController(process_manager=process_mgr, server=server)

    signals_received: list[tuple[bool, float, bool]] = []
    controller.accessibility_changed.connect(
        lambda hc, ts, sc: signals_received.append((hc, ts, sc))
    )

    controller.set_high_contrast(True)
    assert controller.accessibility_high_contrast is True
    assert signals_received[-1][0] is True

    controller.set_text_scale(1.5)
    assert controller.accessibility_text_scale == 1.5
    assert signals_received[-1][1] == 1.5

    controller.set_sound_cues_enabled(True)
    assert controller.accessibility_sound_cues is True
    assert signals_received[-1][2] is True

    # Test cue trigger
    played = controller.play_completion_sound_cue()
    assert isinstance(played, bool)


# ──────────────────────────────────────────
# Diagnostic Window UI Accessibility Tests
# ──────────────────────────────────────────


def test_diagnostic_window_accessibility_controls(qtbot: QtBot) -> None:
    """DiagnosticWindow wires high contrast, text scaling, and sound cue controls."""
    mock_process = QProcess()
    process_mgr = GodotProcessManager(process=mock_process)
    server = WebSocketServer(port=0)
    controller = DesktopController(process_manager=process_mgr, server=server)

    window = DiagnosticWindow(controller, tray_enabled=True)
    qtbot.addWidget(window)

    # Initial control state
    assert window.is_high_contrast_checked() is False
    assert window.is_sound_cues_checked() is False
    assert window.get_selected_text_scale() == 1.0

    # Test sound button respects audio preference (disabled by default)
    mock_backend = MockAudioPlaybackBackend()
    controller.tutorial_engine.sound_player.set_backend(mock_backend)
    window._btn_test_sound.click()
    assert mock_backend.play_count == 0

    # Toggle high contrast
    window._chk_high_contrast.setChecked(True)
    assert controller.accessibility_high_contrast is True

    # Toggle sound cues and test button again
    window._chk_sound_cues.setChecked(True)
    assert controller.accessibility_sound_cues is True
    window._btn_test_sound.click()
    assert mock_backend.play_count == 1

    # Change text scale via combo
    idx = window._combo_text_size.findData(1.5)
    assert idx >= 0
    window._combo_text_size.setCurrentIndex(idx)
    assert controller.accessibility_text_scale == 1.5
    assert window.get_selected_text_scale() == 1.5


def test_diagnostic_window_accessibility_attributes_and_tab_order(qtbot: QtBot) -> None:
    """DiagnosticWindow and TutorialFeedbackDialog define accessible names and focus policies."""
    mock_process = QProcess()
    process_mgr = GodotProcessManager(process=mock_process)
    server = WebSocketServer(port=0)
    controller = DesktopController(process_manager=process_mgr, server=server)

    window = DiagnosticWindow(controller, tray_enabled=True)
    qtbot.addWidget(window)

    # Check accessible names and descriptions
    assert window._btn_start.accessibleName() == "Start Companion"
    assert len(window._btn_start.accessibleDescription()) > 0
    assert window._chk_high_contrast.accessibleName() == "High-Contrast Overlay Cues"
    assert window._chk_sound_cues.accessibleName() == "Tutorial Completion Sound Cues"
    assert window._combo_text_size.accessibleName() == "Text Size Scaling"

    # Check focus policy
    assert window._btn_start.focusPolicy() == Qt.FocusPolicy.StrongFocus
    assert window._combo_text_size.focusPolicy() == Qt.FocusPolicy.StrongFocus
    assert window._chk_high_contrast.focusPolicy() == Qt.FocusPolicy.StrongFocus

    # Feedback dialog accessibility
    dialog = TutorialFeedbackDialog(task_title="Test Task", step_title="Step 1", parent=window)
    qtbot.addWidget(dialog)
    assert dialog._combo_rating.accessibleName() == "Tutorial Rating"
    assert dialog._combo_difficulty.accessibleName() == "Step Difficulty"
    assert dialog._txt_comment.accessibleName() == "Feedback Comments"
    assert dialog._btn_submit.accessibleName() == "Submit Feedback"
