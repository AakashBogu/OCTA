"""Unit tests for the built-in tutorial catalog and loader utilities (Phase 6.0)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from octa.intelligence.exceptions import TaskValidationError
from octa.intelligence.overlay import OverlayBounds, overlay_hint_to_spec
from octa.intelligence.tasks import OverlayElementType
from octa.tutorials.catalog import (
    BUILTIN_TUTORIALS,
    build_explorer_new_folder_tutorial,
    build_notepad_save_tutorial,
    build_octa_welcome_tutorial,
    get_builtin_tutorials,
    get_tutorial_by_id,
    load_tutorial_from_json_file,
)

# Built-in overlay cues must fit even a small 1024x768 display.
MIN_SUPPORTED_SCREEN = OverlayBounds(x=0, y=0, width=1024, height=768)


def test_builtin_tutorials_are_grounded_and_schema_valid() -> None:
    tutorials = get_builtin_tutorials()
    assert tutorials == BUILTIN_TUTORIALS
    assert len(tutorials) == 3

    expected_ids = {
        "octa-welcome-task",
        "windows-notepad-save",
        "windows-explorer-new-folder",
    }
    assert {t.task_id for t in tutorials} == expected_ids

    for tutorial in tutorials:
        assert tutorial.title
        assert tutorial.application in {
            "OCTA Companion",
            "Windows Notepad",
            "Windows File Explorer",
        }
        assert len(tutorial.steps) >= 2
        for idx, step in enumerate(tutorial.steps, start=1):
            assert step.order == idx
            assert step.instruction
            assert step.expected_outcome.description
            assert len(step.hints) >= 1
            assert len(step.overlay_cues) >= 1
            for cue in step.overlay_cues:
                assert cue.kind in (OverlayElementType.HIGHLIGHT, OverlayElementType.ARROW)
                # Raises InvalidOverlayCoordinatesError if the cue exceeds the screen bounds
                overlay_hint_to_spec(cue, screen=MIN_SUPPORTED_SCREEN)


def test_individual_tutorial_builders_round_trip_dict() -> None:
    for builder in (
        build_octa_welcome_tutorial,
        build_notepad_save_tutorial,
        build_explorer_new_folder_tutorial,
    ):
        task = builder()
        serialized = task.to_dict()
        restored = type(task).from_dict(serialized)
        assert restored == task


def test_get_tutorial_by_id_returns_matching_task() -> None:
    notepad = get_tutorial_by_id("windows-notepad-save")
    assert notepad.title == "Create and Save a Note in Windows Notepad"
    assert notepad.application == "Windows Notepad"
    assert len(notepad.steps) == 3


def test_get_tutorial_by_id_rejects_unknown_or_empty_id() -> None:
    with pytest.raises(TaskValidationError, match="non-empty string"):
        get_tutorial_by_id("   ")

    with pytest.raises(TaskValidationError, match="Unknown tutorial task_id"):
        get_tutorial_by_id("nonexistent-tutorial-id")


def test_load_tutorial_from_json_file(tmp_path: Path) -> None:
    sample = build_explorer_new_folder_tutorial()
    json_path = tmp_path / "explorer_tutorial.json"
    json_path.write_text(json.dumps(sample.to_dict(), indent=2), encoding="utf-8")

    loaded = load_tutorial_from_json_file(json_path)
    assert loaded == sample


def test_load_tutorial_from_json_file_missing_raises(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.json"
    with pytest.raises(TaskValidationError, match="not found"):
        load_tutorial_from_json_file(missing)
