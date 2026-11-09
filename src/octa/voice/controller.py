"""Push-to-talk voice controller connecting spoken requests to the task and instruction pipeline.

Manages push-to-talk microphone capture, STT transcription, spoken intent routing
against TaskDefinition / TaskStep / ProgressVerifier, and TTS instruction readout.
Ensures microphone activity is always visible, supports immediate cancel/stop,
forbids continuous recording or wake-word activation, and keeps credentials,
audio bytes, and transcripts out of logs and persistent files.
"""

from __future__ import annotations

import logging
import re
from enum import StrEnum

from PySide6.QtCore import QObject, Signal, Slot

from octa.core.config import AppConfig
from octa.intelligence.tasks import (
    ExpectedOutcome,
    GuidanceInstruction,
    TaskDefinition,
    TaskStep,
)
from octa.intelligence.verifier import ProgressVerifier
from octa.voice.audio import (
    MockAudioCaptureBackend,
    PushToTalkRecorder,
    QtAudioCaptureBackend,
    RecordedAudio,
    SynthesizedSpeech,
    TranscriptionResult,
)
from octa.voice.consent import VoiceConsentDecision, VoiceConsentManager
from octa.voice.exceptions import (
    ContinuousRecordingForbiddenError,
    PushToTalkRequiredError,
    VoiceCancelledError,
    VoiceConfigurationError,
    VoiceError,
)
from octa.voice.providers import (
    AudioPlaybackBackend,
    DisabledSTTProvider,
    DisabledTTSProvider,
    SpeechToTextProvider,
    TextToSpeechProvider,
    create_playback_backend,
    create_stt_provider,
    create_tts_provider,
)

logger = logging.getLogger("octa.voice.controller")

_NEXT_STEP_PATTERN = re.compile(
    r"\b(next\s+step|advance|go\s+forward|move\s+on|continue\s+to\s+next)\b",
    re.IGNORECASE,
)
_PREV_STEP_PATTERN = re.compile(
    r"\b(previous\s+step|prev\s+step|go\s+back|prior\s+step|last\s+step)\b",
    re.IGNORECASE,
)
_HINT_PATTERN = re.compile(
    r"\b(hint|help|tip|stuck|clue)\b",
    re.IGNORECASE,
)
_GOAL_PATTERN = re.compile(
    r"\b(overall\s+goal|what\s+task|which\s+tutorial|task\s+overview)\b",
    re.IGNORECASE,
)


class VoiceState(StrEnum):
    """Observable lifecycle state of the push-to-talk voice subsystem."""

    IDLE = "IDLE"
    RECORDING = "RECORDING"
    TRANSCRIBING = "TRANSCRIBING"
    PROCESSING = "PROCESSING"
    SPEAKING = "SPEAKING"
    ERROR = "ERROR"

    def __str__(self) -> str:
        return self.value


def build_default_companion_task() -> TaskDefinition:
    """Build a default multi-step companion tutorial task for out-of-the-box guidance."""
    return TaskDefinition(
        task_id="octa-welcome-task",
        title="Getting Started with OCTA",
        application="OCTA Companion",
        goal="Learn how to navigate tutorial steps and request guidance with push-to-talk.",
        steps=(
            TaskStep(
                step_id="step-1",
                order=1,
                title="Review Current Step",
                instruction="Open the target application window and review the current step goal.",
                expected_outcome=ExpectedOutcome(
                    description="The target application window is open and visible.",
                    required_elements=("Application window",),
                ),
                hints=("Use Push to Talk to ask for the current step, a hint, or the next step.",),
            ),
            TaskStep(
                step_id="step-2",
                order=2,
                title="Complete the Guided Action",
                instruction="Follow the highlighted UI element and verify your progress.",
                expected_outcome=ExpectedOutcome(
                    description="The guided action has been completed in the application.",
                    required_elements=("Completed state",),
                ),
                hints=("You can click Read Instruction Aloud at any time to hear the step again.",),
            ),
        ),
    )


