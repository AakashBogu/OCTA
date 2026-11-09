"""Structured progress verification and step instruction generation for OCTA.

Evaluates captured screenshots against task step expectations and enforces a
conservative evidence policy: never claims 'complete' when confidence is low,
required elements are missing, or evidence is unclear.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from octa.intelligence.capture import CapturedImage, CaptureRegion, ScreenCapturer
from octa.intelligence.consent import ConsentDecision
from octa.intelligence.exceptions import (
    MalformedModelOutputError,
    TaskValidationError,
)
from octa.intelligence.llm import OpenAICompatibleAdapter, extract_json_object
from octa.intelligence.tasks import (
    VALID_PROGRESS_STATUSES,
    GuidanceInstruction,
    OverlayHint,
    ProgressResult,
    ProgressStatus,
    TaskDefinition,
    TaskStep,
)

logger = logging.getLogger("octa.intelligence.verifier")

DEFAULT_MIN_COMPLETE_CONFIDENCE = 0.75

_AMBIGUOUS_EVIDENCE_REGEX = re.compile(
    r"\b(not\s+sure|unclear|cannot\s+tell|can't\s+tell|cannot\s+confirm|"
    r"can't\s+confirm|hard\s+to\s+see|ambiguous|occluded|blurry|unreadable)\b",
    re.IGNORECASE,
)

VERIFICATION_SYSTEM_PROMPT = (
    "You are OCTA's desktop tutorial progress verifier. Analyze the user's screenshot "
    "against the current task step and expected outcome. Respond ONLY with a valid JSON "
    "object containing: 'status' ('complete', 'incomplete', or 'uncertain'), "
    "'confidence' (number from 0.0 to 1.0), 'reasoning' (non-empty string), "
    "'observed_elements' (list of strings), 'missing_elements' (list of strings), "
    "'next_instruction' (string), and 'overlay_cues' (list of highlight/arrow objects). "
    "Never return 'complete' unless all expected elements are clearly visible."
)

INSTRUCTION_SYSTEM_PROMPT = (
    "You are OCTA's friendly desktop companion tutor. Generate clear, concise step-by-step "
    "guidance for the requested tutorial step. Respond ONLY with a valid JSON object "
    "containing: 'instruction_text' (non-empty string), 'summary' (string), and "
    "'overlay_cues' (optional list of highlight/arrow objects)."
)


def build_verification_prompt(task: TaskDefinition, step: TaskStep) -> str:
    """Build the user prompt describing the step and expected outcome to verify."""
    outcome = step.expected_outcome
    required_str = (
        ", ".join(outcome.required_elements) if outcome.required_elements else "None specified"
    )
    hint_line = (
        f"\nVerification Hint: {outcome.verification_hint}" if outcome.verification_hint else ""
    )
    return (
        f"Task: {task.title} (Application: {task.application})\n"
        f"Goal: {task.goal}\n"
        f"Step {step.order} ({step.step_id}): {step.title}\n"
        f"Instruction given to user: {step.instruction}\n"
        f"Expected Outcome: {outcome.description}\n"
        f"Required Visible Elements: {required_str}"
        f"{hint_line}\n\n"
        "Verify whether the screenshot clearly shows that this step is complete."
    )


def build_instruction_prompt(task: TaskDefinition, step: TaskStep) -> str:
    """Build the user prompt for generating natural-language step instructions."""
    hints_str = "; ".join(step.hints) if step.hints else "None"
    return (
        f"Task: {task.title} (Application: {task.application})\n"
        f"Overall Goal: {task.goal}\n"
        f"Current Step {step.order} ({step.step_id}): {step.title}\n"
        f"Base Instruction: {step.instruction}\n"
        f"Expected Outcome: {step.expected_outcome.description}\n"
        f"Helpful Hints: {hints_str}\n\n"
        "Provide clear, friendly guidance for the user to complete this step."
    )


def _validate_str_list(value: Any, field_name: str) -> tuple[str, ...]:
    """Validate that a JSON field is a list of non-empty strings."""
    if not isinstance(value, list):
        raise MalformedModelOutputError(f"Model field '{field_name}' must be a list of strings.")
    items: list[str] = []
    for idx, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise MalformedModelOutputError(
                f"Model field '{field_name}[{idx}]' must be a non-empty string."
            )
        items.append(item.strip())
    return tuple(items)


def _find_unconfirmed_required_elements(
    required_elements: tuple[str, ...],
    observed_elements: tuple[str, ...],
) -> tuple[str, ...]:
    """Return any required elements not confirmed among observed_elements."""
    if not required_elements:
        return ()
    observed_lower = [obs.lower() for obs in observed_elements]
    unconfirmed: list[str] = []
    for req in required_elements:
        req_low = req.lower()
        matched = any(req_low in obs for obs in observed_lower)
        if not matched:
            unconfirmed.append(req)
    return tuple(unconfirmed)


def parse_verification_output(
    raw_content: str,
    *,
    task: TaskDefinition,
    step: TaskStep,
    min_complete_confidence: float = DEFAULT_MIN_COMPLETE_CONFIDENCE,
) -> ProgressResult:
    """Parse and validate LLM verification output, enforcing conservative evidence rules.

    Never returns ProgressStatus.COMPLETE when confidence is below threshold,
    required elements are unconfirmed/missing, or reasoning expresses unclear evidence.
    """
    data = extract_json_object(raw_content)

    raw_status = data.get("status")
    if not isinstance(raw_status, str) or raw_status.strip().lower() not in VALID_PROGRESS_STATUSES:
        raise MalformedModelOutputError(
            f"Model output 'status' must be one of {sorted(VALID_PROGRESS_STATUSES)}, "
            f"got {raw_status!r}."
        )
    status = ProgressStatus(raw_status.strip().lower())

    raw_confidence = data.get("confidence")
    if isinstance(raw_confidence, bool) or not isinstance(raw_confidence, (int, float)):
        raise MalformedModelOutputError(
            f"Model output 'confidence' must be a number in [0.0, 1.0], got {raw_confidence!r}."
        )
    confidence = float(raw_confidence)
    if confidence < 0.0 or confidence > 1.0:
        raise MalformedModelOutputError(
            f"Model output 'confidence' must be between 0.0 and 1.0, got {confidence}."
        )

    raw_reasoning = data.get("reasoning")
    if not isinstance(raw_reasoning, str) or not raw_reasoning.strip():
        raise MalformedModelOutputError("Model output 'reasoning' must be a non-empty string.")
    reasoning = raw_reasoning.strip()

    next_instruction_raw = data.get("next_instruction", "")
    if not isinstance(next_instruction_raw, str):
        raise MalformedModelOutputError("Model output 'next_instruction' must be a string.")
    next_instruction = next_instruction_raw.strip()

    observed = _validate_str_list(data.get("observed_elements", []), "observed_elements")
    missing = list(_validate_str_list(data.get("missing_elements", []), "missing_elements"))

    raw_cues = data.get("overlay_cues", [])
    if not isinstance(raw_cues, list):
        raise MalformedModelOutputError("Model output 'overlay_cues' must be a list.")
    overlay_cues: list[OverlayHint] = []
    for idx, cue_dict in enumerate(raw_cues):
        try:
            overlay_cues.append(OverlayHint.from_dict(cue_dict))
        except TaskValidationError as exc:
            raise MalformedModelOutputError(
                f"Invalid overlay_cues[{idx}] in model output: {exc}"
            ) from exc

    # Conservative evidence checks: never claim COMPLETE when evidence is unclear
    downgrade_reasons: list[str] = []
    if status == ProgressStatus.COMPLETE:
        if confidence < min_complete_confidence:
            downgrade_reasons.append(
                f"confidence {confidence:.2f} is below required threshold "
                f"{min_complete_confidence:.2f}"
            )

        if missing:
            downgrade_reasons.append(f"missing elements reported ({', '.join(missing)})")

        unconfirmed = _find_unconfirmed_required_elements(
            step.expected_outcome.required_elements,
            observed,
        )
        if unconfirmed:
            for item in unconfirmed:
                if item not in missing:
                    missing.append(item)
            downgrade_reasons.append(
                f"required elements not confirmed in observed_elements ({', '.join(unconfirmed)})"
            )

        if _AMBIGUOUS_EVIDENCE_REGEX.search(reasoning):
            downgrade_reasons.append("reasoning indicates unclear or ambiguous visual evidence")

        if downgrade_reasons:
            status = ProgressStatus.UNCERTAIN
            reason_summary = "; ".join(downgrade_reasons)
            reasoning = f"{reasoning} [Downgraded to uncertain: {reason_summary}]"
            logger.info(
                "Downgraded step '%s' verification from complete to uncertain: %s",
                step.step_id,
                reason_summary,
            )

    return ProgressResult(
        task_id=task.task_id,
        step_id=step.step_id,
        status=status,
        confidence=confidence,
        reasoning=reasoning,
        next_instruction=next_instruction,
        observed_elements=observed,
        missing_elements=tuple(missing),
        overlay_cues=tuple(overlay_cues),
    )


def parse_instruction_output(
    raw_content: str,
    *,
    task: TaskDefinition,
    step: TaskStep,
) -> GuidanceInstruction:
    """Parse and validate LLM step instruction output into a GuidanceInstruction."""
    data = extract_json_object(raw_content)

    instruction_text = data.get("instruction_text")
    if not isinstance(instruction_text, str) or not instruction_text.strip():
        raise MalformedModelOutputError(
            "Model output 'instruction_text' must be a non-empty string."
        )

    summary = data.get("summary", "")
    if not isinstance(summary, str):
        raise MalformedModelOutputError("Model output 'summary' must be a string.")

    raw_cues = data.get("overlay_cues", [])
    if not isinstance(raw_cues, list):
        raise MalformedModelOutputError("Model output 'overlay_cues' must be a list.")

    cues: list[OverlayHint] = []
    for idx, cue_dict in enumerate(raw_cues):
        try:
            cues.append(OverlayHint.from_dict(cue_dict))
        except TaskValidationError as exc:
            raise MalformedModelOutputError(
                f"Invalid overlay_cues[{idx}] in instruction output: {exc}"
            ) from exc

    return GuidanceInstruction(
        task_id=task.task_id,
        step_id=step.step_id,
        instruction_text=instruction_text.strip(),
        summary=summary.strip(),
        overlay_cues=tuple(cues),
    )


class ProgressVerifier:
    """Coordinates screen capture, user consent, LLM analysis, and progress verification."""

    def __init__(
        self,
        adapter: OpenAICompatibleAdapter,
        *,
        capturer: ScreenCapturer | None = None,
        min_complete_confidence: float = DEFAULT_MIN_COMPLETE_CONFIDENCE,
    ) -> None:
        if (
            isinstance(min_complete_confidence, bool)
            or not isinstance(min_complete_confidence, (int, float))
            or min_complete_confidence <= 0.0
            or min_complete_confidence > 1.0
        ):
            raise ValueError("min_complete_confidence must be a float in (0.0, 1.0].")
        self._adapter = adapter
        self._capturer = capturer
        self._min_complete_confidence = float(min_complete_confidence)

    @property
    def min_complete_confidence(self) -> float:
        """Minimum confidence required to report ProgressStatus.COMPLETE."""
        return self._min_complete_confidence

    def verify_step_with_image(
        self,
        task: TaskDefinition,
        step: TaskStep,
        image: CapturedImage,
        *,
        consent_decision: ConsentDecision | None = None,
    ) -> ProgressResult:
        """Verify step progress using an already-captured image after checking user consent."""
        user_prompt = build_verification_prompt(task, step)
        response = self._adapter.complete_with_image(
            system_prompt=VERIFICATION_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            image=image,
            purpose=f"Verify progress for step '{step.title}'",
            task_title=task.title,
            step_title=step.title,
            consent_decision=consent_decision,
            json_mode=True,
        )
        return parse_verification_output(
            response.content,
            task=task,
            step=step,
            min_complete_confidence=self._min_complete_confidence,
        )

    def capture_and_verify_step(
        self,
        task: TaskDefinition,
        step: TaskStep,
        *,
        region: CaptureRegion | None = None,
        user_initiated: bool = False,
    ) -> ProgressResult:
        """Capture the screen/region from an explicit user action, obtain consent, and verify."""
        if self._capturer is None:
            raise RuntimeError("No ScreenCapturer configured on ProgressVerifier.")

        target_region = region if region is not None else step.expected_outcome.target_region
        if target_region is not None:
            image = self._capturer.capture_region(
                target_region,
                user_initiated=user_initiated,
            )
        else:
            image = self._capturer.capture_full_screen(
                user_initiated=user_initiated,
            )

        return self.verify_step_with_image(task, step, image)

    def generate_step_instruction(
        self,
        task: TaskDefinition,
        step: TaskStep,
    ) -> GuidanceInstruction:
        """Generate natural-language step guidance using the LLM adapter."""
        user_prompt = build_instruction_prompt(task, step)
        response = self._adapter.complete_text(
            system_prompt=INSTRUCTION_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            json_mode=True,
        )
        return parse_instruction_output(
            response.content,
            task=task,
            step=step,
        )
