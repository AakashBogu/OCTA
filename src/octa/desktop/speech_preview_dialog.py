"""In-editor speech preview controls and audio timing simulator (Phase 11.2).

Enables tutorial authors to preview spoken instructions and progressive hints,
calculate linguistically modeled speech durations, simulate recommended visual
cue display times, test TTS playback with configured providers and consent,
and preview synchronized visual overlay cues with strict privacy boundaries.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QTimer, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from octa.core.config import AppConfig
from octa.intelligence.consent import sanitize_endpoint_for_display
from octa.intelligence.overlay import (
    GuidanceOverlayWidget,
    OverlayBounds,
)
from octa.intelligence.tasks import TaskDefinition, TaskStep
from octa.tutorials.speech_timing import (
    DEFAULT_CUE_PADDING_SECONDS,
    DEFAULT_WORDS_PER_MINUTE,
    estimate_speech_timing,
)
from octa.voice.audio import SynthesizedSpeech
from octa.voice.consent import VoiceConsentManager
from octa.voice.exceptions import (
    VoiceCancelledError,
    VoiceConsentDeclinedError,
)
from octa.voice.providers import (
    AudioPlaybackBackend,
    DisabledTTSProvider,
    MockAudioPlaybackBackend,
    MockTTSProvider,
    TextToSpeechProvider,
    create_playback_backend,
    create_tts_provider,
)

if TYPE_CHECKING:
    from octa.desktop.controller import DesktopController

logger = logging.getLogger("octa.desktop.speech_preview")


class SpeechPreviewDialog(QDialog):
    """Authoring workbench for speech timing simulation and audio instruction preview."""

    def __init__(
        self,
        task: TaskDefinition,
        step: TaskStep,
        *,
        controller: DesktopController | None = None,
        tts_provider: TextToSpeechProvider | None = None,
        playback_backend: AudioPlaybackBackend | None = None,
        consent_manager: VoiceConsentManager | None = None,
        display_bounds: OverlayBounds | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Speech Preview & Timing Simulator — Step {step.order}: {step.title}")
        self.setMinimumSize(860, 640)
        self.setModal(True)

        self._task = task
        self._step = step
        self._controller = controller
        self._consent_manager = consent_manager

        # Resolve TTS provider and audio playback backend
        self._init_speech_providers(tts_provider, playback_backend)

        # Display bounds for synchronized overlay preview
        self._display_bounds = display_bounds
        if self._display_bounds is None and controller is not None:
            if hasattr(controller, "display_manager") and controller.display_manager:
                active_disp = controller.display_manager.active_display
                self._display_bounds = OverlayBounds(
                    x=active_disp.x,
                    y=active_disp.y,
                    width=active_disp.width,
                    height=active_disp.height,
                )

        # Internal state
        self._is_playing: bool = False
        self._active_speech: SynthesizedSpeech | None = None
        self._active_overlay: GuidanceOverlayWidget | None = None
        self._overlay_timer: QTimer | None = None
        self.applied_cue_duration: float | None = None

        self._init_ui()
        self._populate_initial_data()
        self._recalculate_timing()

    def _init_speech_providers(
        self,
        tts_provider: TextToSpeechProvider | None,
        playback_backend: AudioPlaybackBackend | None,
    ) -> None:
        """Resolve TTS provider and playback backend cleanly without cloud assumptions."""
        if tts_provider is not None:
            self._tts_provider = tts_provider
        elif (
            self._controller is not None
            and hasattr(self._controller, "voice_controller")
            and self._controller.voice_controller is not None
        ):
            self._tts_provider = self._controller.voice_controller.tts_provider
            if self._consent_manager is None:
                self._consent_manager = self._controller.voice_controller.consent_manager
        else:
            try:
                cfg = AppConfig.load()
                self._tts_provider = create_tts_provider(
                    cfg,
                    consent_manager=self._consent_manager,
                )
            except Exception as exc:
                logger.warning("Could not initialize default TTS provider for preview: %s", exc)
                self._tts_provider = DisabledTTSProvider()

        if (
            self._consent_manager is not None
            and getattr(self._tts_provider, "is_remote", False)
            and getattr(self._tts_provider, "_consent_manager", None) is None
        ):
            self._tts_provider._consent_manager = self._consent_manager

        if playback_backend is not None:
            self._playback = playback_backend
        elif (
            self._controller is not None
            and hasattr(self._controller, "voice_controller")
            and self._controller.voice_controller is not None
        ):
            self._playback = self._controller.voice_controller.playback_backend
        else:
            try:
                self._playback = create_playback_backend()
            except Exception as exc:
                logger.warning("Could not initialize audio playback backend for preview: %s", exc)
                self._playback = MockAudioPlaybackBackend(auto_finish=True)

    @property
    def is_tts_disabled(self) -> bool:
        """Return True if TTS is configured as disabled or unavailable."""
        return isinstance(self._tts_provider, DisabledTTSProvider) or (
            hasattr(self._tts_provider, "provider_name")
            and self._tts_provider.provider_name == "disabled"
        )

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(14, 14, 14, 14)

        # ── 1. Header Banner ──────────────────────────────
        hdr_frame = QFrame(self)
        hdr_frame.setStyleSheet(
            "background-color: #f8fafc; border: 1px solid #e2e8f0; "
            "border-radius: 6px; padding: 6px;"
        )
        hdr_layout = QVBoxLayout(hdr_frame)
        hdr_layout.setSpacing(4)
        hdr_layout.setContentsMargins(8, 8, 8, 8)

        lbl_task_info = QLabel(
            f"<b>Tutorial:</b> {self._task.title} <code>({self._task.task_id})</code>",
            hdr_frame,
        )
        lbl_task_info.setStyleSheet("color: #1e293b; font-size: 13px;")

        lbl_step_info = QLabel(
            f"<b>Step {self._step.order}:</b> {self._step.title} "
            f"<code>({self._step.step_id})</code>",
            hdr_frame,
        )
        lbl_step_info.setStyleSheet("color: #334155; font-size: 12px;")

        hdr_layout.addWidget(lbl_task_info)
        hdr_layout.addWidget(lbl_step_info)
        main_layout.addWidget(hdr_frame)

        # ── 2. Target Text Selection & Editor ──────────────
        grp_text = QGroupBox("Text to Preview & Synthesize", self)
        grp_text_layout = QVBoxLayout(grp_text)
        grp_text_layout.setSpacing(8)

        row_source = QHBoxLayout()
        row_source.addWidget(QLabel("Narration Source:", grp_text))

        self._cmb_source = QComboBox(grp_text)
        self._cmb_source.setAccessibleName("Select Narration Text Source")
        self._cmb_source.currentIndexChanged.connect(self._on_source_changed)
        row_source.addWidget(self._cmb_source, stretch=1)
        grp_text_layout.addLayout(row_source)

        self._txt_preview_text = QPlainTextEdit(grp_text)
        self._txt_preview_text.setPlaceholderText(
            "Enter instruction or hint text to simulate timing and preview speech..."
        )
        self._txt_preview_text.setAccessibleName("Narration Text for Speech Preview")
        self._txt_preview_text.textChanged.connect(self._on_text_edited)
        grp_text_layout.addWidget(self._txt_preview_text)
        main_layout.addWidget(grp_text)

        # ── 3. Speech Timing & Cue Duration Simulator ──────
        grp_timing = QGroupBox("Speech Cadence & Visual Cue Timing Simulator", self)
        grp_timing_layout = QVBoxLayout(grp_timing)
        grp_timing_layout.setSpacing(8)

        # Metric Badges Row
        metrics_row = QHBoxLayout()
        metrics_row.setSpacing(12)

        self._lbl_counts = QLabel("Words: 0 | Chars: 0", grp_timing)
        self._lbl_counts.setStyleSheet(
            "background: #f1f5f9; color: #334155; font-weight: bold; padding: 6px 12px; "
            "border-radius: 4px; border: 1px solid #cbd5e1;"
        )
        self._lbl_counts.setAccessibleName("Word and Character Count")

        self._lbl_speech_duration = QLabel("Est. Speech: 0.0s", grp_timing)
        self._lbl_speech_duration.setStyleSheet(
            "background: #e0f2fe; color: #0369a1; font-weight: bold; font-size: 13px; "
            "padding: 6px 12px; border-radius: 4px; border: 1px solid #bae6fd;"
        )
        self._lbl_speech_duration.setAccessibleName("Estimated Speech Duration")

        self._lbl_cue_duration = QLabel("Recommended Cue: 3.5s", grp_timing)
        self._lbl_cue_duration.setStyleSheet(
            "background: #fef3c7; color: #b45309; font-weight: bold; font-size: 13px; "
            "padding: 6px 12px; border-radius: 4px; border: 1px solid #fde68a;"
        )
        self._lbl_cue_duration.setAccessibleName("Recommended Visual Cue Duration")

        metrics_row.addWidget(self._lbl_counts)
        metrics_row.addWidget(self._lbl_speech_duration)
        metrics_row.addWidget(self._lbl_cue_duration)
        metrics_row.addStretch(1)
        grp_timing_layout.addLayout(metrics_row)

        # Fine-Tuning Controls Row
        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(14)

        ctrl_row.addWidget(QLabel("Cadence Rate (WPM):", grp_timing))
        self._spin_wpm = QSpinBox(grp_timing)
        self._spin_wpm.setRange(60, 260)
        self._spin_wpm.setValue(DEFAULT_WORDS_PER_MINUTE)
        self._spin_wpm.setSuffix(" wpm")
        self._spin_wpm.setAccessibleName("Narration Words Per Minute Rate")
        self._spin_wpm.valueChanged.connect(self._recalculate_timing)
        ctrl_row.addWidget(self._spin_wpm)

        ctrl_row.addWidget(QLabel("Visual Cue Padding:", grp_timing))
        self._spin_padding = QDoubleSpinBox(grp_timing)
        self._spin_padding.setRange(0.0, 6.0)
        self._spin_padding.setSingleStep(0.5)
        self._spin_padding.setValue(DEFAULT_CUE_PADDING_SECONDS)
        self._spin_padding.setSuffix(" s")
        self._spin_padding.setAccessibleName("Visual Cue Padding Seconds")
        self._spin_padding.valueChanged.connect(self._recalculate_timing)
        ctrl_row.addWidget(self._spin_padding)

        self._btn_apply_cue_duration = QPushButton("✔ Apply Recommended Cue Duration", grp_timing)
        self._btn_apply_cue_duration.setToolTip(
            "Confirm and apply the calculated cue duration to the active step's visual cues."
        )
        self._btn_apply_cue_duration.setAccessibleName("Apply Recommended Cue Duration")
        self._btn_apply_cue_duration.clicked.connect(self._on_apply_cue_duration_clicked)
        ctrl_row.addWidget(self._btn_apply_cue_duration)
        ctrl_row.addStretch(1)
        grp_timing_layout.addLayout(ctrl_row)

        lbl_timing_hint = QLabel(
            "ℹ Modeled from linguistic cadence without requiring remote network requests.",
            grp_timing,
        )
        lbl_timing_hint.setStyleSheet("color: #64748b; font-size: 11px;")
        grp_timing_layout.addWidget(lbl_timing_hint)
        main_layout.addWidget(grp_timing)

        # ── 4. TTS Provider & Audio Playback Bench ─────────
        grp_audio = QGroupBox("Audio Playback & Provider Bench", self)
        grp_audio_layout = QVBoxLayout(grp_audio)
        grp_audio_layout.setSpacing(8)

        # Provider Status Row
        prov_row = QHBoxLayout()
        prov_row.setSpacing(10)

        self._lbl_provider_badge = QLabel("TTS: DETECTING...", grp_audio)
        self._lbl_provider_badge.setStyleSheet(
            "background: #64748b; color: white; font-weight: bold; "
            "padding: 4px 10px; border-radius: 4px;"
        )
        self._lbl_provider_badge.setAccessibleName("Text to Speech Provider Status Badge")
        prov_row.addWidget(self._lbl_provider_badge)

        self._lbl_provider_desc = QLabel("", grp_audio)
        self._lbl_provider_desc.setStyleSheet("color: #475569; font-size: 11px;")
        prov_row.addWidget(self._lbl_provider_desc, stretch=1)
        grp_audio_layout.addLayout(prov_row)

        # Provider Disabled Notice (when applicable)
        self._frame_disabled_notice = QFrame(grp_audio)
        self._frame_disabled_notice.setStyleSheet(
            "background-color: #fefce8; border: 1px solid #fef08a; "
            "border-radius: 4px; padding: 6px;"
        )
        dis_layout = QVBoxLayout(self._frame_disabled_notice)
        dis_layout.setContentsMargins(6, 4, 6, 4)
        lbl_dis_msg = QLabel(
            "⚠️ <b>TTS Provider is Disabled:</b> Text-to-speech engine is set to 'disabled'. "
            "OCTA provides accurate text-based timing estimates above. In accordance with "
            "project privacy and safety rules, OCTA will <b>not</b> substitute a mock or chime "
            "as spoken TTS.",
            self._frame_disabled_notice,
        )
        lbl_dis_msg.setWordWrap(True)
        lbl_dis_msg.setStyleSheet("color: #854d0e; font-size: 11px;")
        dis_layout.addWidget(lbl_dis_msg)
        grp_audio_layout.addWidget(self._frame_disabled_notice)

        # Audio Preference Row
        pref_row = QHBoxLayout()
        self._chk_enable_audio = QCheckBox("🔊 Enable Audio Playback", grp_audio)
        self._chk_enable_audio.setChecked(True)
        self._chk_enable_audio.setToolTip(
            "Toggle audio playback for preview. If unchecked, timing simulations run without sound."
        )
        self._chk_enable_audio.setAccessibleName("Enable Audio Playback Toggle")
        self._chk_enable_audio.toggled.connect(self._on_audio_pref_toggled)
        pref_row.addWidget(self._chk_enable_audio)
        pref_row.addStretch(1)
        grp_audio_layout.addLayout(pref_row)

        # Action Buttons Row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self._btn_play = QPushButton("🔊 Play Speech Preview", grp_audio)
        self._btn_play.setStyleSheet(
            "background-color: #0284c7; color: white; font-weight: bold; "
            "padding: 6px 14px; border-radius: 4px;"
        )
        self._btn_play.setAccessibleName("Play Speech Preview")
        self._btn_play.clicked.connect(self._on_play_clicked)
        btn_row.addWidget(self._btn_play)

        self._btn_stop = QPushButton("⏹ Stop", grp_audio)
        self._btn_stop.setEnabled(False)
        self._btn_stop.setAccessibleName("Stop Audio Playback")
        self._btn_stop.clicked.connect(self._stop_playback)
        btn_row.addWidget(self._btn_stop)

        self._btn_sync_cues = QPushButton("👁 Synchronized Cue Preview", grp_audio)
        self._btn_sync_cues.setToolTip(
            "Simulates learner cues: displays overlay highlights/arrows timed to spoken audio."
        )
        self._btn_sync_cues.setAccessibleName("Preview Synchronized Cues and Audio")
        self._btn_sync_cues.clicked.connect(self._on_sync_cues_clicked)
        btn_row.addWidget(self._btn_sync_cues)
        btn_row.addStretch(1)
        grp_audio_layout.addLayout(btn_row)

        main_layout.addWidget(grp_audio)

        # ── 5. Footer & Dismissal ─────────────────────────
        bot_bar = QHBoxLayout()
        self._lbl_footer_status = QLabel("Ready.", self)
        self._lbl_footer_status.setStyleSheet("color: #64748b; font-size: 11px;")
        bot_bar.addWidget(self._lbl_footer_status, stretch=1)

        self._btn_close = QPushButton("Close", self)
        self._btn_close.setAccessibleName("Close Speech Preview Dialog")
        self._btn_close.clicked.connect(self.accept)
        bot_bar.addWidget(self._btn_close)

        main_layout.addLayout(bot_bar)

        self._update_provider_ui()

    def _populate_initial_data(self) -> None:
        """Populate narration source combo box with active step options."""
        self._cmb_source.clear()

        # 1. Step Instruction
        inst_words = len(self._step.instruction.split())
        self._cmb_source.addItem(f"Step Instruction ({inst_words} words)", self._step.instruction)

        # 2. Hints
        for i, hint in enumerate(self._step.hints, 1):
            h_words = len(hint.split())
            self._cmb_source.addItem(f"Hint {i} ({h_words} words)", hint)

        # 3. Full narration
        full_text = f"Step {self._step.order}: {self._step.title}. {self._step.instruction}"
        full_words = len(full_text.split())
        self._cmb_source.addItem(
            f"Full Narration (Title + Instruction) ({full_words} words)", full_text
        )

        # 4. Custom Draft
        self._cmb_source.addItem("Custom Draft...", "")

        self._cmb_source.setCurrentIndex(0)
        self._txt_preview_text.setPlainText(self._step.instruction)

    def _update_provider_ui(self) -> None:
        """Update provider badges and notice visibility based on configured TTS provider."""
        if self.is_tts_disabled:
            self._lbl_provider_badge.setText("TTS: DISABLED")
            self._lbl_provider_badge.setStyleSheet(
                "background: #78716c; color: white; font-weight: bold; font-size: 11px; "
                "padding: 4px 10px; border-radius: 4px;"
            )
            self._lbl_provider_desc.setText(
                "TTS provider is disabled. Accurate timing estimates available; audio is off."
            )
            self._frame_disabled_notice.show()
            self._btn_play.setEnabled(False)
            self._btn_play.setToolTip(
                "Speech playback is disabled. Enable a TTS provider to listen to spoken narration."
            )
        elif isinstance(self._tts_provider, MockTTSProvider) or (
            hasattr(self._tts_provider, "provider_name")
            and "mock" in self._tts_provider.provider_name
        ):
            self._lbl_provider_badge.setText("TTS: MOCK (In-Memory)")
            self._lbl_provider_badge.setStyleSheet(
                "background: #0284c7; color: white; font-weight: bold; font-size: 11px; "
                "padding: 4px 10px; border-radius: 4px;"
            )
            self._lbl_provider_desc.setText(
                "Deterministic local in-memory synthesis for testing and offline authoring."
            )
            self._frame_disabled_notice.hide()
            self._btn_play.setEnabled(True)
            self._btn_play.setToolTip("Play in-memory mock speech synthesis.")
        elif getattr(self._tts_provider, "is_remote", False):
            raw_endpoint = getattr(self._tts_provider.config, "endpoint", "https://...")
            sanitized = sanitize_endpoint_for_display(raw_endpoint)
            self._lbl_provider_badge.setText("TTS: REMOTE HTTPS")
            self._lbl_provider_badge.setStyleSheet(
                "background: #7c3aed; color: white; font-weight: bold; font-size: 11px; "
                "padding: 4px 10px; border-radius: 4px;"
            )
            self._lbl_provider_desc.setText(
                f"Endpoint: {sanitized}. Requires explicit single-use consent before sending text."
            )
            self._frame_disabled_notice.hide()
            self._btn_play.setEnabled(True)
            self._btn_play.setToolTip(
                "Request single-use consent and synthesize via remote TTS endpoint."
            )
        else:
            p_name = getattr(self._tts_provider, "provider_name", "custom")
            self._lbl_provider_badge.setText(f"TTS: {p_name.upper()}")
            self._lbl_provider_badge.setStyleSheet(
                "background: #0d9488; color: white; font-weight: bold; font-size: 11px; "
                "padding: 4px 10px; border-radius: 4px;"
            )
            self._lbl_provider_desc.setText("Active local text-to-speech provider.")
            self._frame_disabled_notice.hide()
            self._btn_play.setEnabled(True)

    @Slot(int)
    def _on_source_changed(self, index: int) -> None:
        """Handle selection of different step text sources."""
        if index < 0 or index >= self._cmb_source.count():
            return
        data = self._cmb_source.itemData(index)
        if data:
            self._txt_preview_text.blockSignals(True)
            self._txt_preview_text.setPlainText(str(data))
            self._txt_preview_text.blockSignals(False)
            self._recalculate_timing()

    @Slot()
    def _on_text_edited(self) -> None:
        """Handle manual text edits in preview editor."""
        self._recalculate_timing()

    @Slot()
    def _recalculate_timing(self) -> None:
        """Recalculate speech timing and recommended cue duration."""
        text = self._txt_preview_text.toPlainText()
        wpm = self._spin_wpm.value()
        padding = self._spin_padding.value()

        estimate = estimate_speech_timing(
            text,
            words_per_minute=wpm,
            cue_padding_seconds=padding,
        )

        self._lbl_counts.setText(
            f"Words: {estimate.word_count} | Chars: {estimate.character_count}"
        )
        self._lbl_speech_duration.setText(
            f"Est. Speech: ~{estimate.estimated_duration_seconds:.1f}s"
        )
        self._lbl_cue_duration.setText(
            f"Recommended Cue: ~{estimate.recommended_cue_duration_seconds:.1f}s"
        )

    @Slot(bool)
    def _on_audio_pref_toggled(self, enabled: bool) -> None:
        """Handle audio playback preference checkbox toggle."""
        if not enabled:
            self._stop_playback()
            self._lbl_footer_status.setText("Audio playback muted by author preference.")
        else:
            self._lbl_footer_status.setText("Audio playback enabled.")

    @Slot()
    def _on_apply_cue_duration_clicked(self) -> None:
        """Record the calculated cue duration for application to the step."""
        text = self._txt_preview_text.toPlainText()
        estimate = estimate_speech_timing(
            text,
            words_per_minute=self._spin_wpm.value(),
            cue_padding_seconds=self._spin_padding.value(),
        )
        self.applied_cue_duration = estimate.recommended_cue_duration_seconds
        rec_s = estimate.recommended_cue_duration_seconds
        pad_s = self._spin_padding.value()
        QMessageBox.information(
            self,
            "Cue Duration Applied",
            f"Recommended visual cue duration set to {rec_s:.1f}s "
            f"(Speech: ~{estimate.estimated_duration_seconds:.1f}s + {pad_s:.1f}s padding).\n\n"
            "This value will be applied to the active step's guidance preview.",
        )
        self._lbl_footer_status.setText(f"Applied cue duration: {rec_s:.1f}s.")

    @Slot()
    def _on_play_clicked(self) -> None:
        """Synthesize and play speech audio for current preview text."""
        text = self._txt_preview_text.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "Empty Narration", "Please enter narration text to preview.")
            return

        # 1. Respect disabled provider setting
        if self.is_tts_disabled:
            QMessageBox.information(
                self,
                "TTS Disabled",
                "Text-to-speech provider is disabled (OCTA_VOICE_TTS_PROVIDER=disabled).\n\n"
                "In accordance with project privacy rules, OCTA will not pretend to speak "
                "or play a chime. Please consult the linguistic timing estimate above.",
            )
            return

        # 2. Respect audio playback preference
        if not self._chk_enable_audio.isChecked():
            QMessageBox.information(
                self,
                "Audio Muted",
                "Audio playback is currently muted in the checkbox above.\n"
                "Uncheck 'Enable Audio Playback' to hear synthesized speech.",
            )
            return

        if self._is_playing:
            self._stop_playback()
        self._lbl_footer_status.setText("Synthesizing speech audio...")

        try:
            # 3. Handle speech synthesis (remote TTS provider enforces single-use consent)
            speech = self._tts_provider.synthesize(
                text,
                task_title=self._task.title,
                step_title=self._step.title,
            )

            self._active_speech = speech

            # 4. Play through audio backend
            self._is_playing = True
            self._btn_play.setEnabled(False)
            self._btn_stop.setEnabled(True)
            self._lbl_footer_status.setText(
                f"Playing synthesized speech ({speech.duration_ms} ms, {speech.provider_name})..."
            )

            if hasattr(self._playback, "set_on_finished"):
                self._playback.set_on_finished(self._on_playback_finished)
            self._playback.play(speech)

            if not getattr(self._playback, "is_playing", False):
                # Backend auto-finished or finished synchronously
                self._on_playback_finished()

        except VoiceCancelledError:
            self._stop_playback()
            self._lbl_footer_status.setText("Speech synthesis was cancelled.")
        except VoiceConsentDeclinedError:
            self._stop_playback()
            self._lbl_footer_status.setText("Speech preview cancelled: consent declined by author.")
        except Exception as exc:
            self._stop_playback()
            logger.warning("Failed to synthesize or play preview speech: %s", exc)
            QMessageBox.critical(
                self, "Speech Preview Error", f"Failed to synthesize speech:\n\n{exc}"
            )
            self._lbl_footer_status.setText("Speech preview failed.")

    @Slot()
    def _on_sync_cues_clicked(self) -> None:
        """Simulate synchronized visual overlay cue preview timed to speech or estimate."""
        cues = list(self._step.overlay_cues)
        if not cues:
            QMessageBox.information(
                self,
                "No Cues Defined",
                f"Step {self._step.order} has no visual overlay cues defined.\n"
                "Add a highlight or directional arrow in the visual cues section first.",
            )
            return

        self._stop_playback()

        # Target display bounds
        bounds = self._display_bounds or OverlayBounds(x=0, y=0, width=1920, height=1080)
        hc = self._controller.accessibility_high_contrast if self._controller else False
        ts = self._controller.accessibility_text_scale if self._controller else 1.0

        overlay = GuidanceOverlayWidget(
            screen_bounds=bounds,
            high_contrast=hc,
            text_scale=ts,
        )
        overlay.apply_hints(cues)
        overlay.show()
        self._active_overlay = overlay

        text = self._txt_preview_text.toPlainText().strip()
        estimate = estimate_speech_timing(
            text,
            words_per_minute=self._spin_wpm.value(),
            cue_padding_seconds=self._spin_padding.value(),
        )

        display_ms = estimate.recommended_cue_duration_ms

        # If audio enabled and TTS is active, play speech alongside cues
        if not self.is_tts_disabled and self._chk_enable_audio.isChecked() and text:
            self._on_play_clicked()

        # Set auto-dismiss timer for the overlay
        self._overlay_timer = QTimer(self)
        self._overlay_timer.setSingleShot(True)
        self._overlay_timer.timeout.connect(self._on_overlay_timed_out)
        self._overlay_timer.start(display_ms)

        self._btn_stop.setEnabled(True)
        self._lbl_footer_status.setText(
            f"Simulating synchronized visual cues ({display_ms / 1000.0:.1f}s)..."
        )

    def _on_overlay_timed_out(self) -> None:
        """Handle overlay auto-dismiss timer expiration."""
        if self._active_overlay is not None:
            self._active_overlay.close()
            self._active_overlay = None
        self._overlay_timer = None
        if not self._is_playing:
            self._btn_stop.setEnabled(False)
            self._lbl_footer_status.setText("Synchronized cue preview finished.")

    def _on_playback_finished(self) -> None:
        """Handle natural playback completion callback."""
        self._is_playing = False
        self._active_speech = None  # Release audio buffer
        self._btn_play.setEnabled(not self.is_tts_disabled)
        if self._active_overlay is None:
            self._btn_stop.setEnabled(False)
        self._lbl_footer_status.setText("Speech playback finished.")

    @Slot()
    def _stop_playback(self) -> None:
        """Immediately stop playback, cancel provider, close overlay, and release buffers."""
        if self._is_playing and hasattr(self._tts_provider, "cancel"):
            try:
                self._tts_provider.cancel()
            except Exception:
                pass

        if hasattr(self._playback, "stop"):
            try:
                self._playback.stop()
            except Exception:
                pass

        if self._overlay_timer is not None:
            self._overlay_timer.stop()
            self._overlay_timer = None

        if self._active_overlay is not None:
            self._active_overlay.close()
            self._active_overlay = None

        self._active_speech = None  # Free in-memory audio buffers
        self._is_playing = False
        self._btn_play.setEnabled(not self.is_tts_disabled)
        self._btn_stop.setEnabled(False)
        self._lbl_footer_status.setText("Playback and preview stopped.")

    def reject(self) -> None:
        """Clean up all playback and overlays on dialog rejection/cancel."""
        self._stop_playback()
        super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Ensure closing the dialog stops audio playback and cleans up overlays."""
        self._stop_playback()
        super().closeEvent(event)
