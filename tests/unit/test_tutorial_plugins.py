"""Unit and integration tests for Phase 8.1 External Tutorial Plugin System."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from octa.core.config import AppConfig
from octa.desktop.controller import DesktopController
from octa.desktop.window import DiagnosticWindow
from octa.intelligence.capture import CaptureRegion
from octa.intelligence.tasks import (
    ExpectedOutcome,
    OverlayElementType,
    OverlayHint,
    TaskDefinition,
    TaskStep,
)
from octa.tutorials.catalog import (
    BUILTIN_TASK_IDS,
    BUILTIN_TUTORIALS,
    TutorialCatalog,
)
from octa.tutorials.engine import TutorialEngine, TutorialSessionState
from octa.tutorials.plugins import (
    TUTORIAL_PLUGIN_MANIFEST_FILENAME,
    PluginManifestError,
    PluginSecurityError,
    TutorialPluginManifest,
    discover_tutorial_plugins,
    load_tutorial_plugin,
)


def _create_sample_task(
    task_id: str = "custom-plugin-task",
    title: str = "Custom Calculator Tutorial",
    app: str = "Windows Calculator",
) -> TaskDefinition:
    """Helper creating a valid minimal TaskDefinition for testing."""
    return TaskDefinition(
        task_id=task_id,
        version="1.0",
        title=title,
        application=app,
        goal="Test goal for plugin tutorial.",
        steps=(
            TaskStep(
                step_id="step-1",
                order=1,
                title="Open Calculator",
                instruction="Launch Windows Calculator and verify it is visible.",
                expected_outcome=ExpectedOutcome(
                    description="Calculator window is open.",
                    required_elements=("Calculator",),
                    verification_hint="Check for the Calculator window title.",
                    target_region=CaptureRegion(0, 0, 600, 400),
                ),
                hints=("Press Win+R and type calc.",),
                overlay_cues=(
                    OverlayHint(
                        kind=OverlayElementType.HIGHLIGHT,
                        label="Calculator Target",
                        color="#00E676",
                        bounds=CaptureRegion(20, 20, 200, 100),
                    ),
                ),
            ),
            TaskStep(
                step_id="step-2",
                order=2,
                title="Enter Number",
                instruction="Click or type 42 into the calculator.",
                expected_outcome=ExpectedOutcome(
                    description="Display shows 42.",
                    required_elements=("Display",),
                    verification_hint="Confirm number display shows 42.",
                    target_region=CaptureRegion(0, 0, 600, 400),
                ),
                hints=("Click the numeric buttons.",),
                overlay_cues=(
                    OverlayHint(
                        kind=OverlayElementType.ARROW,
                        label="Numeric Keypad",
                        color="#FFAB00",
                        start_x=100,
                        start_y=200,
                        end_x=100,
                        end_y=150,
                    ),
                ),
            ),
        ),
    )


# ══════════════════════════════════════════════
# Manifest Parsing & Validation Tests
# ══════════════════════════════════════════════


def test_manifest_valid_parsing() -> None:
    data = {
        "schema_version": "1.0",
        "id": "calculator-pack",
        "name": "Windows Calculator Guide",
        "version": "1.2.0",
        "author": "Community Contributor",
        "description": "Guides users through calculations.",
        "tutorial_files": ["calc_tutorial.json"],
    }
    manifest = TutorialPluginManifest.from_dict(data)
    assert manifest.schema_version == "1.0"
    assert manifest.plugin_id == "calculator-pack"
    assert manifest.name == "Windows Calculator Guide"
    assert manifest.version == "1.2.0"
    assert manifest.author == "Community Contributor"
    assert manifest.description == "Guides users through calculations."
    assert manifest.tutorial_files == ("calc_tutorial.json",)


def test_manifest_single_tutorial_file_shorthand() -> None:
    data = {
        "schema_version": "1.0",
        "id": "calc-pack",
        "name": "Calc",
        "version": "1.0",
        "tutorial_file": "tutorials/calc.json",
    }
    manifest = TutorialPluginManifest.from_dict(data)
    assert manifest.tutorial_files == ("tutorials/calc.json",)


def test_manifest_unsupported_schema_version() -> None:
    data = {
        "schema_version": "2.0",
        "id": "future-pack",
        "name": "Future",
        "version": "1.0",
        "tutorial_file": "tut.json",
    }
    with pytest.raises(PluginManifestError, match="Unsupported manifest schema_version"):
        TutorialPluginManifest.from_dict(data)


@pytest.mark.parametrize(
    "invalid_id",
    [
        "",
        "   ",
        "has spaces",
        "has/slash",
        "has$symbols",
        "a" * 65,  # Exceeds max length
    ],
)
def test_manifest_invalid_plugin_id(invalid_id: str) -> None:
    data = {
        "schema_version": "1.0",
        "id": invalid_id,
        "name": "Test",
        "version": "1.0",
        "tutorial_file": "tut.json",
    }
    with pytest.raises(PluginManifestError, match="Plugin 'id' must match"):
        TutorialPluginManifest.from_dict(data)


@pytest.mark.parametrize("invalid_version", ["", "   ", "not-a-semver", "v", "1.a.b"])
def test_manifest_invalid_version(invalid_version: str) -> None:
    data = {
        "schema_version": "1.0",
        "id": "valid-id",
        "name": "Test",
        "version": invalid_version,
        "tutorial_file": "tut.json",
    }
    with pytest.raises(PluginManifestError, match="SemVer-compatible string"):
        TutorialPluginManifest.from_dict(data)


def test_manifest_extra_keys_rejected() -> None:
    data = {
        "schema_version": "1.0",
        "id": "valid-id",
        "name": "Test",
        "version": "1.0",
        "tutorial_file": "tut.json",
        "malicious_executable": "calc.exe",
    }
    with pytest.raises(PluginManifestError, match="Unexpected fields in plugin manifest"):
        TutorialPluginManifest.from_dict(data)


def test_manifest_missing_files_rejected() -> None:
    data = {
        "schema_version": "1.0",
        "id": "valid-id",
        "name": "Test",
        "version": "1.0",
    }
    with pytest.raises(
        PluginManifestError, match="must specify 'tutorial_file' or 'tutorial_files'"
    ):
        TutorialPluginManifest.from_dict(data)


# ══════════════════════════════════════════════
# Path Security & Escape Tests
# ══════════════════════════════════════════════


def test_manifest_rejects_absolute_paths() -> None:
    data = {
        "schema_version": "1.0",
        "id": "escape-pack",
        "name": "Escape",
        "version": "1.0",
        "tutorial_file": "C:/Windows/win.ini" if os.name == "nt" else "/etc/passwd",
    }
    with pytest.raises(PluginSecurityError, match="Absolute paths are forbidden"):
        TutorialPluginManifest.from_dict(data)


def test_manifest_rejects_path_traversal() -> None:
    data = {
        "schema_version": "1.0",
        "id": "escape-pack",
        "name": "Escape",
        "version": "1.0",
        "tutorial_files": ["../../secret.json"],
    }
    with pytest.raises(PluginSecurityError, match="Path traversal"):
        TutorialPluginManifest.from_dict(data)


def test_symlink_escape_rejected_at_load_time(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "symlink_plugin"
    plugin_dir.mkdir()

    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    secret_file = outside_dir / "secret_tutorial.json"
    secret_file.write_text(json.dumps(_create_sample_task().to_dict()), encoding="utf-8")

    link_path = plugin_dir / "linked_tut.json"
    try:
        link_path.symlink_to(secret_file)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation not permitted in this test environment.")

    manifest_data = {
        "schema_version": "1.0",
        "id": "symlink-pack",
        "name": "Symlink Pack",
        "version": "1.0",
        "tutorial_file": "linked_tut.json",
    }
    (plugin_dir / TUTORIAL_PLUGIN_MANIFEST_FILENAME).write_text(
        json.dumps(manifest_data), encoding="utf-8"
    )

    loaded, diags = load_tutorial_plugin(plugin_dir)
    assert loaded is None
    assert any(d.error_code == "PATH_TRAVERSAL_ATTEMPT" for d in diags)


# ══════════════════════════════════════════════
# Plugin Loading & Discovery Tests
# ══════════════════════════════════════════════


def test_load_single_valid_plugin(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "my_plugin"
    plugin_dir.mkdir()

    task = _create_sample_task(task_id="calc-guide", title="Calc Guide")
    tut_file = plugin_dir / "calc.json"
    tut_file.write_text(task.to_json(), encoding="utf-8")

    manifest = {
        "schema_version": "1.0",
        "id": "calc-pack",
        "name": "Calculator Guide Pack",
        "version": "1.0.0",
        "author": "Tester",
        "description": "Calculator guide pack description",
        "tutorial_file": "calc.json",
    }
    (plugin_dir / TUTORIAL_PLUGIN_MANIFEST_FILENAME).write_text(
        json.dumps(manifest), encoding="utf-8"
    )

    loaded, diags = load_tutorial_plugin(plugin_dir)
    assert loaded is not None
    assert len(diags) == 0
    assert loaded.manifest.plugin_id == "calc-pack"
    assert loaded.manifest.name == "Calculator Guide Pack"
    assert len(loaded.tutorials) == 1
    assert loaded.tutorials[0].task_id == "calc-guide"
    assert loaded.tutorials[0].title == "Calc Guide"


def test_load_plugin_multi_tutorial_files(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "multi_pack"
    plugin_dir.mkdir()
    sub_dir = plugin_dir / "tutorials"
    sub_dir.mkdir()

    t1 = _create_sample_task(task_id="t1", title="Task 1")
    t2 = _create_sample_task(task_id="t2", title="Task 2")
    (sub_dir / "t1.json").write_text(t1.to_json(), encoding="utf-8")
    (sub_dir / "t2.json").write_text(t2.to_json(), encoding="utf-8")

    manifest = {
        "schema_version": "1.0",
        "id": "multi-pack",
        "name": "Multi Pack",
        "version": "1.0",
        "tutorial_files": ["tutorials/t1.json", "tutorials/t2.json"],
    }
    (plugin_dir / TUTORIAL_PLUGIN_MANIFEST_FILENAME).write_text(
        json.dumps(manifest), encoding="utf-8"
    )

    loaded, diags = load_tutorial_plugin(plugin_dir)
    assert loaded is not None
    assert len(loaded.tutorials) == 2
    assert {t.task_id for t in loaded.tutorials} == {"t1", "t2"}


def test_collision_with_builtin_tutorial_rejected(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "colliding_pack"
    plugin_dir.mkdir()

    # Try to shadow built-in tutorial windows-notepad-save
    shadowing_task = _create_sample_task(
        task_id="windows-notepad-save",
        title="Fake Notepad",
    )
    (plugin_dir / "fake.json").write_text(shadowing_task.to_json(), encoding="utf-8")

    manifest = {
        "schema_version": "1.0",
        "id": "shadow-pack",
        "name": "Shadow Pack",
        "version": "1.0",
        "tutorial_file": "fake.json",
    }
    (plugin_dir / TUTORIAL_PLUGIN_MANIFEST_FILENAME).write_text(
        json.dumps(manifest), encoding="utf-8"
    )

    loaded, diags = load_tutorial_plugin(plugin_dir, reserved_task_ids=BUILTIN_TASK_IDS)
    assert loaded is None
    assert any(d.error_code == "ID_COLLISION" for d in diags)
    assert any("collides with an existing or built-in tutorial" in d.message for d in diags)


def test_duplicate_tutorial_id_across_plugins(tmp_path: Path) -> None:
    plugins_root = tmp_path / "plugins"
    plugins_root.mkdir()

    p1 = plugins_root / "plugin_a"
    p1.mkdir()
    p2 = plugins_root / "plugin_b"
    p2.mkdir()

    t_shared = _create_sample_task(task_id="duplicate-id", title="Shared Title")
    (p1 / "tut.json").write_text(t_shared.to_json(), encoding="utf-8")
    (p2 / "tut.json").write_text(t_shared.to_json(), encoding="utf-8")

    (p1 / TUTORIAL_PLUGIN_MANIFEST_FILENAME).write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "id": "plugin-a",
                "name": "Plugin A",
                "version": "1.0",
                "tutorial_file": "tut.json",
            }
        ),
        encoding="utf-8",
    )
    (p2 / TUTORIAL_PLUGIN_MANIFEST_FILENAME).write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "id": "plugin-b",
                "name": "Plugin B",
                "version": "1.0",
                "tutorial_file": "tut.json",
            }
        ),
        encoding="utf-8",
    )

    res = discover_tutorial_plugins(plugins_root, reserved_task_ids=BUILTIN_TASK_IDS)
    assert len(res.loaded_plugins) == 1
    assert len(res.tutorials) == 1
    assert any(d.error_code == "ID_COLLISION" for d in res.diagnostics)


def test_malformed_manifest_json_diagnosed_safely(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "broken_plugin"
    plugin_dir.mkdir()
    (plugin_dir / TUTORIAL_PLUGIN_MANIFEST_FILENAME).write_text(
        "INVALID JSON CONTENT {}}", encoding="utf-8"
    )

    loaded, diags = load_tutorial_plugin(plugin_dir)
    assert loaded is None
    assert len(diags) == 1
    assert diags[0].error_code == "INVALID_MANIFEST_JSON"


def test_missing_tutorial_file_diagnosed_safely(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "missing_file_plugin"
    plugin_dir.mkdir()
    manifest = {
        "schema_version": "1.0",
        "id": "missing-file-pack",
        "name": "Missing",
        "version": "1.0",
        "tutorial_file": "nonexistent.json",
    }
    (plugin_dir / TUTORIAL_PLUGIN_MANIFEST_FILENAME).write_text(
        json.dumps(manifest), encoding="utf-8"
    )

    loaded, diags = load_tutorial_plugin(plugin_dir)
    assert loaded is None
    assert any(d.error_code == "FILE_NOT_FOUND" for d in diags)


def test_discover_plugins_with_mixed_valid_and_broken(tmp_path: Path) -> None:
    plugins_root = tmp_path / "plugins"
    plugins_root.mkdir()

    # Valid plugin
    good = plugins_root / "good_plugin"
    good.mkdir()
    tut_json = _create_sample_task(task_id="good-tut").to_json()
    (good / "tut.json").write_text(tut_json, encoding="utf-8")
    (good / TUTORIAL_PLUGIN_MANIFEST_FILENAME).write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "id": "good-plugin",
                "name": "Good Plugin",
                "version": "1.0",
                "tutorial_file": "tut.json",
            }
        ),
        encoding="utf-8",
    )

    # Broken plugin
    bad = plugins_root / "bad_plugin"
    bad.mkdir()
    (bad / TUTORIAL_PLUGIN_MANIFEST_FILENAME).write_text("{bad json", encoding="utf-8")

    res = discover_tutorial_plugins(plugins_root, reserved_task_ids=BUILTIN_TASK_IDS)
    assert len(res.loaded_plugins) == 1
    assert res.loaded_plugins[0].manifest.plugin_id == "good-plugin"
    assert len(res.tutorials) == 1
    assert res.tutorials[0].task_id == "good-tut"
    assert any(d.error_code == "INVALID_MANIFEST_JSON" for d in res.diagnostics)


# ══════════════════════════════════════════════
# TutorialCatalog & Provenance Tests
# ══════════════════════════════════════════════


def test_tutorial_catalog_provenance_and_titles(tmp_path: Path) -> None:
    plugins_root = tmp_path / "plugins"
    plugins_root.mkdir()
    p_dir = plugins_root / "paint_pack"
    p_dir.mkdir()

    paint_task = _create_sample_task(task_id="paint-draw", title="Draw a Shape", app="MS Paint")
    (p_dir / "paint.json").write_text(paint_task.to_json(), encoding="utf-8")
    (p_dir / TUTORIAL_PLUGIN_MANIFEST_FILENAME).write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "id": "paint-plugins",
                "name": "Paint Tools Pack",
                "version": "1.0",
                "tutorial_file": "paint.json",
            }
        ),
        encoding="utf-8",
    )

    catalog = TutorialCatalog(builtin_tutorials=BUILTIN_TUTORIALS, plugins_dir=plugins_root)
    all_tuts = catalog.get_all_tutorials()
    assert len(all_tuts) == len(BUILTIN_TUTORIALS) + 1

    # Check built-in provenance
    assert not catalog.is_plugin_tutorial("windows-notepad-save")
    assert catalog.get_tutorial_provenance("windows-notepad-save") is None
    notepad = catalog.get_tutorial_by_id("windows-notepad-save")
    assert catalog.format_display_title(notepad) == (
        "Create and Save a Note in Windows Notepad (Windows Notepad)"
    )

    # Check plugin provenance
    assert catalog.is_plugin_tutorial("paint-draw")
    assert catalog.get_tutorial_provenance("paint-draw") == "Paint Tools Pack"
    paint = catalog.get_tutorial_by_id("paint-draw")
    assert catalog.format_display_title(paint) == (
        "[Plugin: Paint Tools Pack] Draw a Shape (MS Paint)"
    )


# ══════════════════════════════════════════════
# End-to-End Engine & UI Integration Tests
# ══════════════════════════════════════════════


def test_engine_runs_plugin_tutorial_end_to_end(tmp_path: Path, qapp: object) -> None:
    plugins_root = tmp_path / "plugins"
    plugins_root.mkdir()
    p_dir = plugins_root / "calc_plugin"
    p_dir.mkdir()

    calc_task = _create_sample_task(
        task_id="calc-flow-test",
        title="Calculator Flow",
        app="Calculator",
    )
    (p_dir / "calc.json").write_text(calc_task.to_json(), encoding="utf-8")
    (p_dir / TUTORIAL_PLUGIN_MANIFEST_FILENAME).write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "id": "calc-pack",
                "name": "Calc Pack",
                "version": "1.0",
                "tutorial_file": "calc.json",
            }
        ),
        encoding="utf-8",
    )

    config = AppConfig(tutorial_plugin_dir=plugins_root)
    engine = TutorialEngine.from_app_config(config)

    # Verify discovery
    assert engine.is_plugin_tutorial("calc-flow-test")
    assert engine.get_tutorial_provenance("calc-flow-test") == "Calc Pack"

    # Select the plugin tutorial
    engine.select_tutorial("calc-flow-test")
    assert engine.active_task.task_id == "calc-flow-test"
    assert "[Plugin: Calc Pack]" in engine.get_tutorial_status_summary()

    # Step through the tutorial
    assert engine.current_step.order == 1
    hint = engine.request_hint()
    assert "Win+R" in hint.instruction_text

    # Complete step 1 manually
    assert engine.complete_current_step_manually(user_initiated=True)
    assert engine.current_step.order == 2

    # Complete step 2 manually -> finishes tutorial
    assert engine.complete_current_step_manually(user_initiated=True)
    assert engine.state == TutorialSessionState.COMPLETED

    # Record feedback
    feedback = engine.record_feedback(
        5, difficulty="just_right", comment="Plugin tutorial was great!"
    )
    assert feedback.rating == 5
    assert feedback.task_id == "calc-flow-test"


def test_ui_shows_plugin_provenance_and_dynamic_reload(tmp_path: Path, qapp: object) -> None:
    plugins_root = tmp_path / "plugins"
    plugins_root.mkdir()

    config = AppConfig(tutorial_plugin_dir=plugins_root)
    controller = DesktopController(config=config)
    window = DiagnosticWindow(controller)

    # Initially only built-ins in combo
    combo_count = window._combo_tutorial.count()
    combo_items = [window._combo_tutorial.itemText(i) for i in range(combo_count)]
    assert all("[Plugin:" not in item for item in combo_items)

    # Now add a plugin on the fly
    p_dir = plugins_root / "on_the_fly"
    p_dir.mkdir()
    otf_task = _create_sample_task(task_id="otf-task", title="On The Fly", app="App")
    (p_dir / "otf.json").write_text(otf_task.to_json(), encoding="utf-8")
    (p_dir / TUTORIAL_PLUGIN_MANIFEST_FILENAME).write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "id": "otf-plugin",
                "name": "Fly Pack",
                "version": "1.0",
                "tutorial_file": "otf.json",
            }
        ),
        encoding="utf-8",
    )

    # Reload plugins dynamically
    controller.reload_tutorial_plugins()

    # Verify combo refreshed
    new_count = window._combo_tutorial.count()
    updated_items = [window._combo_tutorial.itemText(i) for i in range(new_count)]
    assert any("[Plugin: Fly Pack] On The Fly (App)" in item for item in updated_items)

    window.close()
    if controller.is_active:
        controller.stop(timeout_ms=100)