def route_spoken_request_to_instruction(
    spoken_text: str,
    *,
    task: TaskDefinition,
    step_index: int = 0,
    verifier: ProgressVerifier | None = None,
    use_llm: bool = False,
) -> tuple[GuidanceInstruction, int]:
    """Route a spoken request to the task and instruction pipeline without logging transcript text.

    Returns:
        tuple of (GuidanceInstruction, updated_step_index)
    """
    if not isinstance(task, TaskDefinition):
        raise ValueError("Expected a valid TaskDefinition instance.")
    if not isinstance(spoken_text, str) or not spoken_text.strip():
        raise ValueError("Spoken request text must be non-empty.")

    cleaned = spoken_text.strip()
    total_steps = len(task.steps)
    current_idx = max(0, min(int(step_index), total_steps - 1))

    # Determine intent from spoken request in memory
    if _NEXT_STEP_PATTERN.search(cleaned):
        current_idx = min(total_steps - 1, current_idx + 1)
        step = task.steps[current_idx]
        if use_llm and verifier is not None:
            return verifier.generate_step_instruction(task, step), current_idx
        return (
            GuidanceInstruction(
                task_id=task.task_id,
                step_id=step.step_id,
                instruction_text=f"Step {step.order}: {step.title}. {step.instruction}",
                summary=step.title,
                overlay_cues=step.overlay_cues,
            ),
            current_idx,
        )

    if _PREV_STEP_PATTERN.search(cleaned):
        current_idx = max(0, current_idx - 1)
        step = task.steps[current_idx]
        if use_llm and verifier is not None:
            return verifier.generate_step_instruction(task, step), current_idx
        return (
            GuidanceInstruction(
                task_id=task.task_id,
                step_id=step.step_id,
                instruction_text=f"Step {step.order}: {step.title}. {step.instruction}",
                summary=step.title,
                overlay_cues=step.overlay_cues,
            ),
            current_idx,
        )

    step = task.steps[current_idx]

    if _HINT_PATTERN.search(cleaned):
        if step.hints:
            hint_text = " ".join(step.hints)
            text = f"Hint for Step {step.order} ({step.title}): {hint_text}"
        else:
            text = f"Step {step.order} ({step.title}): {step.instruction}"
        return (
            GuidanceInstruction(
                task_id=task.task_id,
                step_id=step.step_id,
                instruction_text=text,
                summary=f"Hint: {step.title}",
                overlay_cues=step.overlay_cues,
            ),
            current_idx,
        )

    if _GOAL_PATTERN.search(cleaned):
        text = (
            f"Task: {task.title} in {task.application}. Goal: {task.goal} "
            f"Current Step {step.order} of {total_steps}: {step.instruction}"
        )
        return (
            GuidanceInstruction(
                task_id=task.task_id,
                step_id=step.step_id,
                instruction_text=text,
                summary=task.title,
                overlay_cues=step.overlay_cues,
            ),
            current_idx,
        )

    # Default: generate or return step instruction for current step
    if use_llm and verifier is not None:
        return verifier.generate_step_instruction(task, step), current_idx

    return (
        GuidanceInstruction(
            task_id=task.task_id,
            step_id=step.step_id,
            instruction_text=f"Step {step.order}: {step.title}. {step.instruction}",
            summary=step.title,
            overlay_cues=step.overlay_cues,
        ),
        current_idx,
    )


