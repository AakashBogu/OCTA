"""Unit and Qt tests for Phase 10.2 — Interactive Step & Tutorial Builder UI."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from PySide6.QtCore import QProcess, Qt
from pytestqt.qtbot import QtBot

from octa.communication.server import WebSocketServer
from octa.desktop.controller import DesktopController
from octa.desktop.tutorial_authoring_dialog import TutorialAuthoringDialog
from octa.desktop.window import DiagnosticWindow
from octa.intelligence.authoring_overlay import AuthoringCueDialog
from octa.intelligence.capture import CaptureRegion
from octa.intelligence.overlay import (
    HighlightSpec,
)
from octa.intelligence.tasks import (
    ExpectedOutcome,
    OverlayElementType,
    OverlayHint,
    TaskDefinition,
    TaskStep,
    TaskStepDefinition,
    TaskValidationError,
    load_task_definition_from_file,
    save_task_definition_to_file,
)
from octa.process.manager import GodotProcessManager
from octa.tutorials.catalog import BUILTIN_TASK_IDS


def test_task_step_definition_type_alias() -> None:
    """TaskStepDefinition is an alias for TaskStep."""
    assert TaskStepDefinition is TaskStep


def test_save_and_load_task_definition_roundtrip(tmp_path: Path) -> None:
    """Task definition saves to JSON with atomic safety and loads back losslessly."""
    task = TaskDefinition(
        task_id="unit-test-task",
        version="1.0.0",
        title="Unit Test Task",
        application="TestApp",
        goal="Demonstrate step saving and loading",
        steps=(
            TaskStep(
                step_id="step-1",
                order=1,
                title="First Step",
                instruction="Open the sample application and click Connect.",
                hints=("Look at the top left menu.", "Click on Connect."),
                expected_outcome=ExpectedOutcome(
                    description="Connection dialog appears",
                    required_elements=("ConnectionDialog",),
                    verification_hint="Verify dialog presence",
                ),
                overlay_cues=(
                    OverlayHint(
                        kind=OverlayElementType.HIGHLIGHT,
                        label="Connect Button",
                        color="#00E5FF",
                        bounds=CaptureRegion(x=100, y=200, width=80, height=40),
                    ),
                    OverlayHint(
                        kind=OverlayElementType.ARROW,
                        label="Click Here",
                        start_x=140,
                        start_y=320,
                        end_x=140,
                        end_y=240,
                    ),
                ),
            ),
            TaskStep(
                step_id="step-2",
                order=2,
                title="Second Step",
                instruction="Enter server details and press OK.",
                hints=("Default port is 8080.",),
                expected_outcome=ExpectedOutcome(
                    description="Status indicator turns green",
                ),
            ),
        ),
    )

    out_file = tmp_path / "custom_task.json"
    saved_path = save_task_definition_to_file(task, out_file)
    assert saved_path == out_file
    assert out_file.exists()

    # Verify JSON structure
    with out_file.open("r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["task_id"] == "unit-test-task"
    assert len(data["steps"]) == 2
    assert len(data["steps"][0]["overlay_cues"]) == 2
    assert data["steps"][0]["overlay_cues"][0]["kind"] == "highlight"
    assert data["steps"][0]["overlay_cues"][1]["kind"] == "arrow"

    # Load back
    loaded_task = load_task_definition_from_file(out_file)
    assert loaded_task.task_id == task.task_id
    assert loaded_task.title == task.title
    assert loaded_task.goal == task.goal
    assert len(loaded_task.steps) == 2
    assert loaded_task.steps[0].instruction == task.steps[0].instruction
    assert loaded_task.steps[0].hints == ("Look at the top left menu.", "Click on Connect.")
    assert len(loaded_task.steps[0].overlay_cues) == 2
    assert loaded_task.steps[0].overlay_cues[0].kind == OverlayElementType.HIGHLIGHT
    assert loaded_task.steps[0].overlay_cues[1].kind == OverlayElementType.ARROW


def test_save_task_definition_atomic_preserves_on_failure(tmp_path: Path) -> None:
    """Atomic saving does not truncate or overwrite an existing file if validation fails."""
    target_file = tmp_path / "existing_task.json"
    original_content = '{"task_id": "preserved-content"}'
    target_file.write_text(original_content, encoding="utf-8")

    # Construct invalid task definition (empty steps tuple bypasses frozen check)
    with pytest.raises(TaskValidationError):
        TaskDefinition(
            task_id="bad-task",
            version="1.0.0",
            title="Bad Task",
            application="App",
            goal="Goal",
            steps=(),
        )

    # Bypass dataclass __post_init__ to force validation failure inside save_task_definition_to_file
    bad_obj = object.__new__(TaskDefinition)
    object.__setattr__(bad_obj, "task_id", "")

    with pytest.raises(TaskValidationError):
        save_task_definition_to_file(bad_obj, target_file)

    # Confirm original file remains untouched
    assert target_file.read_text(encoding="utf-8") == original_content


def test_save_task_definition_rejects_overwrite_when_false(tmp_path: Path) -> None:
    """FileExistsError is raised when overwrite=False and target exists."""
    target_file = tmp_path / "readonly_task.json"
    target_file.write_text("{}", encoding="utf-8")

    task = TaskDefinition(
        task_id="task-overwrite-test",
        version="1.0.0",
        title="Title",
        application="App",
        goal="Goal",
        steps=(
            TaskStep(
                step_id="s1",
                order=1,
                title="S1",
                instruction="Do it.",
                expected_outcome=ExpectedOutcome(description="Done"),
            ),
        ),
    )

    with pytest.raises(FileExistsError):
        save_task_definition_to_file(task, target_file, overwrite=False)


def test_load_task_definition_errors(tmp_path: Path) -> None:
    """load_task_definition_from_file handles missing, malformed, and invalid JSON."""
    missing_file = tmp_path / "nonexistent.json"
    with pytest.raises(FileNotFoundError):
        load_task_definition_from_file(missing_file)

    malformed_file = tmp_path / "corrupt.json"
    malformed_file.write_text("{not json", encoding="utf-8")
    with pytest.raises(TaskValidationError, match="Malformed task JSON"):
        load_task_definition_from_file(malformed_file)

    invalid_root_file = tmp_path / "array.json"
    invalid_root_file.write_text("[]", encoding="utf-8")
    with pytest.raises(TaskValidationError, match="TaskDefinition must be a dictionary"):
        load_task_definition_from_file(invalid_root_file)

    schema_invalid_file = tmp_path / "missing_fields.json"
    schema_invalid_file.write_text(
        json.dumps(
            {
                "task_id": "test",
                "version": "1.0",
                "title": "T",
                "application": "A",
                "goal": "G",
                "steps": [],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(TaskValidationError):
        load_task_definition_from_file(schema_invalid_file)


def test_tutorial_authoring_dialog_initial_state(qtbot: QtBot) -> None:
    """TutorialAuthoringDialog initializes with 1 step and valid defaults."""
    dlg = TutorialAuthoringDialog()
    qtbot.addWidget(dlg)

    assert dlg._txt_task_id.text() == "new-tutorial-task"
    assert dlg._txt_version.text() == "1.0"
    assert dlg._txt_title.text() == "New Tutorial"
    assert dlg._list_steps.count() == 1
    assert dlg._active_step_index == 0

    # Delete should be disabled because only 1 step exists
    assert dlg._btn_del_step.isEnabled() is False
    assert dlg._btn_move_up.isEnabled() is False
    assert dlg._btn_move_down.isEnabled() is False


def test_tutorial_authoring_dialog_step_crud(qtbot: QtBot) -> None:
    """Dialog supports adding, duplicating, deleting, and reordering steps."""
    dlg = TutorialAuthoringDialog()
    qtbot.addWidget(dlg)

    # Add Step
    dlg._on_add_step_clicked()
    assert dlg._list_steps.count() == 2
    assert dlg._btn_del_step.isEnabled() is True

    dlg._txt_step_title.setText("Second Step Modified")
    dlg._txt_step_instruction.setPlainText("Instruction 2")
    dlg._save_inspector_to_active_step()

    # Duplicate Step
    dlg._on_duplicate_step_clicked()
    assert dlg._list_steps.count() == 3
    assert dlg._active_step_index == 2
    assert "Copy" in dlg._txt_step_title.text()
    assert dlg._txt_step_instruction.toPlainText() == "Instruction 2"

    # Reordering
    dlg._on_move_up_clicked()
    assert dlg._active_step_index == 1

    dlg._on_move_down_clicked()
    assert dlg._active_step_index == 2

    # Delete Step
    dlg._on_delete_step_clicked()
    assert dlg._list_steps.count() == 2

    # Delete second step
    dlg._on_delete_step_clicked()
    assert dlg._list_steps.count() == 1
    assert dlg._btn_del_step.isEnabled() is False


def test_tutorial_authoring_dialog_validation_builtin_collision(qtbot: QtBot) -> None:
    """Validation prevents using built-in tutorial IDs to avoid shadowing."""
    dlg = TutorialAuthoringDialog()
    qtbot.addWidget(dlg)

    for builtin_id in BUILTIN_TASK_IDS:
        dlg._txt_task_id.setText(builtin_id)
        valid, msg = dlg.validate_current()
        assert valid is False
        assert "collides with a reserved built-in" in msg


def test_tutorial_authoring_dialog_validation_empty_fields(qtbot: QtBot) -> None:
    """Validation rejects empty task IDs, titles, goals, or missing instructions."""
    dlg = TutorialAuthoringDialog()
    qtbot.addWidget(dlg)

    # Empty task ID
    dlg._txt_task_id.setText("")
    valid, msg = dlg.validate_current()
    assert valid is False
    assert "task id cannot be empty" in msg.lower()

    # Empty step instruction
    dlg._txt_task_id.setText("valid-id")
    dlg._txt_step_instruction.setPlainText("")
    dlg._save_inspector_to_active_step()
    valid, msg = dlg.validate_current()
    assert valid is False
    assert "instruction" in msg.lower()


def test_tutorial_authoring_dialog_save_and_open_roundtrip(tmp_path: Path, qtbot: QtBot) -> None:
    """Dialog saves valid task to file and reloads all metadata and steps."""
    dlg = TutorialAuthoringDialog()
    qtbot.addWidget(dlg)

    dlg._txt_task_id.setText("dialog-save-test")
    dlg._txt_version.setText("2.1.0")
    dlg._txt_title.setText("Save Dialog Test")
    dlg._txt_application.setText("TextEditor")
    dlg._txt_goal.setText("Validate end-to-end dialog persistence")
    dlg._txt_step_title.setText("First Action")
    dlg._txt_step_instruction.setPlainText("Press File -> Open.")
    dlg._txt_step_hints.setPlainText("Hint A\nHint B")
    dlg._txt_outcome_desc.setText("File browser opened")
    dlg._save_inspector_to_active_step()

    save_path = tmp_path / "persisted_tutorial.json"
    ok = dlg.save_file(save_path)
    assert ok is True
    assert save_path.exists()
    assert dlg._current_file_path == save_path

    # Fresh dialog
    dlg2 = TutorialAuthoringDialog()
    qtbot.addWidget(dlg2)
    load_ok = dlg2.open_file(save_path)
    assert load_ok is True

    assert dlg2._txt_task_id.text() == "dialog-save-test"
    assert dlg2._txt_version.text() == "2.1.0"
    assert dlg2._txt_title.text() == "Save Dialog Test"
    assert dlg2._txt_application.text() == "TextEditor"
    assert dlg2._txt_goal.text() == "Validate end-to-end dialog persistence"
    assert dlg2._txt_step_title.text() == "First Action"
    assert dlg2._txt_step_instruction.toPlainText() == "Press File -> Open."
    assert dlg2._txt_step_hints.toPlainText() == "Hint A\nHint B"
    assert dlg2._txt_outcome_desc.text() == "File browser opened"


def test_tutorial_authoring_dialog_cue_list_management(qtbot: QtBot) -> None:
    """Adding, previewing, and removing visual cues works accurately."""
    dlg = TutorialAuthoringDialog()
    qtbot.addWidget(dlg)

    # Manually append cues to active step
    step_dict = dlg._steps[dlg._active_step_index]
    step_dict["overlay_cues"] = [
        OverlayHint(
            kind=OverlayElementType.HIGHLIGHT,
            label="Target Area",
            color="#FF5252",
            bounds=CaptureRegion(x=50, y=50, width=200, height=100),
        ),
        OverlayHint(
            kind=OverlayElementType.ARROW,
            label="Click Target",
            start_x=100,
            start_y=200,
            end_x=100,
            end_y=150,
        ),
    ]
    dlg._load_active_step_into_inspector()

    assert dlg._list_cues.count() == 2
    assert "Highlight" in dlg._list_cues.item(0).text()
    assert "Arrow" in dlg._list_cues.item(1).text()

    # Remove second cue
    dlg._list_cues.setCurrentRow(1)
    dlg._on_remove_cue_clicked()
    assert dlg._list_cues.count() == 1
    assert len(step_dict["overlay_cues"]) == 1

    # Remove remaining cue
    dlg._list_cues.setCurrentRow(0)
    dlg._on_remove_cue_clicked()
    assert dlg._list_cues.count() == 0
    assert len(step_dict["overlay_cues"]) == 0


def test_tutorial_authoring_dialog_cue_selector_integration(qtbot: QtBot) -> None:
    """'Select on Screen...' opens AuthoringCueDialog and adds accepted cue."""
    dlg = TutorialAuthoringDialog()
    qtbot.addWidget(dlg)

    mock_highlight = HighlightSpec(
        x=10,
        y=20,
        width=30,
        height=40,
        label="Screen Cue",
        color="#00E5FF",
    )

    with (
        patch.object(
            AuthoringCueDialog,
            "exec",
            return_value=1,
        ),
        patch.object(
            AuthoringCueDialog,
            "created_highlight",
            new=mock_highlight,
        ),
        patch.object(
            AuthoringCueDialog,
            "created_arrow",
            new=None,
        ),
    ):
        dlg._on_select_cue_on_screen_clicked()

    step_dict = dlg._steps[dlg._active_step_index]
    assert len(step_dict["overlay_cues"]) == 1
    assert dlg._list_cues.count() == 1
    assert "Screen Cue" in dlg._list_cues.item(0).text()


def test_tutorial_authoring_dialog_preview_cues_safely(qtbot: QtBot) -> None:
    """Preview cues triggers local GuidanceOverlayWidget without capturing or crashing."""
    dlg = TutorialAuthoringDialog()
    qtbot.addWidget(dlg)

    step_dict = dlg._steps[dlg._active_step_index]
    step_dict["overlay_cues"] = [
        OverlayHint(
            kind=OverlayElementType.HIGHLIGHT,
            label="Preview Test",
            bounds=CaptureRegion(x=10, y=10, width=100, height=50),
        )
    ]
    dlg._load_active_step_into_inspector()

    dlg._on_preview_cues_clicked()
    assert dlg._preview_overlay is not None
    assert dlg._preview_overlay.isVisible() is True
    dlg._preview_overlay.close()


def test_diagnostic_window_tutorial_builder_integration(qtbot: QtBot) -> None:
    """DiagnosticWindow features a 'Tutorial Builder...' button wired to open dialog."""
    mock_process = QProcess()
    process_mgr = GodotProcessManager(process=mock_process)
    server = WebSocketServer(port=0)
    controller = DesktopController(process_manager=process_mgr, server=server)

    window = DiagnosticWindow(controller, tray_enabled=False)
    qtbot.addWidget(window)

    assert hasattr(window, "_btn_tutorial_builder")
    assert window._btn_tutorial_builder.text() == "Tutorial &Builder..."
    assert window._btn_tutorial_builder.accessibleName() == "Tutorial Builder"
    assert window._btn_tutorial_builder.focusPolicy() == Qt.FocusPolicy.StrongFocus

    with patch("octa.desktop.tutorial_authoring_dialog.TutorialAuthoringDialog.exec") as mock_exec:
        window._btn_tutorial_builder.click()
        assert mock_exec.called
