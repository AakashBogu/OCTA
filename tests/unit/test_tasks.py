"""Unit tests for task definitions, steps, expected outcomes, and progress results."""

from __future__ import annotations

import pytest

from octa.intelligence.capture import CaptureRegion
from octa.intelligence.exceptions import TaskValidationError
from octa.intelligence.tasks import (
    ExpectedOutcome,
    GuidanceInstruction,
    OverlayElementType,
    OverlayHint,
    ProgressResult,
    ProgressStatus,
    TaskDefinition,
)


def _sample_task_dict() -> dict[str, object]:
    return {
        "task_id": "notepad-save-file",
        "version": "1.0",
        "title": "Save a Text File in Notepad",
        "application": "Notepad",
        "goal": "Open the File menu and save a new document.",
        "steps": [
            {
                "step_id": "open-file-menu",
                "order": 1,
                "title": "Click File Menu",
                "instruction": "Click 'File' in the top-left menu bar of Notepad.",
                "expected_outcome": {
                    "description": "The File dropdown menu is open showing 'Save' and 'Save As'.",
                    "required_elements": ["Save", "Save As"],
                    "verification_hint": "Look for the open File menu below the title bar.",
                    "target_region": {"x": 0, "y": 0, "width": 400, "height": 300},
                },
                "hints": ["Press Alt+F if you prefer keyboard shortcuts."],
                "overlay_cues": [
                    {
                        "kind": "highlight",
                        "label": "File Menu",
                        "color": "#00E676",
                        "bounds": {"x": 8, "y": 32, "width": 48, "height": 24},
                    },
                    {
                        "kind": "arrow",
                        "label": "Click here",
                        "color": "#FFAB00",
                        "start_x": 120,
                        "start_y": 90,
                        "end_x": 32,
                        "end_y": 44,
                    },
                ],
            },
            {
                "step_id": "click-save",
                "order": 2,
                "title": "Select Save",
                "instruction": "Click 'Save' in the File menu.",
                "expected_outcome": {
                    "description": "The Save As dialog window is visible.",
                    "required_elements": ["Save As", "File name"],
                },
            },
        ],
    }


def test_task_definition_roundtrip_and_lookup() -> None:
    """TaskDefinition validates and roundtrips through dict and JSON cleanly."""
    task = TaskDefinition.from_dict(_sample_task_dict())
    assert task.task_id == "notepad-save-file"
    assert len(task.steps) == 2

    step_1 = task.get_step("open-file-menu")
    assert step_1.order == 1
    assert step_1.expected_outcome.required_elements == ("Save", "Save As")
    assert step_1.expected_outcome.target_region == CaptureRegion(x=0, y=0, width=400, height=300)
    assert len(step_1.overlay_cues) == 2
    assert step_1.overlay_cues[0].kind == OverlayElementType.HIGHLIGHT
    assert step_1.overlay_cues[1].kind == OverlayElementType.ARROW

    # Roundtrip via JSON
    json_str = task.to_json()
    reloaded = TaskDefinition.from_json(json_str)
    assert reloaded == task

    with pytest.raises(TaskValidationError, match="not found"):
        task.get_step("nonexistent-step")


def test_task_definition_rejects_invalid_structures() -> None:
    """TaskDefinition rejects empty steps, duplicate step IDs, and non-sequential orders."""
    base = _sample_task_dict()

    # Empty steps
    empty_steps = dict(base, steps=[])
    with pytest.raises(TaskValidationError, match="non-empty"):
        TaskDefinition.from_dict(empty_steps)

    # Duplicate step_id
    steps_list = list(_sample_task_dict()["steps"])  # type: ignore[arg-type]
    dup_step = dict(steps_list[1], step_id="open-file-menu")  # type: ignore[arg-type]
    with pytest.raises(TaskValidationError, match="Duplicate step_id"):
        TaskDefinition.from_dict(dict(base, steps=[steps_list[0], dup_step]))

    # Out-of-order step
    bad_order_step = dict(steps_list[1], order=5)  # type: ignore[arg-type]
    with pytest.raises(TaskValidationError, match="order must be sequential"):
        TaskDefinition.from_dict(dict(base, steps=[steps_list[0], bad_order_step]))

    # Extra unknown field
    with pytest.raises(TaskValidationError, match="Unexpected fields"):
        TaskDefinition.from_dict(dict(base, unexpected_field="bad"))


