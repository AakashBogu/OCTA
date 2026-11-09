"""Unit tests for structured progress verification and conservative evidence policy."""

from __future__ import annotations

import json
import secrets
from typing import Any

import pytest
from PySide6.QtGui import QColor, QImage

from octa.intelligence.capture import (
    CaptureRegion,
    ScreenCapturer,
    encode_qimage_to_png_bytes,
)
from octa.intelligence.consent import ConsentManager
from octa.intelligence.exceptions import (
    ConsentDeclinedError,
    MalformedModelOutputError,
    UserActionRequiredError,
)
from octa.intelligence.llm import LLMConfig, OpenAICompatibleAdapter
from octa.intelligence.tasks import (
    ExpectedOutcome,
    ProgressStatus,
    TaskDefinition,
    TaskStep,
)
from octa.intelligence.verifier import (
    ProgressVerifier,
    parse_instruction_output,
    parse_verification_output,
)


def _sample_task() -> tuple[TaskDefinition, TaskStep]:
    step = TaskStep(
        step_id="step-1",
        order=1,
        title="Open File Menu",
        instruction="Click File in the top-left menu bar.",
        expected_outcome=ExpectedOutcome(
            description="File dropdown is visible with Save and Save As options.",
            required_elements=("Save", "Save As"),
            target_region=CaptureRegion(x=0, y=0, width=300, height=200),
        ),
        hints=("Use Alt+F if needed.",),
    )
    task = TaskDefinition(
        task_id="task-notepad",
        title="Save File in Notepad",
        application="Notepad",
        goal="Save a document.",
        steps=(step,),
    )
    return task, step


def _wrap_chat_completion(model_json: dict[str, Any]) -> bytes:
    envelope = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": json.dumps(model_json),
                },
                "finish_reason": "stop",
            }
        ]
    }
    return json.dumps(envelope).encode("utf-8")


def test_parse_verification_complete_incomplete_and_uncertain() -> None:
    """Verifier accurately parses clear complete, incomplete, and uncertain outcomes."""
    task, step = _sample_task()

    # 1. Clear COMPLETE
    res_complete = parse_verification_output(
        json.dumps(
            {
                "status": "complete",
                "confidence": 0.92,
                "reasoning": (
                    "The File menu dropdown is open and both Save and Save As are visible."
                ),
                "observed_elements": ["File menu", "Save", "Save As"],
                "missing_elements": [],
                "next_instruction": "Proceed to click Save.",
                "overlay_cues": [
                    {
                        "kind": "highlight",
                        "label": "Save",
                        "bounds": {"x": 12, "y": 60, "width": 80, "height": 22},
                    }
                ],
            }
        ),
        task=task,
        step=step,
    )
    assert res_complete.status == ProgressStatus.COMPLETE
    assert res_complete.is_complete is True
    assert len(res_complete.overlay_cues) == 1

    # 2. INCOMPLETE
    res_incomplete = parse_verification_output(
        json.dumps(
            {
                "status": "incomplete",
                "confidence": 0.89,
                "reasoning": "The Notepad window is open but the File menu has not been clicked.",
                "observed_elements": ["Notepad window"],
                "missing_elements": ["Save", "Save As"],
                "next_instruction": "Click 'File' in the top-left corner.",
            }
        ),
        task=task,
        step=step,
    )
    assert res_incomplete.status == ProgressStatus.INCOMPLETE
    assert res_incomplete.is_complete is False

    # 3. Direct UNCERTAIN
    res_uncertain = parse_verification_output(
        json.dumps(
            {
                "status": "uncertain",
                "confidence": 0.40,
                "reasoning": "Another window is covering the top-left corner of Notepad.",
                "observed_elements": [],
                "missing_elements": ["Save", "Save As"],
            }
        ),
        task=task,
        step=step,
    )
    assert res_uncertain.status == ProgressStatus.UNCERTAIN
    assert res_uncertain.is_complete is False


def test_conservative_downgrade_when_evidence_is_unclear() -> None:
    """Verifier never claims 'complete' when evidence or confidence is unclear."""
    task, step = _sample_task()

    # Case A: Model claims complete, but confidence is below 0.75 threshold
    low_conf = parse_verification_output(
        json.dumps(
            {
                "status": "complete",
                "confidence": 0.60,
                "reasoning": "Menu appears open.",
                "observed_elements": ["Save", "Save As"],
                "missing_elements": [],
            }
        ),
        task=task,
        step=step,
    )
    assert low_conf.status == ProgressStatus.UNCERTAIN
    assert low_conf.is_complete is False
    assert "Downgraded to uncertain" in low_conf.reasoning

    # Case B: Model claims complete, but required element 'Save As' is not in observed_elements
    missing_req = parse_verification_output(
        json.dumps(
            {
                "status": "complete",
                "confidence": 0.95,
                "reasoning": "Saw Save option.",
                "observed_elements": ["Save"],
                "missing_elements": [],
            }
        ),
        task=task,
        step=step,
    )
    assert missing_req.status == ProgressStatus.UNCERTAIN
    assert "Save As" in missing_req.missing_elements

    # Case C: Model claims complete, but explicitly lists a missing element
    contradictory = parse_verification_output(
        json.dumps(
            {
                "status": "complete",
                "confidence": 0.90,
                "reasoning": "Menu open.",
                "observed_elements": ["Save", "Save As"],
                "missing_elements": ["Save As"],
            }
        ),
        task=task,
        step=step,
    )
    assert contradictory.status == ProgressStatus.UNCERTAIN

    # Case D: Model claims complete, but reasoning expresses ambiguity ("not sure" / "blurry")
    ambiguous = parse_verification_output(
        json.dumps(
            {
                "status": "complete",
                "confidence": 0.85,
                "reasoning": "The screenshot is blurry so I am not sure if Save As is enabled.",
                "observed_elements": ["Save", "Save As"],
                "missing_elements": [],
            }
        ),
        task=task,
        step=step,
    )
    assert ambiguous.status == ProgressStatus.UNCERTAIN
    assert "ambiguous" in ambiguous.reasoning


