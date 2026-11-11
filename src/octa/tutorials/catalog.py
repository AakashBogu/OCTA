"""Built-in sample tutorial catalog and loader utilities for OCTA (Phase 6.0).

All sample tutorials are grounded in real desktop workflows and UI concepts that
OCTA actually supports: step-by-step text guidance, passive visual overlay
highlights and directional arrows, user-initiated screen capture with single-use
consent for AI progress verification, manual text-based step completion when AI
is unavailable, and push-to-talk voice interaction.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from octa.intelligence.capture import CaptureRegion
from octa.intelligence.exceptions import TaskValidationError
from octa.intelligence.tasks import (
    ExpectedOutcome,
    OverlayElementType,
    OverlayHint,
    TaskDefinition,
    TaskStep,
)
from octa.tutorials.plugins import (
    LoadedTutorialPlugin,
    PluginDiscoveryResult,
    discover_tutorial_plugins,
)


def build_octa_welcome_tutorial() -> TaskDefinition:
    """Build the default 'Getting Started with OCTA' tutorial."""
    return TaskDefinition(
        task_id="octa-welcome-task",
        version="1.0",
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
                    verification_hint="Confirm that the application window is open on screen.",
                    target_region=CaptureRegion(x=0, y=0, width=800, height=600),
                ),
                hints=("Use Push to Talk to ask for the current step, a hint, or the next step.",),
                overlay_cues=(
                    OverlayHint(
                        kind=OverlayElementType.HIGHLIGHT,
                        label="Step 1 Target Area",
                        color="#00E676",
                        bounds=CaptureRegion(x=40, y=40, width=360, height=120),
                    ),
                ),
            ),
            TaskStep(
                step_id="step-2",
                order=2,
                title="Complete the Guided Action",
                instruction="Follow the highlighted UI element and verify your progress.",
                expected_outcome=ExpectedOutcome(
                    description="The guided action has been completed in the application.",
                    required_elements=("Completed state",),
                    verification_hint="Check that the guided action is visible in the window.",
                    target_region=CaptureRegion(x=0, y=0, width=800, height=600),
                ),
                hints=("You can click Read Instruction Aloud at any time to hear the step again.",),
                overlay_cues=(
                    OverlayHint(
                        kind=OverlayElementType.ARROW,
                        label="Verify or Complete Step",
                        color="#FFAB00",
                        start_x=220,
                        start_y=180,
                        end_x=220,
                        end_y=110,
                    ),
                ),
            ),
        ),
    )


def build_notepad_save_tutorial() -> TaskDefinition:
    """Build a grounded sample tutorial for creating and saving a file in Windows Notepad."""
    return TaskDefinition(
        task_id="windows-notepad-save",
        version="1.0",
        title="Create and Save a Note in Windows Notepad",
        application="Windows Notepad",
        goal="Write a short text note in Windows Notepad and save it as a .txt file.",
        steps=(
            TaskStep(
                step_id="notepad-type-text",
                order=1,
                title="Type a Short Note",
                instruction=(
                    "Open Windows Notepad (notepad.exe) and type a short line of text "
                    "into the document editing area."
                ),
                expected_outcome=ExpectedOutcome(
                    description="Windows Notepad is open with text visible in the editor area.",
                    required_elements=("Notepad",),
                    verification_hint="Look for the Notepad window and typed text in the editor.",
                    target_region=CaptureRegion(x=0, y=0, width=1024, height=768),
                ),
                hints=(
                    "Press Win+R, type notepad, and press Enter to launch Windows Notepad.",
                    "Click inside the white text area before typing.",
                ),
                overlay_cues=(
                    OverlayHint(
                        kind=OverlayElementType.HIGHLIGHT,
                        label="Notepad Editor Area",
                        color="#00E676",
                        bounds=CaptureRegion(x=80, y=120, width=500, height=300),
                    ),
                ),
            ),
            TaskStep(
                step_id="notepad-open-file-menu",
                order=2,
                title="Open the File Menu",
                instruction=(
                    "Click 'File' in the top-left menu bar of Notepad (or press Alt+F) "
                    "to open the File menu dropdown."
                ),
                expected_outcome=ExpectedOutcome(
                    description="The File menu dropdown is open showing Save and Save As.",
                    required_elements=("Save",),
                    verification_hint="Confirm the File menu list is expanded with Save options.",
                    target_region=CaptureRegion(x=0, y=0, width=800, height=600),
                ),
                hints=("You can also press Alt+F on your keyboard to open the File menu.",),
                overlay_cues=(
                    OverlayHint(
                        kind=OverlayElementType.ARROW,
                        label="Click File Menu",
                        color="#FFAB00",
                        start_x=140,
                        start_y=110,
                        end_x=60,
                        end_y=55,
                    ),
                ),
            ),
            TaskStep(
                step_id="notepad-save-as",
                order=3,
                title="Save the Document",
                instruction=(
                    "Click 'Save As' (or press Ctrl+Shift+S), enter a file name such as "
                    "'notes.txt' in the Save As dialog, and click Save."
                ),
                expected_outcome=ExpectedOutcome(
                    description=(
                        "The Save As dialog is visible or the Notepad title bar shows "
                        "the saved file name."
                    ),
                    required_elements=("Save",),
                    verification_hint="Check for the Save As dialog or updated window title.",
                    target_region=CaptureRegion(x=0, y=0, width=1024, height=768),
                ),
                hints=("Shortcut: Press Ctrl+S or Ctrl+Shift+S to open the Save dialog directly.",),
                overlay_cues=(
                    OverlayHint(
                        kind=OverlayElementType.HIGHLIGHT,
                        label="Save As Dialog Area",
                        color="#00E676",
                        bounds=CaptureRegion(x=100, y=100, width=420, height=280),
                    ),
                ),
            ),
        ),
    )


def build_explorer_new_folder_tutorial() -> TaskDefinition:
    """Build a grounded sample tutorial for creating a folder in Windows File Explorer."""
    return TaskDefinition(
        task_id="windows-explorer-new-folder",
        version="1.0",
        title="Create a New Folder in Windows File Explorer",
        application="Windows File Explorer",
        goal="Open Windows File Explorer and create a new named folder in a directory.",
        steps=(
            TaskStep(
                step_id="explorer-open-window",
                order=1,
                title="Open File Explorer",
                instruction=(
                    "Open Windows File Explorer (press Win+E) and navigate to the folder "
                    "where you want to create a new folder (such as Documents or Desktop)."
                ),
                expected_outcome=ExpectedOutcome(
                    description="Windows File Explorer window is open to a folder view.",
                    required_elements=("File Explorer",),
                    verification_hint="Confirm a File Explorer window is visible on screen.",
                    target_region=CaptureRegion(x=0, y=0, width=1024, height=768),
                ),
                hints=("Press Win+E on your keyboard to open File Explorer quickly.",),
                overlay_cues=(
                    OverlayHint(
                        kind=OverlayElementType.HIGHLIGHT,
                        label="File Explorer Window",
                        color="#00E676",
                        bounds=CaptureRegion(x=60, y=60, width=600, height=400),
                    ),
                ),
            ),
            TaskStep(
                step_id="explorer-create-folder",
                order=2,
                title="Create and Name the Folder",
                instruction=(
                    "Click 'New' -> 'Folder' in the top command bar (or press Ctrl+Shift+N), "
                    "type a name for the new folder, and press Enter."
                ),
                expected_outcome=ExpectedOutcome(
                    description="A newly created folder is visible in the File Explorer list.",
                    required_elements=("Folder",),
                    verification_hint="Check that the new folder item appears in the file list.",
                    target_region=CaptureRegion(x=0, y=0, width=1024, height=768),
                ),
                hints=(
                    "Press Ctrl+Shift+N to create a new folder immediately in the current view.",
                ),
                overlay_cues=(
                    OverlayHint(
                        kind=OverlayElementType.ARROW,
                        label="New Folder Command",
                        color="#FFAB00",
                        start_x=200,
                        start_y=160,
                        end_x=140,
                        end_y=95,
                    ),
                ),
            ),
        ),
    )


BUILTIN_TUTORIALS: tuple[TaskDefinition, ...] = (
    build_octa_welcome_tutorial(),
    build_notepad_save_tutorial(),
    build_explorer_new_folder_tutorial(),
)

BUILTIN_TASK_IDS: frozenset[str] = frozenset(t.task_id for t in BUILTIN_TUTORIALS)


def get_builtin_tutorials() -> tuple[TaskDefinition, ...]:
    """Return all built-in validated sample tutorials."""
    return BUILTIN_TUTORIALS


class TutorialCatalog:
    """Manages built-in tutorials and discovered external tutorial plugins (Phase 8.1)."""

    def __init__(
        self,
        builtin_tutorials: Sequence[TaskDefinition] | None = None,
        plugins_dir: Path | str | None = None,
    ) -> None:
        self._builtin_tutorials: tuple[TaskDefinition, ...] = (
            tuple(builtin_tutorials) if builtin_tutorials is not None else BUILTIN_TUTORIALS
        )
        self._builtin_ids: frozenset[str] = frozenset(t.task_id for t in self._builtin_tutorials)
        self._plugins_dir: Path | None = Path(plugins_dir) if plugins_dir is not None else None
        self._plugin_discovery: PluginDiscoveryResult = PluginDiscoveryResult(
            loaded_plugins=(), tutorials=(), diagnostics=()
        )
        self._plugin_provenance: dict[str, LoadedTutorialPlugin] = {}

        if self._plugins_dir is not None:
            self.reload_plugins(self._plugins_dir)

    @property
    def builtin_tutorials(self) -> tuple[TaskDefinition, ...]:
        """Tuple of immutable built-in sample tutorials."""
        return self._builtin_tutorials

    @property
    def plugins_dir(self) -> Path | None:
        """Configured directory path for external tutorial plugins."""
        return self._plugins_dir

    @property
    def loaded_plugins(self) -> tuple[LoadedTutorialPlugin, ...]:
        """Tuple of currently loaded tutorial plugins."""
        return self._plugin_discovery.loaded_plugins

    @property
    def discovery_result(self) -> PluginDiscoveryResult:
        """Complete discovery result including loaded plugins, tutorials, and diagnostics."""
        return self._plugin_discovery

    def reload_plugins(self, plugins_dir: Path | str | None = None) -> PluginDiscoveryResult:
        """Scan or re-scan the plugins directory, refreshing discovered plugins."""
        if plugins_dir is not None:
            self._plugins_dir = Path(plugins_dir)

        result = discover_tutorial_plugins(
            self._plugins_dir,
            reserved_task_ids=self._builtin_ids,
        )
        self._plugin_discovery = result
        self._plugin_provenance.clear()

        for plugin in result.loaded_plugins:
            for tut in plugin.tutorials:
                self._plugin_provenance[tut.task_id] = plugin

        return result

    def get_all_tutorials(self) -> tuple[TaskDefinition, ...]:
        """Return combined tuple of built-in and discovered plugin tutorials."""
        return self._builtin_tutorials + self._plugin_discovery.tutorials

    def get_tutorial_by_id(self, task_id: str) -> TaskDefinition:
        """Return a TaskDefinition by task_id, or raise TaskValidationError."""
        if not isinstance(task_id, str) or not task_id.strip():
            raise TaskValidationError("Tutorial task_id must be a non-empty string.")
        cleaned = task_id.strip()
        for tutorial in self.get_all_tutorials():
            if tutorial.task_id == cleaned:
                return tutorial
        available = ", ".join(t.task_id for t in self.get_all_tutorials())
        raise TaskValidationError(
            f"Unknown tutorial task_id '{cleaned}'. Available tutorials: {available}."
        )

    def is_plugin_tutorial(self, task_id: str) -> bool:
        """Return True if task_id belongs to a loaded external plugin."""
        return task_id.strip() in self._plugin_provenance

    def get_tutorial_provenance(self, task_id: str) -> str | None:
        """Return the plugin name or identifier if task_id is from a plugin, otherwise None."""
        plugin = self._plugin_provenance.get(task_id.strip())
        if plugin is not None:
            return plugin.manifest.name or plugin.manifest.plugin_id
        return None

    def format_display_title(self, task_def: TaskDefinition) -> str:
        """Format a tutorial's display title for the UI, clearly showing [Plugin] provenance."""
        provenance = self.get_tutorial_provenance(task_def.task_id)
        if provenance:
            return f"[Plugin: {provenance}] {task_def.title} ({task_def.application})"
        return f"{task_def.title} ({task_def.application})"


def get_tutorial_by_id(task_id: str) -> TaskDefinition:
    """Return a built-in TaskDefinition by task_id, or raise TaskValidationError."""
    if not isinstance(task_id, str) or not task_id.strip():
        raise TaskValidationError("Tutorial task_id must be a non-empty string.")
    cleaned = task_id.strip()
    for tutorial in BUILTIN_TUTORIALS:
        if tutorial.task_id == cleaned:
            return tutorial
    available = ", ".join(t.task_id for t in BUILTIN_TUTORIALS)
    raise TaskValidationError(
        f"Unknown tutorial task_id '{cleaned}'. Available built-in tutorials: {available}."
    )


def load_tutorial_from_json_file(path: Path | str) -> TaskDefinition:
    """Load and validate a TaskDefinition from a UTF-8 JSON file on disk."""
    file_path = Path(path)
    if not file_path.is_file():
        raise TaskValidationError(f"Tutorial JSON file not found: {file_path}")
    raw_bytes = file_path.read_bytes()
    return TaskDefinition.from_json(raw_bytes)