def test_overlay_hint_validation() -> None:
    """OverlayHint enforces distinct highlight bounds vs arrow endpoint requirements."""
    with pytest.raises(TaskValidationError, match="requires a valid 'bounds'"):
        OverlayHint(kind=OverlayElementType.HIGHLIGHT, bounds=None)

    with pytest.raises(TaskValidationError, match="must not define arrow coordinates"):
        OverlayHint(
            kind=OverlayElementType.HIGHLIGHT,
            bounds=CaptureRegion(x=0, y=0, width=10, height=10),
            start_x=1,
            start_y=2,
            end_x=3,
            end_y=4,
        )

    with pytest.raises(TaskValidationError, match="must not be identical"):
        OverlayHint(
            kind=OverlayElementType.ARROW,
            start_x=50,
            start_y=50,
            end_x=50,
            end_y=50,
        )


def test_guidance_instruction_validation() -> None:
    """GuidanceInstruction validates fields and serializes cleanly."""
    instr = GuidanceInstruction.from_dict(
        {
            "task_id": "t1",
            "step_id": "s1",
            "instruction_text": "Click the File menu in the top-left corner.",
            "summary": "Open File menu",
            "overlay_cues": [],
        }
    )
    assert instr.instruction_text == "Click the File menu in the top-left corner."
    assert GuidanceInstruction.from_dict(instr.to_dict()) == instr

    with pytest.raises(TaskValidationError, match="instruction_text"):
        GuidanceInstruction(task_id="t1", step_id="s1", instruction_text="   ")


def test_progress_result_validation_and_statuses() -> None:
    """ProgressResult supports complete, incomplete, and uncertain and rejects contradictions."""
    complete_res = ProgressResult(
        task_id="t1",
        step_id="s1",
        status=ProgressStatus.COMPLETE,
        confidence=0.92,
        reasoning="File menu dropdown with Save and Save As is clearly visible.",
        observed_elements=("Save", "Save As"),
    )
    assert complete_res.is_complete is True
    assert ProgressResult.from_dict(complete_res.to_dict()) == complete_res

    incomplete_res = ProgressResult(
        task_id="t1",
        step_id="s1",
        status=ProgressStatus.INCOMPLETE,
        confidence=0.88,
        reasoning="File menu is still closed.",
        missing_elements=("Save",),
    )
    assert incomplete_res.is_complete is False

    uncertain_res = ProgressResult(
        task_id="t1",
        step_id="s1",
        status=ProgressStatus.UNCERTAIN,
        confidence=0.45,
        reasoning="Window is partially covered by another dialog.",
    )
    assert uncertain_res.status == ProgressStatus.UNCERTAIN
    assert uncertain_res.is_complete is False

    # Contradiction: status='complete' while missing_elements is non-empty is rejected
    with pytest.raises(TaskValidationError, match="cannot claim 'complete'"):
        ProgressResult(
            task_id="t1",
            step_id="s1",
            status=ProgressStatus.COMPLETE,
            confidence=0.95,
            reasoning="Claims complete despite missing element.",
            missing_elements=("Save As",),
        )

    # Confidence out of bounds
    with pytest.raises(TaskValidationError, match="between 0.0 and 1.0"):
        ProgressResult(
            task_id="t1",
            step_id="s1",
            status=ProgressStatus.INCOMPLETE,
            confidence=1.2,
            reasoning="Invalid confidence.",
        )

    # Boolean rejected for confidence
    with pytest.raises(TaskValidationError, match="must be a number"):
        ProgressResult(
            task_id="t1",
            step_id="s1",
            status=ProgressStatus.INCOMPLETE,
            confidence=True,  # type: ignore[arg-type]
            reasoning="Bool confidence.",
        )


def test_task_json_and_expected_outcome_error_handling() -> None:
    """from_json and ExpectedOutcome reject malformed inputs."""
    with pytest.raises(TaskValidationError, match="Cannot parse empty task JSON"):
        TaskDefinition.from_json("   ")

    with pytest.raises(TaskValidationError, match="Malformed task JSON"):
        TaskDefinition.from_json("{invalid json")

    with pytest.raises(TaskValidationError, match="Invalid UTF-8"):
        TaskDefinition.from_json(b"\xff\xfe")

    with pytest.raises(TaskValidationError, match="Missing required field 'description'"):
        ExpectedOutcome.from_dict({"required_elements": ["Save"]})