def test_capture_and_verify_step_end_to_end_and_cancellation() -> None:
    """ProgressVerifier coordinates user-initiated capture, consent check, and LLM verification."""
    task, step = _sample_task()
    runtime_credential = secrets.token_urlsafe(16)

    img = QImage(300, 200, QImage.Format.Format_ARGB32)
    img.fill(QColor(25, 50, 75))
    png_bytes = encode_qimage_to_png_bytes(img)

    capturer = ScreenCapturer(
        grabber=lambda r: (
            png_bytes,
            300,
            200,
            r or CaptureRegion(x=0, y=0, width=300, height=200),
        )
    )

    transport_calls = 0

    def mock_transport(_req: object, _timeout: float) -> bytes:
        nonlocal transport_calls
        transport_calls += 1
        return _wrap_chat_completion(
            {
                "status": "complete",
                "confidence": 0.91,
                "reasoning": "File menu with Save and Save As is clearly open.",
                "observed_elements": ["Save", "Save As"],
                "missing_elements": [],
            }
        )

    # 1. User cancels consent -> raises ConsentDeclinedError, 0 transport calls
    cancel_adapter = OpenAICompatibleAdapter(
        LLMConfig(api_key=runtime_credential),
        transport=mock_transport,
        consent_manager=ConsentManager(handler=lambda _req: False),
    )
    verifier_cancel = ProgressVerifier(cancel_adapter, capturer=capturer)

    with pytest.raises(UserActionRequiredError):
        verifier_cancel.capture_and_verify_step(task, step, user_initiated=False)

    with pytest.raises(ConsentDeclinedError):
        verifier_cancel.capture_and_verify_step(task, step, user_initiated=True)
    assert transport_calls == 0

    # 2. User approves consent -> executes transport and returns ProgressResult
    approve_adapter = OpenAICompatibleAdapter(
        LLMConfig(api_key=runtime_credential),
        transport=mock_transport,
        consent_manager=ConsentManager(handler=lambda _req: True),
    )
    verifier_ok = ProgressVerifier(approve_adapter, capturer=capturer)
    result = verifier_ok.capture_and_verify_step(task, step, user_initiated=True)

    assert transport_calls == 1
    assert result.status == ProgressStatus.COMPLETE
    assert result.confidence == 0.91


def test_generate_step_instruction_and_malformed_output_rejection() -> None:
    """ProgressVerifier generates structured step instructions and rejects malformed JSON."""
    task, step = _sample_task()
    runtime_credential = secrets.token_urlsafe(16)

    adapter = OpenAICompatibleAdapter(
        LLMConfig(api_key=runtime_credential),
        transport=lambda _req, _t: _wrap_chat_completion(
            {
                "instruction_text": "Click the 'File' menu at the top-left of Notepad.",
                "summary": "Open File menu",
                "overlay_cues": [],
            }
        ),
    )
    verifier = ProgressVerifier(adapter)
    guidance = verifier.generate_step_instruction(task, step)
    assert guidance.task_id == task.task_id
    assert guidance.step_id == step.step_id
    assert "File" in guidance.instruction_text

    # Malformed verification output
    with pytest.raises(MalformedModelOutputError, match="status"):
        parse_verification_output(
            '{"status": "unknown_state", "confidence": 0.9, "reasoning": "bad"}',
            task=task,
            step=step,
        )

    # Malformed instruction output
    with pytest.raises(MalformedModelOutputError, match="instruction_text"):
        parse_instruction_output('{"summary": "missing instruction_text"}', task=task, step=step)


def test_verifier_validation_and_malformed_fields() -> None:
    """ProgressVerifier validates confidence and rejects malformed verification fields."""
    task, step = _sample_task()
    adapter = OpenAICompatibleAdapter(LLMConfig(api_key=None))

    with pytest.raises(ValueError, match="min_complete_confidence"):
        ProgressVerifier(adapter, min_complete_confidence=0.0)

    verifier_no_capturer = ProgressVerifier(adapter, capturer=None)
    with pytest.raises(RuntimeError, match="No ScreenCapturer configured"):
        verifier_no_capturer.capture_and_verify_step(task, step, user_initiated=True)

    # Boolean confidence rejected
    with pytest.raises(MalformedModelOutputError, match="confidence"):
        parse_verification_output(
            '{"status": "complete", "confidence": true, "reasoning": "ok"}',
            task=task,
            step=step,
        )

    # Non-list observed_elements rejected
    with pytest.raises(MalformedModelOutputError, match="observed_elements"):
        parse_verification_output(
            '{"status": "incomplete", "confidence": 0.5, "reasoning": "ok", '
            '"observed_elements": "not-a-list"}',
            task=task,
            step=step,
        )
