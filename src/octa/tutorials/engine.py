"""End-to-End Tutorial Engine for OCTA (Phase 6.0).

Coordinates the desktop UI and task flow with:
- User-initiated screen capture (`ScreenCapturer`) and single-use consent (`ConsentManager`)
- Multimodal LLM adapter (`OpenAICompatibleAdapter`) and progress verifier (`ProgressVerifier`)
- Passive visual guidance overlay (`GuidanceOverlayWidget`)
- Optional push-to-talk / read-aloud voice interaction (`VoiceInteractionController`)
- Full text-based step navigation and manual completion fallback when AI or voice features
  are unavailable or unconfigured.

Slow or network-bound LLM verification and instruction generation calls execute off the Qt
UI thread using a managed `QThread` worker with immediate cancellation and bounded shutdown.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Self

from PySide6.QtCore import QEventLoop, QObject, QThread, QTimer, Signal, Slot

from octa.core.config import AppConfig
from octa.intelligence.capture import CapturedImage, CaptureRegion, ScreenCapturer
from octa.intelligence.consent import ConsentDecision, ConsentManager, ConsentRequest
from octa.intelligence.exceptions import (
    CaptureRegionError,
    ConsentDeclinedError,
    IntelligenceError,
    MissingCredentialsError,
    OverlayError,
    TaskValidationError,
    UserActionRequiredError,
)
from octa.intelligence.llm import LLMConfig, OpenAICompatibleAdapter
from octa.intelligence.overlay import GuidanceOverlayWidget
from octa.intelligence.tasks import OverlayHint, TaskDefinition, TaskStep
from octa.intelligence.verifier import (
    VERIFICATION_SYSTEM_PROMPT,
    GuidanceInstruction,
    ProgressResult,
    ProgressStatus,
    ProgressVerifier,
    build_verification_prompt,
)
from octa.tutorials.catalog import BUILTIN_TUTORIALS, TutorialCatalog
from octa.tutorials.feedback import (
    FeedbackCollector,
    TutorialFeedback,
    TutorialSessionMetrics,
)
from octa.tutorials.plugins import (
    LoadedTutorialPlugin,
    PluginDiscoveryResult,
)
from octa.voice.controller import VoiceInteractionController, build_default_companion_task

logger = logging.getLogger("octa.tutorials.engine")

DEFAULT_COMPANION_TASK_ID = build_default_companion_task().task_id


class TutorialSessionState(StrEnum):
    """High-level lifecycle states for an interactive tutorial session."""

    IDLE = "IDLE"
    ACTIVE = "ACTIVE"
    VERIFYING = "VERIFYING"
    GENERATING_GUIDANCE = "GENERATING_GUIDANCE"
    COMPLETED = "COMPLETED"
    ERROR = "ERROR"


@dataclass(frozen=True)
class StepProgressRecord:
    """Progress tracking record for a single tutorial step."""

    step_id: str
    order: int
    title: str
    completed: bool = False
    completion_mode: str = "uncompleted"  # "uncompleted" | "verified_ai" | "manual_text"
    last_verification: ProgressResult | None = None


class _TutorialWorkerThread(QThread):
    """Background QThread executing slow or network-bound tutorial operations off the UI thread."""

    work_finished = Signal(int, object, object, int)  # op_id, result, error, worker_thread_id

    def __init__(
        self,
        op_id: int,
        task_fn: Callable[[], Any],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._op_id = op_id
        self._task_fn = task_fn

    def run(self) -> None:
        worker_tid = threading.get_ident()
        try:
            result = self._task_fn()
            self.work_finished.emit(self._op_id, result, None, worker_tid)
        except BaseException as exc:  # noqa: BLE001
            self.work_finished.emit(self._op_id, None, exc, worker_tid)


class TutorialEngine(QObject):
    """Cohesive tutorial engine connecting tasks, capture, consent, AI verifier, overlay, and voice.

    Provides both AI-verified step progression (gated on explicit user initiation and
    single-use screen-share consent) and a complete text-based flow when optional AI or
    voice features are unconfigured.
    """

    session_state_changed = Signal(str)  # TutorialSessionState.value
    tutorial_changed = Signal(str, str)  # task_id, title
    step_changed = Signal(int, int, str, str)  # step_index, total_steps, step_id, instruction_text
    verification_started = Signal(str)  # step_id
    verification_completed = Signal(object)  # ProgressResult
    tutorial_completed = Signal(str, str)  # task_id, title
    companion_state_requested = Signal(str)  # "IDLE" | "LISTENING" | "THINKING" | "SUCCESS"
    overlay_updated = Signal(int, int)  # num_highlights, num_arrows
    activity_logged = Signal(str, str)  # message, level
    error_occurred = Signal(str)  # message
    feedback_recorded = Signal(object)  # TutorialFeedback
    session_metrics_updated = Signal(object)  # TutorialSessionMetrics
    tutorials_catalog_reloaded = Signal()  # Emitted when external plugins or tutorials are reloaded

    def __init__(
        self,
        *,
        config: AppConfig | None = None,
        capturer: ScreenCapturer | None = None,
        consent_manager: ConsentManager | None = None,
        llm_adapter: OpenAICompatibleAdapter | None = None,
        verifier: ProgressVerifier | None = None,
        overlay: GuidanceOverlayWidget | None = None,
        voice_controller: VoiceInteractionController | None = None,
        tutorials: Sequence[TaskDefinition] | None = None,
        overlay_enabled: bool = False,
        feedback_collector: FeedbackCollector | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config if config is not None else AppConfig()

        if tutorials is not None:
            self._catalog_manager = TutorialCatalog(builtin_tutorials=tutorials, plugins_dir=None)
        else:
            self._catalog_manager = TutorialCatalog(
                builtin_tutorials=BUILTIN_TUTORIALS,
                plugins_dir=self._config.tutorial_plugin_dir,
            )

        catalog = self._catalog_manager.get_all_tutorials()
        if not catalog:
            raise TaskValidationError("TutorialEngine requires at least one TaskDefinition.")
        for item in catalog:
            if not isinstance(item, TaskDefinition):
                raise TaskValidationError(
                    f"Expected TaskDefinition in tutorials catalog, got {type(item).__name__}."
                )
        self._tutorials: tuple[TaskDefinition, ...] = catalog
        initial_task = catalog[0]

        # Adopt a custom task already loaded into an injected voice controller instead
        # of silently replacing it: reuse the matching catalog entry, or register the
        # custom task at the front of the catalog. The voice controller's built-in
        # default companion task never overrides the tutorial catalog.
        if voice_controller is not None:
            voice_task = voice_controller.active_task
            matched = next((t for t in catalog if t.task_id == voice_task.task_id), None)
            if matched is not None:
                initial_task = matched
            elif voice_task.task_id != DEFAULT_COMPANION_TASK_ID:
                self._tutorials = (voice_task, *catalog)
                initial_task = voice_task

        self._capturer = (
            capturer
            if capturer is not None
            else ScreenCapturer(save_screenshots=self._config.capture_save_screenshots)
        )
        self._consent_manager = consent_manager if consent_manager is not None else ConsentManager()
        if llm_adapter is not None:
            self._llm_adapter = llm_adapter
        else:
            llm_config = LLMConfig.from_app_config(self._config)
            self._llm_adapter = OpenAICompatibleAdapter(
                llm_config,
                consent_manager=self._consent_manager,
            )
        self._verifier = verifier if verifier is not None else ProgressVerifier(self._llm_adapter)
        self._overlay = overlay if overlay is not None else GuidanceOverlayWidget()
        self._overlay_enabled = bool(overlay_enabled)

        storage_dir = (
            self._config.app_dir / "tutorial_feedback" if self._config.app_dir is not None else None
        )
        self._feedback_collector = (
            feedback_collector
            if feedback_collector is not None
            else FeedbackCollector(storage_dir=storage_dir)
        )
        self._session_start_mono: float = time.monotonic()
        self._session_start_iso: str = datetime.now(UTC).isoformat()
        self._hints_requested: int = 0
        self._ai_verifications_attempted: int = 0
        self._ai_verifications_passed: int = 0
        self._manual_completions: int = 0
        self._overlay_toggles: int = 0

        self._voice_controller = voice_controller
        self._syncing_voice = False

        self._state = TutorialSessionState.ACTIVE
        self._active_task: TaskDefinition = initial_task
        self._step_index: int = 0
        self._step_records: list[StepProgressRecord] = self._build_initial_step_records(
            self._active_task
        )
        self._current_instruction: GuidanceInstruction = self._build_local_step_instruction(
            self._active_task,
            self._active_task.steps[0],
        )
        self._last_verification_result: ProgressResult | None = None
        self._last_error: str = ""
        self._last_worker_thread_id: int | None = None

        self._next_op_id: int = 0
        self._active_op_id: int | None = None
        self._active_op_kind: str = ""
        self._active_op_step_id: str = ""
        self._active_op_auto_speak: bool = False
        self._cancelled_op_ids: set[int] = set()
        self._active_workers: set[_TutorialWorkerThread] = set()

        if self._voice_controller is not None:
            self._sync_voice_task_and_step()
            self._voice_controller.instruction_ready.connect(self._on_voice_instruction_ready)

        self._sync_overlay(self._current_instruction.overlay_cues)

    @classmethod
    def from_app_config(
        cls,
        config: AppConfig | None = None,
        *,
        capturer: ScreenCapturer | None = None,
        consent_manager: ConsentManager | None = None,
        llm_adapter: OpenAICompatibleAdapter | None = None,
        verifier: ProgressVerifier | None = None,
        overlay: GuidanceOverlayWidget | None = None,
        voice_controller: VoiceInteractionController | None = None,
        tutorials: Sequence[TaskDefinition] | None = None,
        overlay_enabled: bool = False,
        feedback_collector: FeedbackCollector | None = None,
        parent: QObject | None = None,
    ) -> Self:
        """Construct a TutorialEngine from an AppConfig instance."""
        resolved = config if config is not None else AppConfig.load()
        return cls(
            config=resolved,
            capturer=capturer,
            consent_manager=consent_manager,
            llm_adapter=llm_adapter,
            verifier=verifier,
            overlay=overlay,
            voice_controller=voice_controller,
            tutorials=tutorials,
            overlay_enabled=overlay_enabled,
            feedback_collector=feedback_collector,
            parent=parent,
        )

    # ──────────────────────────────────────────
    # Properties
    # ──────────────────────────────────────────

    @property
    def state(self) -> TutorialSessionState:
        """Current tutorial session state."""
        return self._state

    @property
    def available_tutorials(self) -> tuple[TaskDefinition, ...]:
        """Tuple of available sample and loaded tutorials."""
        return self._tutorials

    @property
    def active_task(self) -> TaskDefinition:
        """Currently selected tutorial TaskDefinition."""
        return self._active_task

    @property
    def current_step(self) -> TaskStep:
        """Currently active TaskStep."""
        return self._active_task.steps[self._step_index]

    @property
    def current_step_index(self) -> int:
        """Zero-based index of the current tutorial step."""
        return self._step_index

    @property
    def total_steps(self) -> int:
        """Total number of steps in the active tutorial."""
        return len(self._active_task.steps)

    @property
    def current_instruction(self) -> GuidanceInstruction:
        """Current GuidanceInstruction displayed to the user."""
        return self._current_instruction

    @property
    def step_records(self) -> tuple[StepProgressRecord, ...]:
        """Immutable snapshot of per-step completion records for the active tutorial."""
        return tuple(self._step_records)

    @property
    def completed_steps_count(self) -> int:
        """Count of completed steps in the active tutorial."""
        return sum(1 for record in self._step_records if record.completed)

    @property
    def last_verification_result(self) -> ProgressResult | None:
        """Most recent ProgressResult from AI verification, or None."""
        return self._last_verification_result

    @property
    def last_error(self) -> str:
        """Most recent privacy-safe error message, or empty string."""
        return self._last_error

    @property
    def last_worker_thread_id(self) -> int | None:
        """Thread identifier of the most recent background worker execution (for diagnostics)."""
        return self._last_worker_thread_id

    @property
    def is_busy(self) -> bool:
        """Return True when a background AI verification or guidance operation is in flight."""
        return self._active_op_id is not None

    @property
    def is_ai_configured(self) -> bool:
        """Return True when an LLM API key is configured for remote AI verification."""
        return self._llm_adapter.config.has_credentials

    @property
    def is_voice_configured(self) -> bool:
        """Return True when either STT or TTS voice providers are enabled."""
        if self._voice_controller is None:
            return False
        return self._voice_controller.is_stt_enabled or self._voice_controller.is_tts_enabled

    @property
    def is_overlay_enabled(self) -> bool:
        """Return True when the passive visual guidance overlay is enabled."""
        return self._overlay_enabled

    @property
    def overlay_widget(self) -> GuidanceOverlayWidget:
        """Passive click-through visual guidance overlay widget."""
        return self._overlay

    @property
    def capturer(self) -> ScreenCapturer:
        """User-initiated screen capturer."""
        return self._capturer

    @property
    def consent_manager(self) -> ConsentManager:
        """Single-use screen-share consent manager."""
        return self._consent_manager

    @property
    def verifier(self) -> ProgressVerifier:
        """Multimodal progress verifier."""
        return self._verifier

    @property
    def catalog_manager(self) -> TutorialCatalog:
        """Underlying tutorial catalog manager."""
        return self._catalog_manager

    @property
    def loaded_plugins(self) -> tuple[LoadedTutorialPlugin, ...]:
        """Discovered external tutorial plugins."""
        return self._catalog_manager.loaded_plugins

    @property
    def plugin_discovery_result(self) -> PluginDiscoveryResult:
        """Full plugin discovery result with diagnostics."""
        return self._catalog_manager.discovery_result

    def is_plugin_tutorial(self, task_id: str) -> bool:
        """Return True if task_id belongs to a loaded external plugin."""
        return self._catalog_manager.is_plugin_tutorial(task_id)

    def get_tutorial_provenance(self, task_id: str) -> str | None:
        """Return plugin name if task_id is from an external plugin, otherwise None."""
        return self._catalog_manager.get_tutorial_provenance(task_id)

    def reload_plugins(self, plugins_dir: Path | str | None = None) -> PluginDiscoveryResult:
        """Reload external tutorial plugins from disk and update available tutorials."""
        result = self._catalog_manager.reload_plugins(plugins_dir)
        self._tutorials = self._catalog_manager.get_all_tutorials()

        # If active task is no longer present, reset to first tutorial
        if not any(t.task_id == self._active_task.task_id for t in self._tutorials):
            self.select_tutorial(self._tutorials[0].task_id)

        self.activity_logged.emit(
            f"Tutorial plugins reloaded: {len(result.loaded_plugins)} loaded, "
            f"{len(result.tutorials)} external tutorials, {len(result.diagnostics)} diagnostics.",
            "INFO",
        )
        self.tutorials_catalog_reloaded.emit()
        return result

    def get_tutorial_status_summary(self) -> str:
        """Return a concise human-readable tutorial progress summary for the UI."""
        step = self.current_step
        provenance = self.get_tutorial_provenance(self._active_task.task_id)
        prov_tag = f" [Plugin: {provenance}]" if provenance else ""
        return (
            f"Tutorial: {self._active_task.title} ({self._active_task.application}){prov_tag} — "
            f"Step {step.order}/{self.total_steps}: {step.title} [{self._state.value}]"
        )

    def get_verification_mode_summary(self) -> str:
        """Return a clear summary of verification status and text-flow availability."""
        if self._last_verification_result is not None:
            res = self._last_verification_result
            status_upper = res.status.value.upper()
            return (
                f"Verification: {status_upper} (confidence={res.confidence:.2f}) — {res.reasoning}"
            )
        if self.is_ai_configured:
            return (
                "Verification Mode: AI Capture & Verify Ready "
                "(Single-Use Consent Required; Text Flow Also Available)"
            )
        return (
            "Verification Mode: Text / Manual Flow Active "
            "(AI Key Not Configured — use Complete Step or Next/Previous Step)"
        )

    @property
    def feedback_collector(self) -> FeedbackCollector:
        """Collector managing user feedback and session metrics."""
        return self._feedback_collector

    def get_session_metrics(self) -> TutorialSessionMetrics:
        """Return a snapshot of metrics for the active tutorial session."""
        duration = max(0.0, time.monotonic() - self._session_start_mono)
        return TutorialSessionMetrics(
            task_id=self._active_task.task_id,
            start_time=self._session_start_iso,
            end_time=None,
            duration_seconds=duration,
            total_steps=self.total_steps,
            completed_steps=self.completed_steps_count,
            hints_requested=self._hints_requested,
            ai_verifications_attempted=self._ai_verifications_attempted,
            ai_verifications_passed=self._ai_verifications_passed,
            manual_completions=self._manual_completions,
            overlay_toggles=self._overlay_toggles,
            completed=self._state == TutorialSessionState.COMPLETED,
        )

    def record_feedback(
        self,
        rating: int,
        *,
        difficulty: str = "just_right",
        comment: str = "",
        step_id: str | None = None,
    ) -> TutorialFeedback:
        """Record structured user feedback for the current step or active tutorial."""
        target_step_id = (
            step_id.strip() if step_id and step_id.strip() else self.current_step.step_id
        )
        feedback = TutorialFeedback(
            task_id=self._active_task.task_id,
            step_id=target_step_id,
            rating=rating,
            difficulty=difficulty,
            comment=comment,
        )
        self._feedback_collector.record_feedback(feedback)
        self.feedback_recorded.emit(feedback)
        self._emit_activity(
            f"User feedback recorded for '{feedback.task_id}/{feedback.step_id}': "
            f"{feedback.rating}/5 stars ({feedback.difficulty}).",
            "INFO",
        )
        return feedback

    # ──────────────────────────────────────────
    # Tutorial Selection & Session Control
    # ──────────────────────────────────────────

    def select_tutorial(
        self,
        task_id_or_task: str | TaskDefinition,
        *,
        start_immediately: bool = True,
    ) -> GuidanceInstruction:
        """Select a tutorial by task_id or TaskDefinition and reset progress to Step 1."""
        self.cancel_active_operation()
        self._finalize_active_session(completed=False)

        if isinstance(task_id_or_task, TaskDefinition):
            selected = task_id_or_task
        elif isinstance(task_id_or_task, str) and task_id_or_task.strip():
            target_id = task_id_or_task.strip()
            matched = next((t for t in self._tutorials if t.task_id == target_id), None)
            if matched is None:
                available = ", ".join(t.task_id for t in self._tutorials)
                raise TaskValidationError(
                    f"Unknown tutorial task_id '{target_id}'. Available: {available}."
                )
            selected = matched
        else:
            raise TaskValidationError(
                "select_tutorial requires a task_id string or TaskDefinition."
            )

        self._active_task = selected
        self._step_index = 0
        self._step_records = self._build_initial_step_records(selected)
        self._last_verification_result = None
        self._last_error = ""
        step = selected.steps[0]
        self._current_instruction = self._build_local_step_instruction(selected, step)

        self._set_state(
            TutorialSessionState.ACTIVE if start_immediately else TutorialSessionState.IDLE
        )
        self.tutorial_changed.emit(selected.task_id, selected.title)
        self._sync_voice_task_and_step()
        self._sync_overlay(self._current_instruction.overlay_cues)
        self._emit_step_changed()
        self.companion_state_requested.emit("IDLE")
        self._emit_activity(
            f"Selected tutorial '{selected.title}' ({selected.task_id}) — "
            f"Step 1/{len(selected.steps)}: {step.title}.",
            "INFO",
        )
        return self._current_instruction

    @Slot()
    def start_tutorial(self, *, auto_speak: bool = False) -> GuidanceInstruction:
        """Start or restart the active tutorial from Step 1."""
        instruction = self.reset_tutorial()
        if auto_speak:
            self._maybe_speak(instruction)
        return instruction

    @Slot()
    def reset_tutorial(self) -> GuidanceInstruction:
        """Reset the active tutorial back to Step 1 with all steps marked uncompleted."""
        self.cancel_active_operation()
        self._finalize_active_session(completed=False)
        self._step_index = 0
        self._step_records = self._build_initial_step_records(self._active_task)
        self._last_verification_result = None
        self._last_error = ""
        step = self.current_step
        self._current_instruction = self._build_local_step_instruction(self._active_task, step)
        self._set_state(TutorialSessionState.ACTIVE)
        self._sync_voice_task_and_step()
        self._sync_overlay(self._current_instruction.overlay_cues)
        self._emit_step_changed()
        self.companion_state_requested.emit("IDLE")
        self._emit_activity(
            f"Reset tutorial '{self._active_task.title}' to Step 1/{self.total_steps}.",
            "INFO",
        )
        return self._current_instruction

    @Slot()
    def stop_tutorial(self) -> None:
        """Stop the active tutorial session, cancel in-flight work, and clear overlays."""
        self.cancel_active_operation()
        self._overlay.clear_annotations()
        self._overlay.hide()
        self.overlay_updated.emit(0, 0)
        self._set_state(TutorialSessionState.IDLE)
        self.companion_state_requested.emit("IDLE")
        self._emit_activity(f"Tutorial '{self._active_task.title}' stopped.", "INFO")

    # ──────────────────────────────────────────
    # Text-Based Step Navigation & Completion
    # ──────────────────────────────────────────

    @Slot()
    def next_step(self, *, auto_speak: bool = False) -> GuidanceInstruction:
        """Advance to the next tutorial step using the local text-based flow."""
        self.cancel_active_operation()
        self._last_error = ""

        if self._step_index + 1 < self.total_steps:
            self._step_index += 1
            step = self.current_step
            self._current_instruction = self._build_local_step_instruction(self._active_task, step)
            if self._state != TutorialSessionState.COMPLETED:
                self._set_state(TutorialSessionState.ACTIVE)
            self._sync_voice_task_and_step()
            self._sync_overlay(self._current_instruction.overlay_cues)
            self._emit_step_changed()
            self._emit_activity(
                f"Advanced to Step {step.order}/{self.total_steps}: {step.title}.",
                "INFO",
            )
        else:
            step = self.current_step
            self._current_instruction = GuidanceInstruction(
                task_id=self._active_task.task_id,
                step_id=step.step_id,
                instruction_text=(
                    f"You are on the final step (Step {step.order} of {self.total_steps}): "
                    f"{step.title}. {step.instruction}"
                ),
                summary=step.title,
                overlay_cues=step.overlay_cues,
            )
            self._sync_overlay(self._current_instruction.overlay_cues)
            self._emit_step_changed()
            self._emit_activity(
                f"Already on final step ({step.order}/{self.total_steps}) of "
                f"'{self._active_task.title}'.",
                "INFO",
            )

        if auto_speak:
            self._maybe_speak(self._current_instruction)
        return self._current_instruction

    @Slot()
    def previous_step(self, *, auto_speak: bool = False) -> GuidanceInstruction:
        """Move back to the previous tutorial step using the local text-based flow."""
        self.cancel_active_operation()
        self._last_error = ""

        if self._step_index > 0:
            self._step_index -= 1
            step = self.current_step
            self._current_instruction = self._build_local_step_instruction(self._active_task, step)
            self._set_state(TutorialSessionState.ACTIVE)
            self._sync_voice_task_and_step()
            self._sync_overlay(self._current_instruction.overlay_cues)
            self._emit_step_changed()
            self._emit_activity(
                f"Returned to Step {step.order}/{self.total_steps}: {step.title}.",
                "INFO",
            )
        else:
            step = self.current_step
            self._current_instruction = GuidanceInstruction(
                task_id=self._active_task.task_id,
                step_id=step.step_id,
                instruction_text=(
                    f"You are on the first step (Step 1 of {self.total_steps}): "
                    f"{step.title}. {step.instruction}"
                ),
                summary=step.title,
                overlay_cues=step.overlay_cues,
            )
            if self._state != TutorialSessionState.COMPLETED:
                self._set_state(TutorialSessionState.ACTIVE)
            self._sync_overlay(self._current_instruction.overlay_cues)
            self._emit_step_changed()
            self._emit_activity("Already on the first step.", "INFO")

        if auto_speak:
            self._maybe_speak(self._current_instruction)
        return self._current_instruction

    @Slot()
    def request_hint(self, *, auto_speak: bool = False) -> GuidanceInstruction:
        """Display the hint for the current tutorial step (available without AI or voice)."""
        self.cancel_active_operation()
        self._last_error = ""
        self._hints_requested += 1
        step = self.current_step
        if step.hints:
            hints_joined = " ".join(step.hints)
            text = f"Hint for Step {step.order} ({step.title}): {hints_joined}"
        else:
            text = (
                f"No extra hint for Step {step.order} ({step.title}). "
                f"Goal: {step.expected_outcome.description}"
            )
        self._current_instruction = GuidanceInstruction(
            task_id=self._active_task.task_id,
            step_id=step.step_id,
            instruction_text=text,
            summary=f"Hint: {step.title}",
            overlay_cues=step.overlay_cues,
        )
        self._sync_overlay(self._current_instruction.overlay_cues)
        self._emit_step_changed()
        self._emit_activity(f"Displayed hint for step '{step.step_id}'.", "INFO")
        if auto_speak:
            self._maybe_speak(self._current_instruction)
        return self._current_instruction

    @Slot()
    def complete_current_step_manually(
        self,
        *,
        user_initiated: bool = True,
        auto_speak: bool = False,
    ) -> GuidanceInstruction:
        """Mark the current step completed via the text-based flow and advance or finish."""
        if not isinstance(user_initiated, bool) or not user_initiated:
            raise UserActionRequiredError(
                "Manual step completion requires an explicit user action (user_initiated=True)."
            )

        self.cancel_active_operation()
        self._last_error = ""
        self._manual_completions += 1
        step = self.current_step
        self._mark_step_record_completed(
            self._step_index,
            mode="manual_text",
            verification=None,
        )
        self.companion_state_requested.emit("SUCCESS")

        if self._step_index + 1 < self.total_steps:
            self._step_index += 1
            next_task_step = self.current_step
            self._current_instruction = GuidanceInstruction(
                task_id=self._active_task.task_id,
                step_id=next_task_step.step_id,
                instruction_text=(
                    f"Step {step.order} marked complete. "
                    f"Next — Step {next_task_step.order} of {self.total_steps}: "
                    f"{next_task_step.title}. {next_task_step.instruction}"
                ),
                summary=next_task_step.title,
                overlay_cues=next_task_step.overlay_cues,
            )
            self._set_state(TutorialSessionState.ACTIVE)
            self._sync_voice_task_and_step()
            self._sync_overlay(self._current_instruction.overlay_cues)
            self._emit_step_changed()
            self._emit_activity(
                f"Step {step.order} completed via text flow; advanced to Step "
                f"{next_task_step.order}/{self.total_steps}: {next_task_step.title}.",
                "INFO",
            )
        else:
            self._current_instruction = GuidanceInstruction(
                task_id=self._active_task.task_id,
                step_id=step.step_id,
                instruction_text=(
                    f"Tutorial Complete! You finished all {self.total_steps} steps of "
                    f"'{self._active_task.title}'."
                ),
                summary=f"Completed: {self._active_task.title}",
                overlay_cues=(),
            )
            self._set_state(TutorialSessionState.COMPLETED)
            self._sync_overlay(())
            self._emit_step_changed()
            self._finalize_active_session(completed=True)
            self.tutorial_completed.emit(self._active_task.task_id, self._active_task.title)
            self._emit_activity(
                f"Tutorial '{self._active_task.title}' completed via text flow!",
                "INFO",
            )

        if auto_speak:
            self._maybe_speak(self._current_instruction)
        return self._current_instruction

    # ──────────────────────────────────────────
    # AI Screen Capture + Consent + Verification
    # ──────────────────────────────────────────

    def verify_current_step(
        self,
        *,
        user_initiated: bool = False,
        region: CaptureRegion | None = None,
        consent_decision: ConsentDecision | None = None,
        auto_speak: bool = False,
    ) -> int:
        """Capture the screen, request single-use consent, and verify progress on a worker thread.

        Screen capture and the modal consent dialog run on the Qt UI thread; the slow/network
        LLM verification call executes off the Qt UI thread in `_TutorialWorkerThread`.

        Returns:
            int: Positive operation ID for the background verification job.
        """
        if not isinstance(user_initiated, bool) or not user_initiated:
            raise UserActionRequiredError(
                "Step verification with screen capture starts only from an explicit user action "
                "(user_initiated=True)."
            )

        self.cancel_active_operation()
        self._last_error = ""

        task = self._active_task
        step = self.current_step

        # 1. Capture screen or target region on the Qt UI thread (in-memory by default)
        image = self._capture_for_step(step, explicit_region=region)

        # 2. Request or validate single-use user consent on the Qt UI thread before spawning worker
        #    The request mirrors exactly what ProgressVerifier.verify_step_with_image sends so
        #    the single-use decision validates against the real outbound payload.
        user_prompt = build_verification_prompt(task, step)
        consent_request: ConsentRequest = self._llm_adapter.build_consent_request(
            image=image,
            system_prompt=VERIFICATION_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            purpose=f"Verify progress for step '{step.title}'",
            task_title=task.title,
            step_title=step.title,
        )

        try:
            decision = (
                consent_decision
                if consent_decision is not None
                else self._consent_manager.request_consent(consent_request)
            )
            if not isinstance(decision, ConsentDecision) or not decision.approved:
                raise ConsentDeclinedError(
                    "Remote AI processing aborted because user did not approve "
                    "screen-share consent."
                )
        except ConsentDeclinedError as exc:
            self._last_error = str(exc)
            self._set_state(TutorialSessionState.ACTIVE)
            self.companion_state_requested.emit("IDLE")
            self._emit_activity(
                f"Screen-share consent declined for step '{step.step_id}'; "
                "continuing with local text-based flow.",
                "WARNING",
            )
            raise

        # 3. Run slow/network verification off the Qt UI thread
        self._ai_verifications_attempted += 1
        self._next_op_id += 1
        op_id = self._next_op_id
        self._active_op_id = op_id
        self._active_op_kind = "verify"
        self._active_op_step_id = step.step_id
        self._active_op_auto_speak = bool(auto_speak)

        self._set_state(TutorialSessionState.VERIFYING)
        self.verification_started.emit(step.step_id)
        self.companion_state_requested.emit("THINKING")
        self._emit_activity(
            f"Verifying step '{step.step_id}' ({step.title}) with AI in background...",
            "INFO",
        )

        def _worker_task() -> ProgressResult:
            return self._verifier.verify_step_with_image(
                task,
                step,
                image,
                consent_decision=decision,
            )

        self._start_worker(op_id, _worker_task)
        return op_id

    def refresh_step_instruction_with_ai(
        self,
        *,
        auto_speak: bool = False,
    ) -> int:
        """Generate tailored text guidance for the current step on a background worker thread."""
        self.cancel_active_operation()
        self._last_error = ""

        task = self._active_task
        step = self.current_step

        self._next_op_id += 1
        op_id = self._next_op_id
        self._active_op_id = op_id
        self._active_op_kind = "guidance"
        self._active_op_step_id = step.step_id
        self._active_op_auto_speak = bool(auto_speak)

        self._set_state(TutorialSessionState.GENERATING_GUIDANCE)
        self.companion_state_requested.emit("THINKING")
        self._emit_activity(
            f"Generating AI step guidance for '{step.step_id}' in background...",
            "INFO",
        )

        def _worker_task() -> GuidanceInstruction:
            return self._verifier.generate_step_instruction(task, step)

        self._start_worker(op_id, _worker_task)
        return op_id

    # ──────────────────────────────────────────
    # Overlay Management
    # ──────────────────────────────────────────

    @Slot(bool)
    def set_overlay_enabled(self, enabled: bool) -> None:
        """Enable or disable the passive click-through visual guidance overlay."""
        self._overlay_enabled = bool(enabled)
        self._overlay_toggles += 1
        self._sync_overlay(self._current_instruction.overlay_cues)
        state_word = "enabled" if self._overlay_enabled else "disabled"
        self._emit_activity(f"Passive visual guidance overlay {state_word}.", "INFO")

    # ──────────────────────────────────────────
    # Cancellation, Waiting & Clean Shutdown
    # ──────────────────────────────────────────

    @Slot()
    def cancel_active_operation(self) -> bool:
        """Immediately cancel any in-flight background AI operation or voice activity.

        Returns:
            bool: True if an in-flight background operation was cancelled.
        """
        was_cancelled = False
        if self._active_op_id is not None:
            cancelled_id = self._active_op_id
            self._cancelled_op_ids.add(cancelled_id)
            self._active_op_id = None
            self._active_op_kind = ""
            self._active_op_step_id = ""
            self._active_op_auto_speak = False
            was_cancelled = True
            if self._state in (
                TutorialSessionState.VERIFYING,
                TutorialSessionState.GENERATING_GUIDANCE,
            ):
                self._set_state(TutorialSessionState.ACTIVE)
            self.companion_state_requested.emit("IDLE")
            self._emit_activity(
                f"Cancelled in-flight tutorial background operation (#{cancelled_id}).",
                "INFO",
            )

        if self._voice_controller is not None and (
            self._voice_controller.is_microphone_active or self._voice_controller.is_speaking
        ):
            self._voice_controller.cancel_or_stop()

        return was_cancelled

    def wait_for_active_operation(self, timeout_ms: int = 3000) -> bool:
        """Process events until the current background operation finishes or timeout expires."""
        if self._active_op_id is None and not self._active_workers:
            return True

        deadline = time.monotonic() + max(0.05, timeout_ms / 1000.0)
        loop = QEventLoop()
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)

        while (self._active_op_id is not None or self._active_workers) and (
            time.monotonic() < deadline
        ):
            remaining_ms = max(10, min(50, int((deadline - time.monotonic()) * 1000)))
            timer.start(remaining_ms)
            loop.exec()
            timer.stop()

        return self._active_op_id is None and not self._active_workers

    def shutdown(self, timeout_ms: int = 2000) -> None:
        """Cancel active operations, stop voice/overlay, and wait for background workers."""
        self.cancel_active_operation()
        if self._voice_controller is not None:
            self._voice_controller.cancel_or_stop()

        self._overlay.clear_annotations()
        self._overlay.hide()
        self.overlay_updated.emit(0, 0)

        workers_snapshot = list(self._active_workers)
        per_worker_ms = max(100, timeout_ms)
        for worker in workers_snapshot:
            if worker.isRunning():
                worker.wait(per_worker_ms)
            self._active_workers.discard(worker)

        self._set_state(TutorialSessionState.IDLE)

    # ──────────────────────────────────────────
    # Internal Worker & Signal Handlers
    # ──────────────────────────────────────────

    def _start_worker(self, op_id: int, task_fn: Callable[[], Any]) -> None:
        worker = _TutorialWorkerThread(op_id, task_fn, parent=self)
        self._active_workers.add(worker)
        worker.work_finished.connect(self._on_worker_finished)
        worker.finished.connect(lambda w=worker: self._cleanup_worker(w))
        worker.start()

    def _cleanup_worker(self, worker: _TutorialWorkerThread) -> None:
        self._active_workers.discard(worker)
        worker.deleteLater()

    def _on_worker_finished(
        self,
        op_id: int,
        result: object,
        error: object,
        worker_tid: int,
    ) -> None:
        self._last_worker_thread_id = worker_tid

        if op_id in self._cancelled_op_ids or op_id != self._active_op_id:
            self._cancelled_op_ids.discard(op_id)
            return

        op_kind = self._active_op_kind
        auto_speak = self._active_op_auto_speak
        self._active_op_id = None
        self._active_op_kind = ""
        self._active_op_step_id = ""
        self._active_op_auto_speak = False

        if error is not None:
            self._handle_worker_error(op_kind, error)
            return

        if op_kind == "verify" and isinstance(result, ProgressResult):
            self._handle_verification_result(result, auto_speak=auto_speak)
        elif op_kind == "guidance" and isinstance(result, GuidanceInstruction):
            self._handle_guidance_result(result, auto_speak=auto_speak)

    def _handle_worker_error(self, op_kind: str, error: object) -> None:
        if isinstance(error, MissingCredentialsError):
            msg = (
                "AI verification unavailable because OCTA_LLM_API_KEY is not configured. "
                "Use 'Complete Step (Text Flow)' or 'Next Step' to continue."
            )
            self._last_error = msg
            self._set_state(TutorialSessionState.ACTIVE)
            self.companion_state_requested.emit("IDLE")
            self._emit_error(msg)
            return

        if isinstance(error, IntelligenceError):
            msg = f"Tutorial AI {op_kind} error: {error}"
        else:
            msg = f"Unexpected tutorial {op_kind} error: {error}"

        self._last_error = msg
        self._set_state(TutorialSessionState.ERROR)
        self.companion_state_requested.emit("IDLE")
        self._emit_error(msg)

    def _handle_verification_result(
        self,
        result: ProgressResult,
        *,
        auto_speak: bool = False,
    ) -> None:
        self._last_verification_result = result
        step = self.current_step

        if result.status == ProgressStatus.COMPLETE:
            self._ai_verifications_passed += 1
            self._mark_step_record_completed(
                self._step_index,
                mode="verified_ai",
                verification=result,
            )
            self.companion_state_requested.emit("SUCCESS")

            if self._step_index + 1 < self.total_steps:
                self._step_index += 1
                next_task_step = self.current_step
                cues = result.overlay_cues if result.overlay_cues else next_task_step.overlay_cues
                self._current_instruction = GuidanceInstruction(
                    task_id=self._active_task.task_id,
                    step_id=next_task_step.step_id,
                    instruction_text=(
                        f"Verified Step {step.order} complete ({result.reasoning}). "
                        f"Next — Step {next_task_step.order} of {self.total_steps}: "
                        f"{next_task_step.title}. {next_task_step.instruction}"
                    ),
                    summary=next_task_step.title,
                    overlay_cues=cues,
                )
                self._set_state(TutorialSessionState.ACTIVE)
                self._sync_voice_task_and_step()
                self._sync_overlay(cues)
                self._emit_step_changed()
                self.verification_completed.emit(result)
                self._emit_activity(
                    f"AI verified Step {step.order} COMPLETE (confidence={result.confidence:.2f}); "
                    f"advanced to Step {next_task_step.order}/{self.total_steps}.",
                    "INFO",
                )
            else:
                self._current_instruction = GuidanceInstruction(
                    task_id=self._active_task.task_id,
                    step_id=step.step_id,
                    instruction_text=(
                        f"Tutorial Complete! AI verified the final step ({result.reasoning}). "
                        f"You finished all {self.total_steps} steps of '{self._active_task.title}'."
                    ),
                    summary=f"Completed: {self._active_task.title}",
                    overlay_cues=(),
                )
                self._set_state(TutorialSessionState.COMPLETED)
                self._sync_overlay(())
                self._emit_step_changed()
                self._finalize_active_session(completed=True)
                self.verification_completed.emit(result)
                self.tutorial_completed.emit(self._active_task.task_id, self._active_task.title)
                self._emit_activity(
                    f"Tutorial '{self._active_task.title}' completed with AI verification!",
                    "INFO",
                )
        else:
            # INCOMPLETE or UNCERTAIN — stay on current step and provide corrective guidance
            self._mark_step_record_uncompleted(self._step_index, verification=result)
            self._set_state(TutorialSessionState.ACTIVE)
            self.companion_state_requested.emit("IDLE")

            missing_note = (
                f" Missing: {', '.join(result.missing_elements)}."
                if result.missing_elements
                else ""
            )
            action_note = f" {result.next_instruction}" if result.next_instruction else ""
            status_label = (
                "Incomplete" if result.status == ProgressStatus.INCOMPLETE else "Uncertain"
            )
            cues = result.overlay_cues if result.overlay_cues else step.overlay_cues

            self._current_instruction = GuidanceInstruction(
                task_id=self._active_task.task_id,
                step_id=step.step_id,
                instruction_text=(
                    f"Step {step.order} {status_label}: {result.reasoning}{missing_note}"
                    f"{action_note} (Step goal: {step.instruction})"
                ),
                summary=f"{status_label}: {step.title}",
                overlay_cues=cues,
            )
            self._sync_overlay(cues)
            self._emit_step_changed()
            self.verification_completed.emit(result)
            self._emit_activity(
                f"AI verification for Step {step.order} returned {result.status.value.upper()} "
                f"(confidence={result.confidence:.2f}): {result.reasoning}",
                "INFO",
            )

        if auto_speak:
            self._maybe_speak(self._current_instruction)

    def _handle_guidance_result(
        self,
        instruction: GuidanceInstruction,
        *,
        auto_speak: bool = False,
    ) -> None:
        self._current_instruction = instruction
        self._set_state(TutorialSessionState.ACTIVE)
        self.companion_state_requested.emit("IDLE")
        self._sync_overlay(instruction.overlay_cues)
        self._emit_step_changed()
        self._emit_activity(
            f"Updated step guidance for '{instruction.step_id}' with AI.",
            "INFO",
        )
        if auto_speak:
            self._maybe_speak(instruction)

    # ──────────────────────────────────────────
    # Internal Helpers
    # ──────────────────────────────────────────

    def _capture_for_step(
        self,
        step: TaskStep,
        *,
        explicit_region: CaptureRegion | None = None,
    ) -> CapturedImage:
        if explicit_region is not None:
            return self._capturer.capture_region(explicit_region, user_initiated=True)

        target_region = step.expected_outcome.target_region
        if target_region is not None:
            try:
                return self._capturer.capture_region(target_region, user_initiated=True)
            except CaptureRegionError:
                logger.info(
                    "Step target_region exceeded current screen bounds; "
                    "falling back to full screen."
                )
        return self._capturer.capture_full_screen(user_initiated=True)

    @staticmethod
    def _build_initial_step_records(task: TaskDefinition) -> list[StepProgressRecord]:
        return [
            StepProgressRecord(
                step_id=step.step_id,
                order=step.order,
                title=step.title,
                completed=False,
                completion_mode="uncompleted",
                last_verification=None,
            )
            for step in task.steps
        ]

    @staticmethod
    def _build_local_step_instruction(
        task: TaskDefinition,
        step: TaskStep,
    ) -> GuidanceInstruction:
        return GuidanceInstruction(
            task_id=task.task_id,
            step_id=step.step_id,
            instruction_text=f"Step {step.order}: {step.title}. {step.instruction}",
            summary=step.title,
            overlay_cues=step.overlay_cues,
        )

    def _mark_step_record_completed(
        self,
        index: int,
        *,
        mode: str,
        verification: ProgressResult | None,
    ) -> None:
        if 0 <= index < len(self._step_records):
            existing = self._step_records[index]
            self._step_records[index] = StepProgressRecord(
                step_id=existing.step_id,
                order=existing.order,
                title=existing.title,
                completed=True,
                completion_mode=mode,
                last_verification=verification,
            )

    def _mark_step_record_uncompleted(
        self,
        index: int,
        *,
        verification: ProgressResult | None,
    ) -> None:
        if 0 <= index < len(self._step_records):
            existing = self._step_records[index]
            self._step_records[index] = StepProgressRecord(
                step_id=existing.step_id,
                order=existing.order,
                title=existing.title,
                completed=False,
                completion_mode="uncompleted",
                last_verification=verification,
            )

    def _sync_overlay(self, cues: Sequence[OverlayHint]) -> None:
        if not self._overlay_enabled or not cues:
            if (
                not self._overlay.isVisible()
                and not self._overlay.highlights
                and not self._overlay.arrows
            ):
                return
            self._overlay.clear_annotations()
            self._overlay.hide()
            self.overlay_updated.emit(0, 0)
            return

        try:
            self._overlay.apply_hints(cues)
        except OverlayError as exc:
            # Never render cues that fall outside the validated screen bounds.
            logger.warning("Skipped tutorial overlay cues outside screen bounds: %s", exc)
            self._overlay.clear_annotations()
            self._overlay.hide()
            self.overlay_updated.emit(0, 0)
            return
        self._overlay.show()
        self.overlay_updated.emit(
            len(self._overlay.highlights),
            len(self._overlay.arrows),
        )

    def _sync_voice_task_and_step(self) -> None:
        if self._voice_controller is None:
            return
        self._syncing_voice = True
        try:
            self._voice_controller.set_active_task(
                self._active_task,
                step_index=self._step_index,
            )
        finally:
            self._syncing_voice = False

    def _on_voice_instruction_ready(self, step_id: str, instruction_text: str) -> None:
        if self._syncing_voice or self._voice_controller is None:
            return
        voice_idx = self._voice_controller.current_step_index
        if 0 <= voice_idx < self.total_steps:
            self._step_index = voice_idx
        self._current_instruction = self._voice_controller.last_instruction
        self._sync_overlay(self._current_instruction.overlay_cues)
        self._emit_step_changed()
        logger.debug("Synchronized tutorial engine with voice step '%s'.", step_id)

    def _maybe_speak(self, instruction: GuidanceInstruction) -> None:
        if self._voice_controller is None or not self._voice_controller.is_tts_enabled:
            return
        try:
            self._voice_controller.speak_instruction(instruction, user_initiated=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Optional TTS playback skipped during tutorial flow: %s", exc)

    def _set_state(self, new_state: TutorialSessionState) -> None:
        if self._state != new_state:
            self._state = new_state
            self.session_state_changed.emit(new_state.value)

    def _emit_step_changed(self) -> None:
        step = self.current_step
        self.step_changed.emit(
            self._step_index + 1,
            self.total_steps,
            step.step_id,
            self._current_instruction.instruction_text,
        )

    def _emit_activity(self, message: str, level: str = "INFO") -> None:
        logger.log(getattr(logging, level, logging.INFO), message)
        self.activity_logged.emit(message, level)

    def _emit_error(self, message: str) -> None:
        self._last_error = message
        logger.error(message)
        self.activity_logged.emit(message, "ERROR")
        self.error_occurred.emit(message)

    def _finalize_active_session(self, *, completed: bool) -> None:
        has_activity = (
            self.completed_steps_count > 0
            or self._hints_requested > 0
            or self._ai_verifications_attempted > 0
            or self._manual_completions > 0
            or self._overlay_toggles > 0
        )
        if not has_activity and not completed:
            self._reset_session_counters()
            return

        duration = max(0.0, time.monotonic() - self._session_start_mono)
        now_iso = datetime.now(UTC).isoformat()
        metrics = TutorialSessionMetrics(
            task_id=self._active_task.task_id,
            start_time=self._session_start_iso,
            end_time=now_iso,
            duration_seconds=duration,
            total_steps=self.total_steps,
            completed_steps=self.completed_steps_count,
            hints_requested=self._hints_requested,
            ai_verifications_attempted=self._ai_verifications_attempted,
            ai_verifications_passed=self._ai_verifications_passed,
            manual_completions=self._manual_completions,
            overlay_toggles=self._overlay_toggles,
            completed=completed,
        )
        self._feedback_collector.record_session_metrics(metrics)
        self.session_metrics_updated.emit(metrics)
        self._reset_session_counters()

    def _reset_session_counters(self) -> None:
        self._session_start_mono = time.monotonic()
        self._session_start_iso = datetime.now(UTC).isoformat()
        self._hints_requested = 0
        self._ai_verifications_attempted = 0
        self._ai_verifications_passed = 0
        self._manual_completions = 0
        self._overlay_toggles = 0
