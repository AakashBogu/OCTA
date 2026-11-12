"""Interactive Step & Tutorial Builder UI for OCTA (Phase 10.2).

Provides an in-app visual editor for authoring and fine-tuning tutorial definitions
(TaskDefinition) and sequential steps (TaskStep / TaskStepDefinition).
Supports:
- Task metadata editing (ID, title, application, goal, version).
- Ordered step list management (add, delete, duplicate, reorder up/down).
- Step detail editing (title, instructions, sequential progressive hints, expected outcomes).
- Visual guidance cue integration with Phase 10.1 AuthoringCueDialog.
- Schema validation enforcing TaskDefinition rules and built-in ID collision prevention.
- Safe atomic JSON persistence preventing corrupted or truncated files.
- Local cue preview without screen recording or input synthesis.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from octa.desktop.controller import DesktopController
from octa.intelligence.authoring_overlay import AuthoringCueDialog
from octa.intelligence.capture import CaptureRegion
from octa.intelligence.exceptions import TaskValidationError
from octa.intelligence.overlay import (
    GuidanceOverlayWidget,
    OverlayBounds,
)
from octa.intelligence.tasks import (
    ExpectedOutcome,
    OverlayElementType,
    OverlayHint,
    TaskDefinition,
    TaskStep,
    load_task_definition_from_file,
    save_task_definition_to_file,
)
from octa.tutorials.catalog import BUILTIN_TASK_IDS

logger = logging.getLogger("octa.desktop.authoring")


def _format_cue_display_text(cue: OverlayHint) -> str:
    """Format an OverlayHint into a concise, readable summary string."""
    if cue.kind == OverlayElementType.HIGHLIGHT and cue.bounds:
        b = cue.bounds
        tag = f" '{cue.label}'" if cue.label else ""
        return f"Highlight Box [{b.x}, {b.y}, {b.width}×{b.height}]{tag}"
    elif cue.kind == OverlayElementType.ARROW:
        tag = f" '{cue.label}'" if cue.label else ""
        return f"Arrow ({cue.start_x}, {cue.start_y}) → ({cue.end_x}, {cue.end_y}){tag}"
    return f"Overlay Cue ({cue.kind.value})"


class TutorialAuthoringDialog(QDialog):
    """Full-featured interactive tutorial authoring and step builder modal."""

    def __init__(
        self,
        controller: DesktopController | None = None,
        task: TaskDefinition | None = None,
        file_path: Path | str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("OCTA Tutorial Authoring Studio")
        self.resize(1020, 720)
        self.setMinimumSize(850, 580)

        self._controller = controller
        self._current_file_path = Path(file_path).resolve() if file_path else None
        self._is_dirty = False
        self._active_step_index = 0
        self._preview_overlay: GuidanceOverlayWidget | None = None

        # Mutable in-memory step working state
        self._steps: list[dict[str, Any]] = []

        self._init_ui()

        if task is not None:
            self.load_task(task)
        elif self._current_file_path is not None and self._current_file_path.is_file():
            self.open_file(self._current_file_path)
        else:
            self.new_task()

    # ──────────────────────────────────────────
    # UI Layout Construction
    # ──────────────────────────────────────────

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 10, 12, 10)
        main_layout.setSpacing(8)

        # ── 1. Top Action Toolbar ─────────────────
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)

        self._btn_new = QPushButton("📄 &New", self)
        self._btn_new.setAccessibleName("Create New Tutorial")
        self._btn_new.clicked.connect(self._on_new_clicked)

        self._btn_open = QPushButton("📂 &Open...", self)
        self._btn_open.setAccessibleName("Open Tutorial JSON File")
        self._btn_open.clicked.connect(self._on_open_clicked)

        self._btn_save = QPushButton("💾 &Save", self)
        self._btn_save.setAccessibleName("Save Tutorial JSON")
        self._btn_save.clicked.connect(self._on_save_clicked)

        self._btn_save_as = QPushButton("Save &As...", self)
        self._btn_save_as.setAccessibleName("Save Tutorial As JSON")
        self._btn_save_as.clicked.connect(self._on_save_as_clicked)

        self._btn_validate = QPushButton("✔ &Validate", self)
        self._btn_validate.setAccessibleName("Validate Tutorial Schema")
        self._btn_validate.clicked.connect(self._on_validate_clicked)

        self._btn_project = QPushButton("📁 Plugin &Studio...", self)
        self._btn_project.setAccessibleName("Open Plugin Project Studio")
        self._btn_project.clicked.connect(self._on_project_studio_clicked)

        toolbar.addWidget(self._btn_new)
        toolbar.addWidget(self._btn_open)
        toolbar.addWidget(self._btn_save)
        toolbar.addWidget(self._btn_save_as)
        toolbar.addWidget(self._btn_validate)
        toolbar.addWidget(self._btn_project)
        toolbar.addStretch()

        self._lbl_filepath = QLabel("Unsaved Tutorial", self)
        self._lbl_filepath.setStyleSheet("color: #64748B; font-size: 11px; font-weight: bold;")
        toolbar.addWidget(self._lbl_filepath)

        main_layout.addLayout(toolbar)

        # ── 2. Splitter: Left (Metadata & Steps) | Right (Step Inspector) ──
        splitter = QSplitter(Qt.Orientation.Horizontal, self)

        # Left Column Container
        left_container = QWidget(splitter)
        left_layout = QVBoxLayout(left_container)
        left_layout.setContentsMargins(0, 0, 6, 0)
        left_layout.setSpacing(8)

        # Task Metadata Group
        grp_meta = QGroupBox("Tutorial Metadata", left_container)
        meta_layout = QVBoxLayout(grp_meta)
        meta_layout.setSpacing(6)

        row_id_ver = QHBoxLayout()
        self._txt_task_id = QLineEdit(grp_meta)
        self._txt_task_id.setPlaceholderText("e.g. paint-draw-circle")
        self._txt_task_id.setAccessibleName("Task Identifier")
        self._txt_task_id.textChanged.connect(self._mark_dirty)

        self._txt_version = QLineEdit("1.0", grp_meta)
        self._txt_version.setMaximumWidth(70)
        self._txt_version.setAccessibleName("Tutorial Version")
        self._txt_version.textChanged.connect(self._mark_dirty)

        row_id_ver.addWidget(QLabel("Task ID:", grp_meta))
        row_id_ver.addWidget(self._txt_task_id, stretch=1)
        row_id_ver.addWidget(QLabel("Version:", grp_meta))
        row_id_ver.addWidget(self._txt_version)
        meta_layout.addLayout(row_id_ver)

        row_title = QHBoxLayout()
        self._txt_title = QLineEdit(grp_meta)
        self._txt_title.setPlaceholderText("e.g. Draw a Circle in Paint")
        self._txt_title.setAccessibleName("Tutorial Title")
        self._txt_title.textChanged.connect(self._mark_dirty)
        row_title.addWidget(QLabel("Title:", grp_meta))
        row_title.addWidget(self._txt_title, stretch=1)
        meta_layout.addLayout(row_title)

        row_app = QHBoxLayout()
        self._txt_application = QLineEdit(grp_meta)
        self._txt_application.setPlaceholderText("e.g. Paint")
        self._txt_application.setAccessibleName("Target Application")
        self._txt_application.textChanged.connect(self._mark_dirty)
        row_app.addWidget(QLabel("App:", grp_meta))
        row_app.addWidget(self._txt_application, stretch=1)
        meta_layout.addLayout(row_app)

        self._txt_goal = QLineEdit(grp_meta)
        self._txt_goal.setPlaceholderText("Overall goal or outcome learned")
        self._txt_goal.setAccessibleName("Tutorial Learning Goal")
        self._txt_goal.textChanged.connect(self._mark_dirty)
        meta_layout.addWidget(QLabel("Goal:", grp_meta))
        meta_layout.addWidget(self._txt_goal)

        left_layout.addWidget(grp_meta)

        # Steps Sequence Group
        grp_steps = QGroupBox("Tutorial Steps", left_container)
        steps_layout = QVBoxLayout(grp_steps)
        steps_layout.setSpacing(6)

        self._list_steps = QListWidget(grp_steps)
        self._list_steps.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list_steps.setAccessibleName("Steps Sequence List")
        self._list_steps.currentRowChanged.connect(self._on_step_selection_changed)
        steps_layout.addWidget(self._list_steps, stretch=1)

        step_btn_row1 = QHBoxLayout()
        step_btn_row1.setSpacing(4)
        self._btn_add_step = QPushButton("➕ Add Step", grp_steps)
        self._btn_add_step.clicked.connect(self._on_add_step_clicked)
        self._btn_dup_step = QPushButton("📋 Duplicate", grp_steps)
        self._btn_dup_step.clicked.connect(self._on_duplicate_step_clicked)
        self._btn_del_step = QPushButton("🗑 Delete", grp_steps)
        self._btn_del_step.clicked.connect(self._on_delete_step_clicked)
        step_btn_row1.addWidget(self._btn_add_step)
        step_btn_row1.addWidget(self._btn_dup_step)
        step_btn_row1.addWidget(self._btn_del_step)
        steps_layout.addLayout(step_btn_row1)

        step_btn_row2 = QHBoxLayout()
        step_btn_row2.setSpacing(4)
        self._btn_move_up = QPushButton("⬆ Move Up", grp_steps)
        self._btn_move_up.clicked.connect(self._on_move_up_clicked)
        self._btn_move_down = QPushButton("⬇ Move Down", grp_steps)
        self._btn_move_down.clicked.connect(self._on_move_down_clicked)
        step_btn_row2.addWidget(self._btn_move_up)
        step_btn_row2.addWidget(self._btn_move_down)
        steps_layout.addLayout(step_btn_row2)

        left_layout.addWidget(grp_steps, stretch=1)
        splitter.addWidget(left_container)

        # Right Column Container (Step Detail Inspector)
        right_scroll = QScrollArea(splitter)
        right_scroll.setWidgetResizable(True)
        right_widget = QWidget(right_scroll)
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(6, 0, 0, 0)
        right_layout.setSpacing(10)

        self._grp_inspector = QGroupBox("Step Inspector", right_widget)
        insp_layout = QVBoxLayout(self._grp_inspector)
        insp_layout.setSpacing(8)

        # Step ID & Title
        row_s_meta = QHBoxLayout()
        self._txt_step_id = QLineEdit(self._grp_inspector)
        self._txt_step_id.setPlaceholderText("e.g. step-1-brush")
        self._txt_step_id.setAccessibleName("Step ID")
        self._txt_step_id.textChanged.connect(self._on_step_field_edited)

        self._txt_step_title = QLineEdit(self._grp_inspector)
        self._txt_step_title.setPlaceholderText("e.g. Select the Brushes tool")
        self._txt_step_title.setAccessibleName("Step Title")
        self._txt_step_title.textChanged.connect(self._on_step_field_edited)

        row_s_meta.addWidget(QLabel("Step ID:", self._grp_inspector))
        row_s_meta.addWidget(self._txt_step_id, stretch=1)
        row_s_meta.addWidget(QLabel("Title:", self._grp_inspector))
        row_s_meta.addWidget(self._txt_step_title, stretch=2)
        insp_layout.addLayout(row_s_meta)

        # Step Instruction
        insp_layout.addWidget(QLabel("Instruction (spoken & displayed to learner):"))
        self._txt_step_instruction = QPlainTextEdit(self._grp_inspector)
        self._txt_step_instruction.setMaximumHeight(70)
        self._txt_step_instruction.setPlaceholderText(
            "Clear, actionable guidance text describing what the user should do."
        )
        self._txt_step_instruction.setAccessibleName("Step Instruction Text")
        self._txt_step_instruction.textChanged.connect(self._on_step_field_edited)
        insp_layout.addWidget(self._txt_step_instruction)

        # Progressive Hints
        insp_layout.addWidget(QLabel("Progressive Hints (one per line, shown sequentially):"))
        self._txt_step_hints = QPlainTextEdit(self._grp_inspector)
        self._txt_step_hints.setMaximumHeight(70)
        self._txt_step_hints.setPlaceholderText(
            "Look for the icon in the top toolbar.\nTry pressing shortcut B."
        )
        self._txt_step_hints.setAccessibleName("Step Progressive Hints")
        self._txt_step_hints.textChanged.connect(self._on_step_field_edited)
        insp_layout.addWidget(self._txt_step_hints)

        # Expected Outcome Box
        grp_outcome = QGroupBox("Expected Outcome (AI Verification Criteria)", self._grp_inspector)
        out_layout = QVBoxLayout(grp_outcome)
        out_layout.setSpacing(6)

        row_o1 = QHBoxLayout()
        self._txt_outcome_desc = QLineEdit(grp_outcome)
        self._txt_outcome_desc.setPlaceholderText("e.g. Brush menu is open and visible on screen")
        self._txt_outcome_desc.setAccessibleName("Outcome Description")
        self._txt_outcome_desc.textChanged.connect(self._on_step_field_edited)
        row_o1.addWidget(QLabel("Outcome:", grp_outcome))
        row_o1.addWidget(self._txt_outcome_desc, stretch=1)
        out_layout.addLayout(row_o1)

        row_o2 = QHBoxLayout()
        self._txt_outcome_req = QLineEdit(grp_outcome)
        self._txt_outcome_req.setPlaceholderText("Comma-separated: Brush menu, Canvas")
        self._txt_outcome_req.setAccessibleName("Required Visual Elements")
        self._txt_outcome_req.textChanged.connect(self._on_step_field_edited)
        row_o2.addWidget(QLabel("Required Elements:", grp_outcome))
        row_o2.addWidget(self._txt_outcome_req, stretch=1)
        out_layout.addLayout(row_o2)

        row_o3 = QHBoxLayout()
        self._txt_outcome_hint = QLineEdit(grp_outcome)
        self._txt_outcome_hint.setPlaceholderText(
            "Optional hint for verification AI (e.g. Check for dropdown expanded)"
        )
        self._txt_outcome_hint.setAccessibleName("Verification Prompt Hint")
        self._txt_outcome_hint.textChanged.connect(self._on_step_field_edited)
        row_o3.addWidget(QLabel("Verifier Hint:", grp_outcome))
        row_o3.addWidget(self._txt_outcome_hint, stretch=1)
        out_layout.addLayout(row_o3)

        insp_layout.addWidget(grp_outcome)

        # Visual Guidance Overlay Cues Box
        grp_cues = QGroupBox("Visual Guidance Overlay Cues", self._grp_inspector)
        cues_layout = QVBoxLayout(grp_cues)
        cues_layout.setSpacing(6)

        self._list_cues = QListWidget(grp_cues)
        self._list_cues.setMaximumHeight(85)
        self._list_cues.setAccessibleName("Step Overlay Cues List")
        cues_layout.addWidget(self._list_cues)

        cues_btn_row = QHBoxLayout()
        cues_btn_row.setSpacing(6)

        self._btn_cue_screen = QPushButton("🎯 Select on Screen...", grp_cues)
        self._btn_cue_screen.setStyleSheet(
            "background-color: #0284C7; color: white; font-weight: bold; padding: 4px 8px;"
        )
        self._btn_cue_screen.setAccessibleName("Launch Visual Cue Selector")
        self._btn_cue_screen.clicked.connect(self._on_select_cue_on_screen_clicked)

        self._btn_remove_cue = QPushButton("🗑 Remove Cue", grp_cues)
        self._btn_remove_cue.setAccessibleName("Remove Selected Cue")
        self._btn_remove_cue.clicked.connect(self._on_remove_cue_clicked)

        self._btn_preview_cues = QPushButton("👁 Preview Cues", grp_cues)
        self._btn_preview_cues.setAccessibleName("Preview Cues on Desktop")
        self._btn_preview_cues.clicked.connect(self._on_preview_cues_clicked)

        cues_btn_row.addWidget(self._btn_cue_screen)
        cues_btn_row.addWidget(self._btn_remove_cue)
        cues_btn_row.addWidget(self._btn_preview_cues)
        cues_layout.addLayout(cues_btn_row)

        insp_layout.addWidget(grp_cues)
        right_layout.addWidget(self._grp_inspector)
        right_layout.addStretch()

        right_scroll.setWidget(right_widget)
        splitter.addWidget(right_scroll)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 6)

        main_layout.addWidget(splitter, stretch=1)

        # ── 3. Bottom Status Bar & Close ───────────
        bot_row = QHBoxLayout()
        self._lbl_status = QLabel("Ready", self)
        self._lbl_status.setStyleSheet("color: #475569; font-size: 11px;")
        bot_row.addWidget(self._lbl_status, stretch=1)

        self._btn_close = QPushButton("&Close", self)
        self._btn_close.clicked.connect(self.close)
        bot_row.addWidget(self._btn_close)
        main_layout.addLayout(bot_row)

    # ──────────────────────────────────────────
    # Task & Step State Management
    # ──────────────────────────────────────────

    def new_task(self) -> None:
        """Reset the authoring studio with an empty starter tutorial."""
        if self._is_dirty and not self._prompt_discard_unsaved():
            return

        self._txt_task_id.setText("new-tutorial-task")
        self._txt_version.setText("1.0")
        self._txt_title.setText("New Tutorial")
        self._txt_application.setText("Application")
        self._txt_goal.setText("Learn essential application actions")
        self._current_file_path = None
        self._lbl_filepath.setText("New Tutorial (Unsaved)")

        # Create starter step
        self._steps = [
            {
                "step_id": "step-1",
                "order": 1,
                "title": "Initial Action",
                "instruction": "Open the application and locate the primary tool.",
                "hints": ["Look for the tool in the main interface menu."],
                "outcome_desc": "Application is ready for interaction",
                "outcome_req": ["Main window"],
                "outcome_hint": "Confirm window is open",
                "overlay_cues": [],
            }
        ]

        self._active_step_index = 0
        self._refresh_steps_list()
        self._load_active_step_into_inspector()
        self._is_dirty = False
        self._lbl_status.setText("Created new tutorial.")

    def load_task(self, task: TaskDefinition) -> None:
        """Populate the editor fields from a validated TaskDefinition."""
        if not isinstance(task, TaskDefinition):
            raise TaskValidationError(f"Expected TaskDefinition, got {type(task).__name__}.")

        self._txt_task_id.setText(task.task_id)
        self._txt_version.setText(task.version)
        self._txt_title.setText(task.title)
        self._txt_application.setText(task.application)
        self._txt_goal.setText(task.goal)

        self._steps = []
        for s in task.steps:
            self._steps.append(
                {
                    "step_id": s.step_id,
                    "order": s.order,
                    "title": s.title,
                    "instruction": s.instruction,
                    "hints": list(s.hints),
                    "outcome_desc": s.expected_outcome.description,
                    "outcome_req": list(s.expected_outcome.required_elements),
                    "outcome_hint": s.expected_outcome.verification_hint,
                    "overlay_cues": list(s.overlay_cues),
                }
            )

        self._active_step_index = 0
        self._refresh_steps_list()
        self._load_active_step_into_inspector()
        self._is_dirty = False
        self._lbl_status.setText(f"Loaded '{task.title}' with {len(self._steps)} steps.")

    def _refresh_steps_list(self) -> None:
        """Update the steps list widget while preserving selection."""
        self._list_steps.blockSignals(True)
        self._list_steps.clear()
        for idx, s in enumerate(self._steps):
            order = idx + 1
            s["order"] = order
            title = s.get("title", f"Step {order}")
            step_id = s.get("step_id", f"step-{order}")
            item = QListWidgetItem(f"#{order}: {title} ({step_id})")
            self._list_steps.addItem(item)

        if 0 <= self._active_step_index < len(self._steps):
            self._list_steps.setCurrentRow(self._active_step_index)
        elif len(self._steps) > 0:
            self._active_step_index = 0
            self._list_steps.setCurrentRow(0)
        self._list_steps.blockSignals(False)

        can_delete = len(self._steps) > 1
        self._btn_del_step.setEnabled(can_delete)
        self._btn_move_up.setEnabled(self._active_step_index > 0)
        self._btn_move_down.setEnabled(0 <= self._active_step_index < len(self._steps) - 1)

    def _load_active_step_into_inspector(self) -> None:
        """Sync the active step's data into the right-hand inspector inputs."""
        if not (0 <= self._active_step_index < len(self._steps)):
            self._grp_inspector.setEnabled(False)
            return

        self._grp_inspector.setEnabled(True)
        s = self._steps[self._active_step_index]

        # Block signals during data sync to avoid dirty loops
        for w in (
            self._txt_step_id,
            self._txt_step_title,
            self._txt_step_instruction,
            self._txt_step_hints,
            self._txt_outcome_desc,
            self._txt_outcome_req,
            self._txt_outcome_hint,
        ):
            w.blockSignals(True)

        self._txt_step_id.setText(str(s.get("step_id", "")))
        self._txt_step_title.setText(str(s.get("title", "")))
        self._txt_step_instruction.setPlainText(str(s.get("instruction", "")))
        hints_list = s.get("hints", [])
        self._txt_step_hints.setPlainText("\n".join(hints_list))

        self._txt_outcome_desc.setText(str(s.get("outcome_desc", "")))
        req_list = s.get("outcome_req", [])
        self._txt_outcome_req.setText(", ".join(req_list))
        self._txt_outcome_hint.setText(str(s.get("outcome_hint", "")))

        for w in (
            self._txt_step_id,
            self._txt_step_title,
            self._txt_step_instruction,
            self._txt_step_hints,
            self._txt_outcome_desc,
            self._txt_outcome_req,
            self._txt_outcome_hint,
        ):
            w.blockSignals(False)

        # Refresh cues list
        self._refresh_cues_list()

    def _refresh_cues_list(self) -> None:
        """Render overlay hints for active step in the inspector list."""
        self._list_cues.clear()
        if not (0 <= self._active_step_index < len(self._steps)):
            return

        cues = self._steps[self._active_step_index].get("overlay_cues", [])
        for cue in cues:
            if isinstance(cue, OverlayHint):
                self._list_cues.addItem(_format_cue_display_text(cue))

    def _save_inspector_to_active_step(self) -> None:
        """Write inspector widget values back into the active step dictionary."""
        if not (0 <= self._active_step_index < len(self._steps)):
            return

        s = self._steps[self._active_step_index]
        s["step_id"] = self._txt_step_id.text().strip()
        s["title"] = self._txt_step_title.text().strip()
        s["instruction"] = self._txt_step_instruction.toPlainText().strip()

        hints_text = self._txt_step_hints.toPlainText()
        s["hints"] = [line.strip() for line in hints_text.splitlines() if line.strip()]

        s["outcome_desc"] = self._txt_outcome_desc.text().strip()
        req_text = self._txt_outcome_req.text().strip()
        s["outcome_req"] = [elem.strip() for elem in req_text.split(",") if elem.strip()]
        s["outcome_hint"] = self._txt_outcome_hint.text().strip()

    # ──────────────────────────────────────────
    # Step List Action Slots
    # ──────────────────────────────────────────

    def _on_step_selection_changed(self, row: int) -> None:
        if row < 0 or row == self._active_step_index:
            return
        self._save_inspector_to_active_step()
        self._active_step_index = row
        self._load_active_step_into_inspector()
        self._btn_move_up.setEnabled(self._active_step_index > 0)
        self._btn_move_down.setEnabled(0 <= self._active_step_index < len(self._steps) - 1)

    def _on_step_field_edited(self) -> None:
        self._save_inspector_to_active_step()
        # Update list item title
        if 0 <= self._active_step_index < self._list_steps.count():
            order = self._active_step_index + 1
            title = self._txt_step_title.text().strip() or f"Step {order}"
            step_id = self._txt_step_id.text().strip() or f"step-{order}"
            self._list_steps.item(self._active_step_index).setText(f"#{order}: {title} ({step_id})")
        self._mark_dirty()

    def _on_add_step_clicked(self) -> None:
        self._save_inspector_to_active_step()
        new_order = len(self._steps) + 1
        new_step = {
            "step_id": f"step-{new_order}",
            "order": new_order,
            "title": f"Step {new_order}",
            "instruction": "Describe the required learner action.",
            "hints": [],
            "outcome_desc": f"Step {new_order} target state is achieved",
            "outcome_req": [],
            "outcome_hint": "",
            "overlay_cues": [],
        }
        self._steps.append(new_step)
        self._active_step_index = len(self._steps) - 1
        self._refresh_steps_list()
        self._load_active_step_into_inspector()
        self._mark_dirty()

    def _on_duplicate_step_clicked(self) -> None:
        if not (0 <= self._active_step_index < len(self._steps)):
            return
        self._save_inspector_to_active_step()
        orig = self._steps[self._active_step_index]

        dup = dict(orig)
        dup["step_id"] = f"{orig['step_id']}-copy"
        dup["title"] = f"{orig['title']} (Copy)"
        dup["hints"] = list(orig.get("hints", []))
        dup["outcome_req"] = list(orig.get("outcome_req", []))
        dup["overlay_cues"] = list(orig.get("overlay_cues", []))

        insert_idx = self._active_step_index + 1
        self._steps.insert(insert_idx, dup)
        self._active_step_index = insert_idx
        self._refresh_steps_list()
        self._load_active_step_into_inspector()
        self._mark_dirty()

    def _on_delete_step_clicked(self) -> None:
        if len(self._steps) <= 1:
            QMessageBox.warning(
                self,
                "Cannot Delete Step",
                "A tutorial must contain at least one step.",
            )
            return

        if not (0 <= self._active_step_index < len(self._steps)):
            return

        del self._steps[self._active_step_index]
        if self._active_step_index >= len(self._steps):
            self._active_step_index = len(self._steps) - 1

        self._refresh_steps_list()
        self._load_active_step_into_inspector()
        self._mark_dirty()

    def _on_move_up_clicked(self) -> None:
        idx = self._active_step_index
        if idx <= 0:
            return
        self._save_inspector_to_active_step()
        self._steps[idx], self._steps[idx - 1] = self._steps[idx - 1], self._steps[idx]
        self._active_step_index = idx - 1
        self._refresh_steps_list()
        self._load_active_step_into_inspector()
        self._mark_dirty()

    def _on_move_down_clicked(self) -> None:
        idx = self._active_step_index
        if idx >= len(self._steps) - 1:
            return
        self._save_inspector_to_active_step()
        self._steps[idx], self._steps[idx + 1] = self._steps[idx + 1], self._steps[idx]
        self._active_step_index = idx + 1
        self._refresh_steps_list()
        self._load_active_step_into_inspector()
        self._mark_dirty()

    # ──────────────────────────────────────────
    # Overlay Cue Selection & Preview
    # ──────────────────────────────────────────

    def _on_select_cue_on_screen_clicked(self) -> None:
        """Launch Phase 10.1 AuthoringCueDialog and add the created cue to the active step."""
        if not (0 <= self._active_step_index < len(self._steps)):
            return

        dm = self._controller.display_manager if self._controller else None
        hc = self._controller.accessibility_high_contrast if self._controller else False
        ts = self._controller.accessibility_text_scale if self._controller else 1.0

        dlg = AuthoringCueDialog(
            display_manager=dm,
            high_contrast=hc,
            text_scale=ts,
            parent=self,
        )

        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_hint: OverlayHint | None = None
            if dlg.created_highlight is not None:
                h = dlg.created_highlight
                new_hint = OverlayHint(
                    kind=OverlayElementType.HIGHLIGHT,
                    label=h.label,
                    color=h.color,
                    bounds=CaptureRegion(
                        x=h.x,
                        y=h.y,
                        width=h.width,
                        height=h.height,
                        allow_negative=h.allow_negative,
                    ),
                )
            elif dlg.created_arrow is not None:
                a = dlg.created_arrow
                new_hint = OverlayHint(
                    kind=OverlayElementType.ARROW,
                    label=a.label,
                    color=a.color,
                    start_x=a.start_x,
                    start_y=a.start_y,
                    end_x=a.end_x,
                    end_y=a.end_y,
                )

            if new_hint is not None:
                cues = self._steps[self._active_step_index].setdefault("overlay_cues", [])
                cues.append(new_hint)
                self._refresh_cues_list()
                self._mark_dirty()

    def _on_remove_cue_clicked(self) -> None:
        row = self._list_cues.currentRow()
        if row < 0 or not (0 <= self._active_step_index < len(self._steps)):
            return

        cues = self._steps[self._active_step_index].get("overlay_cues", [])
        if 0 <= row < len(cues):
            del cues[row]
            self._refresh_cues_list()
            self._mark_dirty()

    def _on_preview_cues_clicked(self) -> None:
        """Temporarily display the active step's overlay cues for learner-accurate preview."""
        if not (0 <= self._active_step_index < len(self._steps)):
            return

        cues = self._steps[self._active_step_index].get("overlay_cues", [])
        if not cues:
            QMessageBox.information(
                self,
                "No Cues to Preview",
                "This step has no visual overlay cues defined.",
            )
            return

        # Determine target display bounds
        bounds = OverlayBounds(x=0, y=0, width=1920, height=1080)
        if self._controller and self._controller.display_manager:
            active_disp = self._controller.display_manager.active_display
            bounds = OverlayBounds(
                x=active_disp.x,
                y=active_disp.y,
                width=active_disp.width,
                height=active_disp.height,
            )

        hc = self._controller.accessibility_high_contrast if self._controller else False
        ts = self._controller.accessibility_text_scale if self._controller else 1.0

        if self._preview_overlay is not None:
            self._preview_overlay.close()

        self._preview_overlay = GuidanceOverlayWidget(
            screen_bounds=bounds,
            high_contrast=hc,
            text_scale=ts,
        )

        try:
            self._preview_overlay.apply_hints(cues)
            self._preview_overlay.show()
            # Auto-dismiss after 3.5 seconds
            QTimer.singleShot(3500, self._preview_overlay.close)
            self._lbl_status.setText("Displaying visual cue preview (3.5s auto-dismiss)...")
        except Exception as exc:
            QMessageBox.warning(self, "Preview Error", f"Failed to render preview: {exc}")

    # ──────────────────────────────────────────
    # Validation & File Persistence
    # ──────────────────────────────────────────

    def build_task_definition(self) -> TaskDefinition:
        """Validate all current inputs and construct an immutable TaskDefinition instance."""
        self._save_inspector_to_active_step()

        task_id = self._txt_task_id.text().strip()
        if not task_id:
            raise TaskValidationError("Task ID cannot be empty.")

        # Guard against reserved built-in collisions
        if task_id in BUILTIN_TASK_IDS:
            reserved = ", ".join(sorted(BUILTIN_TASK_IDS))
            raise TaskValidationError(
                f"Task ID '{task_id}' collides with a reserved built-in tutorial ({reserved})."
            )

        title = self._txt_title.text().strip()
        if not title:
            raise TaskValidationError("Tutorial title cannot be empty.")

        app = self._txt_application.text().strip()
        if not app:
            raise TaskValidationError("Target application cannot be empty.")

        goal = self._txt_goal.text().strip()
        if not goal:
            raise TaskValidationError("Learning goal cannot be empty.")

        version = self._txt_version.text().strip() or "1.0"

        if len(self._steps) == 0:
            raise TaskValidationError("A tutorial requires at least one step.")

        validated_steps: list[TaskStep] = []
        seen_ids: set[str] = set()

        for idx, s in enumerate(self._steps):
            order = idx + 1
            sid = str(s.get("step_id", "")).strip()
            if not sid:
                raise TaskValidationError(f"Step {order} is missing a step_id.")
            if sid in seen_ids:
                raise TaskValidationError(f"Duplicate step_id '{sid}' in step {order}.")
            seen_ids.add(sid)

            stitle = str(s.get("title", "")).strip()
            if not stitle:
                raise TaskValidationError(f"Step {order} ('{sid}') is missing a title.")

            sinst = str(s.get("instruction", "")).strip()
            if not sinst:
                raise TaskValidationError(f"Step {order} ('{sid}') is missing instruction text.")

            desc = str(s.get("outcome_desc", "")).strip()
            if not desc:
                raise TaskValidationError(
                    f"Step {order} ('{sid}') is missing an expected outcome description."
                )

            req = tuple(s.get("outcome_req", []))
            vhint = str(s.get("outcome_hint", "")).strip()
            outcome = ExpectedOutcome(
                description=desc,
                required_elements=req,
                verification_hint=vhint,
            )

            hints = tuple(s.get("hints", []))
            cues = tuple(s.get("overlay_cues", []))

            validated_steps.append(
                TaskStep(
                    step_id=sid,
                    order=order,
                    title=stitle,
                    instruction=sinst,
                    expected_outcome=outcome,
                    hints=hints,
                    overlay_cues=cues,
                )
            )

        return TaskDefinition(
            task_id=task_id,
            version=version,
            title=title,
            application=app,
            goal=goal,
            steps=tuple(validated_steps),
        )

    def validate_current(self) -> tuple[bool, str]:
        """Check the current editor state against TaskDefinition schema."""
        try:
            task = self.build_task_definition()
            return True, f"Valid: '{task.title}' ({len(task.steps)} steps verified)."
        except TaskValidationError as exc:
            return False, str(exc)

    def save_file(self, dest_path: Path | str) -> bool:
        """Safely save task definition to JSON file using atomic write."""
        try:
            task = self.build_task_definition()
        except TaskValidationError as exc:
            QMessageBox.critical(self, "Validation Error", f"Cannot save tutorial:\n\n{exc}")
            return False

        try:
            saved_path = save_task_definition_to_file(task, dest_path, overwrite=True)
            self._current_file_path = saved_path
            self._lbl_filepath.setText(f"File: {saved_path.name}")
            self._is_dirty = False
            self._lbl_status.setText(f"Saved '{task.task_id}' to {saved_path.name} successfully.")
            return True
        except Exception as exc:
            QMessageBox.critical(self, "Save Error", f"Failed to save tutorial JSON:\n\n{exc}")
            return False

    def open_file(self, source_path: Path | str) -> bool:
        """Open and parse a tutorial JSON file strictly into the editor."""
        path = Path(source_path).resolve()
        if not path.is_file():
            QMessageBox.critical(self, "File Not Found", f"File '{path}' does not exist.")
            return False

        try:
            task = load_task_definition_from_file(path)
            self.load_task(task)
            self._current_file_path = path
            self._lbl_filepath.setText(f"File: {path.name}")
            self._is_dirty = False
            self._lbl_status.setText(f"Opened '{path.name}'.")
            return True
        except Exception as exc:
            QMessageBox.critical(self, "Open Error", f"Failed to open tutorial JSON:\n\n{exc}")
            return False

    # ──────────────────────────────────────────
    # Button Slots
    # ──────────────────────────────────────────

    def _on_new_clicked(self) -> None:
        self.new_task()

    def _on_open_clicked(self) -> None:
        if self._is_dirty and not self._prompt_discard_unsaved():
            return

        file_name, _ = QFileDialog.getOpenFileName(
            self,
            "Open Tutorial JSON",
            "",
            "Tutorial JSON Files (*.json);;All Files (*)",
        )
        if file_name:
            self.open_file(file_name)

    def _on_save_clicked(self) -> None:
        if self._current_file_path is not None:
            self.save_file(self._current_file_path)
        else:
            self._on_save_as_clicked()

    def _on_save_as_clicked(self) -> None:
        task_id = self._txt_task_id.text().strip() or "tutorial"
        default_name = f"{task_id}.json"

        file_name, _ = QFileDialog.getSaveFileName(
            self,
            "Save Tutorial JSON",
            default_name,
            "Tutorial JSON Files (*.json)",
        )
        if file_name:
            self.save_file(file_name)

    def _on_validate_clicked(self) -> None:
        is_valid, msg = self.validate_current()
        if is_valid:
            QMessageBox.information(self, "Tutorial Schema Valid", msg)
            self._lbl_status.setText(msg)
        else:
            QMessageBox.warning(self, "Tutorial Schema Invalid", f"Validation Failed:\n\n{msg}")
            self._lbl_status.setText(f"Validation failed: {msg}")

    @Slot()
    def _on_project_studio_clicked(self) -> None:
        """Open the Plugin Project Studio dialog."""
        from octa.desktop.plugin_project_dialog import PluginProjectDialog

        dlg = PluginProjectDialog(controller=self._controller, parent=self)
        dlg.exec()

    def _mark_dirty(self) -> None:
        self._is_dirty = True
        if self._current_file_path:
            self._lbl_filepath.setText(f"File: {self._current_file_path.name} *")
        else:
            self._lbl_filepath.setText("New Tutorial (Unsaved) *")

    def _prompt_discard_unsaved(self) -> bool:
        res = QMessageBox.question(
            self,
            "Unsaved Changes",
            "You have unsaved changes in this tutorial. Discard changes?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        return res == QMessageBox.StandardButton.Yes

    def closeEvent(self, event: Any) -> None:
        if self._is_dirty and not self._prompt_discard_unsaved():
            event.ignore()
            return
        if self._preview_overlay is not None:
            self._preview_overlay.close()
        event.accept()