class VoiceInteractionController(QObject):
    """Push-to-talk voice flow controller connecting STT, task pipeline, and TTS readout."""

    voice_state_changed = Signal(str)  # VoiceState value
    microphone_active_changed = Signal(bool)  # True only during active push-to-talk recording
    speaking_active_changed = Signal(bool)  # True during active TTS playback
    instruction_ready = Signal(str, str)  # step_id, instruction_text
    voice_activity_event = Signal(str, str)  # privacy-safe message, level
    voice_error = Signal(str)  # privacy-safe error message

    def __init__(
        self,
        *,
        recorder: PushToTalkRecorder | None = None,
        stt_provider: SpeechToTextProvider | None = None,
        tts_provider: TextToSpeechProvider | None = None,
        playback_backend: AudioPlaybackBackend | None = None,
        task: TaskDefinition | None = None,
        verifier: ProgressVerifier | None = None,
        use_llm_instructions: bool = False,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._recorder = recorder if recorder is not None else PushToTalkRecorder()
        self._stt_provider: SpeechToTextProvider = (
            stt_provider if stt_provider is not None else create_stt_provider()
        )
        self._tts_provider: TextToSpeechProvider = (
            tts_provider if tts_provider is not None else create_tts_provider()
        )
        self._playback: AudioPlaybackBackend = (
            playback_backend if playback_backend is not None else create_playback_backend()
        )
        if hasattr(self._playback, "set_on_finished"):
            self._playback.set_on_finished(self._on_playback_finished)
        self._task = task if task is not None else build_default_companion_task()
        self._step_index = 0
        self._verifier = verifier
        self._use_llm_instructions = bool(use_llm_instructions)
        self._state = VoiceState.IDLE
        self._cancelled = False
        self._last_error: str = ""

        initial_step = self._task.steps[self._step_index]
        self._last_instruction = GuidanceInstruction(
            task_id=self._task.task_id,
            step_id=initial_step.step_id,
            instruction_text=(
                f"Step {initial_step.order}: {initial_step.title}. {initial_step.instruction}"
            ),
            summary=initial_step.title,
            overlay_cues=initial_step.overlay_cues,
        )

    @classmethod
    def from_app_config(
        cls,
        config: AppConfig | None = None,
        *,
        consent_manager: VoiceConsentManager | None = None,
        recorder: PushToTalkRecorder | None = None,
        stt_provider: SpeechToTextProvider | None = None,
        tts_provider: TextToSpeechProvider | None = None,
        playback_backend: AudioPlaybackBackend | None = None,
        task: TaskDefinition | None = None,
        verifier: ProgressVerifier | None = None,
        parent: QObject | None = None,
    ) -> VoiceInteractionController:
        """Create a VoiceInteractionController from an AppConfig instance."""
        resolved = config if config is not None else AppConfig.load()
        actual_consent_manager = (
            consent_manager if consent_manager is not None else VoiceConsentManager()
        )
        actual_capture_backend = (
            MockAudioCaptureBackend()
            if resolved.voice_stt_provider == "mock"
            else QtAudioCaptureBackend()
        )
        actual_recorder = (
            recorder
            if recorder is not None
            else PushToTalkRecorder(
                backend=actual_capture_backend,
                save_audio=resolved.voice_save_audio,
                max_recording_seconds=resolved.voice_max_recording_seconds,
            )
        )
        actual_stt = (
            stt_provider
            if stt_provider is not None
            else create_stt_provider(resolved, consent_manager=actual_consent_manager)
        )
        actual_tts = (
            tts_provider
            if tts_provider is not None
            else create_tts_provider(resolved, consent_manager=actual_consent_manager)
        )
        actual_playback = (
            playback_backend if playback_backend is not None else create_playback_backend(resolved)
        )
        return cls(
            recorder=actual_recorder,
            stt_provider=actual_stt,
            tts_provider=actual_tts,
            playback_backend=actual_playback,
            task=task,
            verifier=verifier,
            parent=parent,
        )

    # ──────────────────────────────────────────
    # Properties
    # ──────────────────────────────────────────

    @property
    def state(self) -> VoiceState:
        """Current voice pipeline state."""
        return self._state

    @property
    def is_microphone_active(self) -> bool:
        """Return True only when the push-to-talk microphone is actively recording."""
        return self._recorder.is_recording

    @property
    def is_speaking(self) -> bool:
        """Return True when TTS instruction audio is actively playing."""
        return self._playback.is_playing or self._state == VoiceState.SPEAKING

    @property
    def active_task(self) -> TaskDefinition:
        """Currently loaded tutorial TaskDefinition."""
        return self._task

    @property
    def current_step(self) -> TaskStep:
        """Current active TaskStep."""
        return self._task.steps[self._step_index]

    @property
    def current_step_index(self) -> int:
        """Zero-based index of the current active TaskStep."""
        return self._step_index

    @property
    def last_instruction(self) -> GuidanceInstruction:
        """Most recent GuidanceInstruction produced by the task pipeline."""
        return self._last_instruction

    @property
    def stt_provider(self) -> SpeechToTextProvider:
        """Configured Speech-to-Text provider."""
        return self._stt_provider

    @property
    def tts_provider(self) -> TextToSpeechProvider:
        """Configured Text-to-Speech provider."""
        return self._tts_provider

    @property
    def recorder(self) -> PushToTalkRecorder:
        """Push-to-talk microphone recorder."""
        return self._recorder

    @property
    def playback_backend(self) -> AudioPlaybackBackend:
        """Audio playback backend."""
        return self._playback

    @property
    def is_stt_enabled(self) -> bool:
        """Return True when a non-disabled Speech-to-Text provider is configured."""
        return (
            not isinstance(self._stt_provider, DisabledSTTProvider)
            and self._stt_provider.provider_name != "disabled"
        )

    @property
    def is_tts_enabled(self) -> bool:
        """Return True when a non-disabled Text-to-Speech provider is configured."""
        return (
            not isinstance(self._tts_provider, DisabledTTSProvider)
            and self._tts_provider.provider_name != "disabled"
        )

    @property
    def last_error(self) -> str:
        """Most recent privacy-safe voice error message, or empty string."""
        return self._last_error

    def get_provider_status_summary(self) -> str:
        """Return a human-readable summary of configured STT and TTS provider states."""
        stt_label = (
            "disabled (Disabled)" if not self.is_stt_enabled else self._stt_provider.provider_name
        )
        tts_label = (
            "disabled (Disabled)" if not self.is_tts_enabled else self._tts_provider.provider_name
        )
        return f"Voice Providers: STT={stt_label} | TTS={tts_label}"

    # ──────────────────────────────────────────
    # Task & Pipeline Management
    # ──────────────────────────────────────────

    def set_active_task(self, task: TaskDefinition, *, step_index: int = 0) -> GuidanceInstruction:
        """Load a TaskDefinition into the voice pipeline and set the current step."""
        if not isinstance(task, TaskDefinition):
            raise ValueError("Expected a TaskDefinition instance.")
        if step_index < 0 or step_index >= len(task.steps):
            raise ValueError(f"step_index {step_index} is out of range for task '{task.task_id}'.")
        self._task = task
        self._step_index = step_index
        step = self._task.steps[self._step_index]
        self._last_instruction = GuidanceInstruction(
            task_id=task.task_id,
            step_id=step.step_id,
            instruction_text=f"Step {step.order}: {step.title}. {step.instruction}",
            summary=step.title,
            overlay_cues=step.overlay_cues,
        )
        self.instruction_ready.emit(step.step_id, self._last_instruction.instruction_text)
        return self._last_instruction

    # ──────────────────────────────────────────
    # Policy Guards (Wake-Word & Continuous Deferred)
    # ──────────────────────────────────────────

    def enable_wake_word(self, *_args: object, **_kwargs: object) -> None:
        """Explicitly block wake-word activation (deferred by policy)."""
        raise ContinuousRecordingForbiddenError(
            "Wake-word detection is deferred and disabled by policy. "
            "Use explicit push-to-talk activation."
        )

    def enable_continuous_recording(self, *_args: object, **_kwargs: object) -> None:
        """Explicitly block continuous background microphone recording."""
        raise ContinuousRecordingForbiddenError(
            "Continuous microphone recording is forbidden by policy. "
            "Use explicit push-to-talk activation."
        )

    # ──────────────────────────────────────────
    # Push-to-Talk & Read-Aloud Flow
    # ──────────────────────────────────────────

    @Slot()
    def start_push_to_talk(self, *, user_initiated: bool = False) -> None:
        """Start push-to-talk microphone recording from an explicit user action."""
        if not isinstance(user_initiated, bool) or not user_initiated:
            raise PushToTalkRequiredError(
                "Push-to-talk recording requires an explicit user action (user_initiated=True)."
            )

        if not self.is_stt_enabled:
            msg = (
                "Speech-to-Text (STT) is disabled (OCTA_VOICE_STT_PROVIDER=disabled). "
                "Configure a provider before using Push to Talk."
            )
            self._last_error = msg
            self._set_state(VoiceState.ERROR)
            self._emit_error(msg)
            raise VoiceConfigurationError(msg)

        # If currently playing TTS audio, stop playback immediately so the user can speak
        if self._playback.is_playing or self._state == VoiceState.SPEAKING:
            self._playback.stop()
            self.speaking_active_changed.emit(False)

        self._cancelled = False
        self._last_error = ""
        try:
            self._recorder.start_recording(user_initiated=True, mode="push_to_talk")
        except Exception as exc:
            err_msg = f"Failed to start push-to-talk microphone: {exc}"
            self._last_error = err_msg
            self._set_state(VoiceState.ERROR)
            self._emit_error(err_msg)
            raise

        self.microphone_active_changed.emit(True)
        self._set_state(VoiceState.RECORDING)
        self._emit_activity("Push-to-talk microphone active (recording in memory).", "INFO")

    @Slot()
    def stop_push_to_talk_and_process(
        self,
        *,
        auto_speak_response: bool = False,
        stt_consent: VoiceConsentDecision | None = None,
        tts_consent: VoiceConsentDecision | None = None,
    ) -> GuidanceInstruction | None:
        """Stop push-to-talk recording, transcribe audio, route to task, and optionally speak."""
        if not self._recorder.is_recording:
            raise PushToTalkRequiredError("Push-to-talk recording is not currently active.")

        try:
            audio: RecordedAudio = self._recorder.stop_recording()
        finally:
            self.microphone_active_changed.emit(False)

        if self._cancelled:
            self._set_state(VoiceState.IDLE)
            return None

        self._set_state(VoiceState.TRANSCRIBING)
        self._emit_activity(
            f"Push-to-talk audio captured ({audio.duration_ms} ms); transcribing...",
            "INFO",
        )

        try:
            step = self.current_step
            transcription: TranscriptionResult = self._stt_provider.transcribe(
                audio,
                task_title=self._task.title,
                step_title=step.title,
                consent_decision=stt_consent,
            )
            if self._cancelled:
                self._set_state(VoiceState.IDLE)
                return None

            self._set_state(VoiceState.PROCESSING)
            instruction, new_idx = route_spoken_request_to_instruction(
                transcription.text,
                task=self._task,
                step_index=self._step_index,
                verifier=self._verifier,
                use_llm=self._use_llm_instructions,
            )
            self._step_index = new_idx
            self._last_instruction = instruction
            self.instruction_ready.emit(instruction.step_id, instruction.instruction_text)
            self._emit_activity(
                f"Voice request processed for task '{self._task.task_id}' "
                f"step '{instruction.step_id}'.",
                "INFO",
            )

            if auto_speak_response and not self._cancelled:
                self.speak_instruction(
                    instruction,
                    user_initiated=True,
                    consent_decision=tts_consent,
                )
            else:
                self._set_state(VoiceState.IDLE)

            return instruction
        except VoiceCancelledError:
            self._set_state(VoiceState.IDLE)
            self._emit_activity("Voice interaction cancelled.", "INFO")
            return None
        except VoiceError as exc:
            self._last_error = str(exc)
            self._set_state(VoiceState.ERROR)
            self._emit_error(str(exc))
            raise
        except Exception as exc:
            err_msg = f"Unexpected voice pipeline error: {exc}"
            self._last_error = err_msg
            self._set_state(VoiceState.ERROR)
            self._emit_error(err_msg)
            raise

    def speak_instruction(
        self,
        instruction: GuidanceInstruction | TaskStep | str,
        *,
        user_initiated: bool = True,
        consent_decision: VoiceConsentDecision | None = None,
    ) -> SynthesizedSpeech:
        """Synthesize and read a GuidanceInstruction, TaskStep, or instruction string aloud."""
        if not isinstance(user_initiated, bool) or not user_initiated:
            raise PushToTalkRequiredError(
                "Reading instructions aloud requires an explicit user action (user_initiated=True)."
            )

        if isinstance(instruction, GuidanceInstruction):
            text_to_speak = instruction.instruction_text
            step_title = instruction.summary or instruction.step_id
            step_id = instruction.step_id
        elif isinstance(instruction, TaskStep):
            text_to_speak = (
                f"Step {instruction.order}: {instruction.title}. {instruction.instruction}"
            )
            step_title = instruction.title
            step_id = instruction.step_id
        elif isinstance(instruction, str) and instruction.strip():
            text_to_speak = instruction.strip()
            step_title = self.current_step.title
            step_id = self.current_step.step_id
        else:
            raise ValueError("speak_instruction requires a non-empty instruction.")

        # Never allow microphone recording and TTS playback simultaneously
        if self._recorder.is_recording:
            self._recorder.cancel_recording()
            self.microphone_active_changed.emit(False)

        self._cancelled = False
        self._last_error = ""
        try:
            speech = self._tts_provider.synthesize(
                text_to_speak,
                task_title=self._task.title,
                step_title=step_title,
                consent_decision=consent_decision,
            )
            if self._cancelled:
                self._set_state(VoiceState.IDLE)
                raise VoiceCancelledError("Speech synthesis cancelled before playback.")

            self._set_state(VoiceState.SPEAKING)
            self.speaking_active_changed.emit(True)
            self._playback.play(speech)
            self._emit_activity(
                f"Reading instruction aloud for step '{step_id}' ({speech.duration_ms} ms).",
                "INFO",
            )
            if not self._playback.is_playing:
                self.speaking_active_changed.emit(False)
                self._set_state(VoiceState.IDLE)
            return speech
        except VoiceCancelledError:
            self.speaking_active_changed.emit(False)
            self._set_state(VoiceState.IDLE)
            raise
        except VoiceError as exc:
            self._last_error = str(exc)
            self.speaking_active_changed.emit(False)
            self._set_state(VoiceState.ERROR)
            self._emit_error(str(exc))
            raise
        except Exception as exc:
            err_msg = f"Failed to read instruction aloud: {exc}"
            self._last_error = err_msg
            self.speaking_active_changed.emit(False)
            self._set_state(VoiceState.ERROR)
            self._emit_error(err_msg)
            raise

    def _on_playback_finished(self) -> None:
        """Transition state from SPEAKING to IDLE when audio playback completes naturally."""
        if self._state == VoiceState.SPEAKING and not self._playback.is_playing:
            self.speaking_active_changed.emit(False)
            self._set_state(VoiceState.IDLE)

    @Slot()
    def speak_current_instruction(
        self,
        *,
        user_initiated: bool = True,
        consent_decision: VoiceConsentDecision | None = None,
    ) -> SynthesizedSpeech:
        """Read the current step's GuidanceInstruction aloud."""
        return self.speak_instruction(
            self._last_instruction,
            user_initiated=user_initiated,
            consent_decision=consent_decision,
        )

    @Slot()
    def cancel_or_stop(self) -> None:
        """Immediately cancel/stop microphone recording, transcription, or TTS playback."""
        self._cancelled = True
        self._last_error = ""
        was_recording = self._recorder.is_recording
        was_transcribing = self._state == VoiceState.TRANSCRIBING
        was_speaking = self._playback.is_playing or self._state == VoiceState.SPEAKING
        was_busy = self._state != VoiceState.IDLE

        if was_recording:
            self._recorder.cancel_recording()
            self.microphone_active_changed.emit(False)

        if was_transcribing:
            self._stt_provider.cancel()

        if was_speaking or self._playback.is_playing:
            self._playback.stop()
            self.speaking_active_changed.emit(False)

        self._set_state(VoiceState.IDLE)
        if was_recording or was_speaking or was_busy:
            self._emit_activity("Voice activity stopped and cancelled immediately.", "INFO")

    # ──────────────────────────────────────────
    # Internal Helpers
    # ──────────────────────────────────────────

    def _set_state(self, new_state: VoiceState) -> None:
        if self._state != new_state:
            self._state = new_state
            self.voice_state_changed.emit(new_state.value)

    def _emit_activity(self, message: str, level: str = "INFO") -> None:
        logger.log(getattr(logging, level, logging.INFO), message)
        self.voice_activity_event.emit(message, level)

    def _emit_error(self, message: str) -> None:
        self._last_error = message
        logger.error(message)
        self.voice_activity_event.emit(message, "ERROR")
        self.voice_error.emit(message)
