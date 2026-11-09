"""Task definitions, steps, expected outcomes, and progress result schemas for OCTA."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Self

from octa.intelligence.capture import CaptureRegion
from octa.intelligence.exceptions import CaptureRegionError, TaskValidationError


class ProgressStatus(StrEnum):
    """Structured verification outcome for a task step."""

    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    UNCERTAIN = "uncertain"

    def __str__(self) -> str:
        return self.value


VALID_PROGRESS_STATUSES: frozenset[str] = frozenset(s.value for s in ProgressStatus)


def is_valid_progress_status(status: str) -> bool:
    """Return True if status is a recognized ProgressStatus string."""
    return isinstance(status, str) and status in VALID_PROGRESS_STATUSES


class OverlayElementType(StrEnum):
    """Supported visual overlay element types."""

    HIGHLIGHT = "highlight"
    ARROW = "arrow"

    def __str__(self) -> str:
        return self.value


VALID_OVERLAY_TYPES: frozenset[str] = frozenset(t.value for t in OverlayElementType)


def _require_non_empty_str(value: Any, field_name: str) -> str:
    """Validate that value is a non-empty, non-whitespace string."""
    if not isinstance(value, str) or not value.strip():
        raise TaskValidationError(f"Field '{field_name}' must be a non-empty string.")
    return value.strip()


def _require_optional_str(value: Any, field_name: str) -> str:
    """Validate that value is a string (may be empty)."""
    if not isinstance(value, str):
        raise TaskValidationError(f"Field '{field_name}' must be a string.")
    return value.strip()


def _require_str_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    """Validate a sequence of non-empty strings and convert to a tuple."""
    if not isinstance(value, (list, tuple)):
        raise TaskValidationError(f"Field '{field_name}' must be a list or tuple of strings.")
    result: list[str] = []
    for idx, item in enumerate(value):
        result.append(_require_non_empty_str(item, f"{field_name}[{idx}]"))
    return tuple(result)


def _require_int(value: Any, field_name: str, *, min_value: int | None = None) -> int:
    """Validate that value is an integer (not bool) and meets minimum constraint."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise TaskValidationError(f"Field '{field_name}' must be an integer, got {value!r}.")
    if min_value is not None and value < min_value:
        raise TaskValidationError(
            f"Field '{field_name}' must be at least {min_value}, got {value}."
        )
    return value


@dataclass(frozen=True)
class OverlayHint:
    """Validated specification for a highlight box or directional arrow in a task or result."""

    kind: OverlayElementType
    label: str = ""
    color: str = ""
    bounds: CaptureRegion | None = None
    start_x: int | None = None
    start_y: int | None = None
    end_x: int | None = None
    end_y: int | None = None

    def __post_init__(self) -> None:
        if isinstance(self.kind, str) and not isinstance(self.kind, OverlayElementType):
            if self.kind not in VALID_OVERLAY_TYPES:
                raise TaskValidationError(
                    f"Field 'kind' must be one of {sorted(VALID_OVERLAY_TYPES)}, got {self.kind!r}."
                )
            object.__setattr__(self, "kind", OverlayElementType(self.kind))
        elif not isinstance(self.kind, OverlayElementType):
            raise TaskValidationError(
                f"Field 'kind' must be an OverlayElementType, got {self.kind!r}."
            )

        object.__setattr__(self, "label", _require_optional_str(self.label, "label"))
        object.__setattr__(self, "color", _require_optional_str(self.color, "color"))

        if self.kind == OverlayElementType.HIGHLIGHT:
            if not isinstance(self.bounds, CaptureRegion):
                raise TaskValidationError(
                    "Highlight OverlayHint requires a valid 'bounds' CaptureRegion."
                )
            if any(
                coord is not None for coord in (self.start_x, self.start_y, self.end_x, self.end_y)
            ):
                raise TaskValidationError(
                    "Highlight OverlayHint must not define arrow coordinates."
                )
        elif self.kind == OverlayElementType.ARROW:
            if self.bounds is not None:
                raise TaskValidationError("Arrow OverlayHint must not define 'bounds'.")
            sx = _require_int(self.start_x, "start_x", min_value=0)
            sy = _require_int(self.start_y, "start_y", min_value=0)
            ex = _require_int(self.end_x, "end_x", min_value=0)
            ey = _require_int(self.end_y, "end_y", min_value=0)
            if sx == ex and sy == ey:
                raise TaskValidationError(
                    "Arrow OverlayHint start and end coordinates must not be identical."
                )

    def to_dict(self) -> dict[str, Any]:
        """Serialize OverlayHint to a JSON-compatible dictionary."""
        result: dict[str, Any] = {
            "kind": self.kind.value,
            "label": self.label,
            "color": self.color,
        }
        if self.kind == OverlayElementType.HIGHLIGHT and self.bounds is not None:
            result["bounds"] = self.bounds.to_dict()
        elif self.kind == OverlayElementType.ARROW:
            result["start_x"] = self.start_x
            result["start_y"] = self.start_y
            result["end_x"] = self.end_x
            result["end_y"] = self.end_y
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create and validate an OverlayHint from a dictionary."""
        if not isinstance(data, dict):
            raise TaskValidationError("OverlayHint must be a dictionary.")

        allowed_keys = {
            "kind",
            "label",
            "color",
            "bounds",
            "start_x",
            "start_y",
            "end_x",
            "end_y",
        }
        extra = set(data.keys()) - allowed_keys
        if extra:
            raise TaskValidationError(f"Unexpected fields in OverlayHint: {sorted(extra)}")
        if "kind" not in data:
            raise TaskValidationError("Missing required field 'kind' in OverlayHint.")

        kind_raw = data["kind"]
        if not isinstance(kind_raw, str) or kind_raw not in VALID_OVERLAY_TYPES:
            raise TaskValidationError(
                f"Field 'kind' must be one of {sorted(VALID_OVERLAY_TYPES)}, got {kind_raw!r}."
            )

        bounds_raw = data.get("bounds")
        bounds: CaptureRegion | None = None
        if bounds_raw is not None:
            try:
                bounds = (
                    bounds_raw
                    if isinstance(bounds_raw, CaptureRegion)
                    else CaptureRegion.from_dict(bounds_raw)
                )
            except CaptureRegionError as exc:
                raise TaskValidationError(f"Invalid OverlayHint bounds: {exc}") from exc

        return cls(
            kind=OverlayElementType(kind_raw),
            label=data.get("label", ""),
            color=data.get("color", ""),
            bounds=bounds,
            start_x=data.get("start_x"),
            start_y=data.get("start_y"),
            end_x=data.get("end_x"),
            end_y=data.get("end_y"),
        )


@dataclass(frozen=True)
class ExpectedOutcome:
    """Observable criteria defining what constitutes completion of a task step."""

    description: str
    required_elements: tuple[str, ...] = ()
    verification_hint: str = ""
    target_region: CaptureRegion | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "description",
            _require_non_empty_str(self.description, "description"),
        )
        object.__setattr__(
            self,
            "required_elements",
            _require_str_tuple(self.required_elements, "required_elements"),
        )
        object.__setattr__(
            self,
            "verification_hint",
            _require_optional_str(self.verification_hint, "verification_hint"),
        )
        if self.target_region is not None and not isinstance(self.target_region, CaptureRegion):
            raise TaskValidationError(
                "Field 'target_region' must be a CaptureRegion instance or None."
            )

    def to_dict(self) -> dict[str, Any]:
        """Serialize ExpectedOutcome to a dictionary."""
        return {
            "description": self.description,
            "required_elements": list(self.required_elements),
            "verification_hint": self.verification_hint,
            "target_region": (
                self.target_region.to_dict() if self.target_region is not None else None
            ),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Construct and validate ExpectedOutcome from a dictionary."""
        if not isinstance(data, dict):
            raise TaskValidationError("ExpectedOutcome must be a dictionary.")

        allowed_keys = {
            "description",
            "required_elements",
            "verification_hint",
            "target_region",
        }
        extra = set(data.keys()) - allowed_keys
        if extra:
            raise TaskValidationError(f"Unexpected fields in ExpectedOutcome: {sorted(extra)}")

        if "description" not in data:
            raise TaskValidationError("Missing required field 'description' in ExpectedOutcome.")

        target_raw = data.get("target_region")
        target_region: CaptureRegion | None = None
        if target_raw is not None:
            try:
                target_region = (
                    target_raw
                    if isinstance(target_raw, CaptureRegion)
                    else CaptureRegion.from_dict(target_raw)
                )
            except CaptureRegionError as exc:
                raise TaskValidationError(f"Invalid target_region: {exc}") from exc

        return cls(
            description=data["description"],
            required_elements=tuple(data.get("required_elements", ())),
            verification_hint=data.get("verification_hint", ""),
            target_region=target_region,
        )


@dataclass(frozen=True)
class TaskStep:
    """Single actionable step within a tutorial task."""

    step_id: str
    order: int
    title: str
    instruction: str
    expected_outcome: ExpectedOutcome
    hints: tuple[str, ...] = ()
    overlay_cues: tuple[OverlayHint, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "step_id", _require_non_empty_str(self.step_id, "step_id"))
        object.__setattr__(self, "order", _require_int(self.order, "order", min_value=1))
        object.__setattr__(self, "title", _require_non_empty_str(self.title, "title"))
        object.__setattr__(
            self, "instruction", _require_non_empty_str(self.instruction, "instruction")
        )
        if not isinstance(self.expected_outcome, ExpectedOutcome):
            raise TaskValidationError(
                "Field 'expected_outcome' must be an ExpectedOutcome instance."
            )
        object.__setattr__(self, "hints", _require_str_tuple(self.hints, "hints"))

        if not isinstance(self.overlay_cues, (list, tuple)):
            raise TaskValidationError("Field 'overlay_cues' must be a list or tuple.")
        validated_cues: list[OverlayHint] = []
        for idx, cue in enumerate(self.overlay_cues):
            if not isinstance(cue, OverlayHint):
                raise TaskValidationError(f"Field 'overlay_cues[{idx}]' must be an OverlayHint.")
            validated_cues.append(cue)
        object.__setattr__(self, "overlay_cues", tuple(validated_cues))

    def to_dict(self) -> dict[str, Any]:
        """Serialize TaskStep to a dictionary."""
        return {
            "step_id": self.step_id,
            "order": self.order,
            "title": self.title,
            "instruction": self.instruction,
            "expected_outcome": self.expected_outcome.to_dict(),
            "hints": list(self.hints),
            "overlay_cues": [cue.to_dict() for cue in self.overlay_cues],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Construct and validate a TaskStep from a dictionary."""
        if not isinstance(data, dict):
            raise TaskValidationError("TaskStep must be a dictionary.")

        allowed_keys = {
            "step_id",
            "order",
            "title",
            "instruction",
            "expected_outcome",
            "hints",
            "overlay_cues",
        }
        extra = set(data.keys()) - allowed_keys
        if extra:
            raise TaskValidationError(f"Unexpected fields in TaskStep: {sorted(extra)}")

        required_keys = {"step_id", "order", "title", "instruction", "expected_outcome"}
        missing = required_keys - set(data.keys())
        if missing:
            raise TaskValidationError(f"Missing required fields in TaskStep: {sorted(missing)}")

        outcome_raw = data["expected_outcome"]
        outcome = (
            outcome_raw
            if isinstance(outcome_raw, ExpectedOutcome)
            else ExpectedOutcome.from_dict(outcome_raw)
        )

        raw_cues = data.get("overlay_cues", ())
        if not isinstance(raw_cues, (list, tuple)):
            raise TaskValidationError("Field 'overlay_cues' must be a list or tuple.")
        cues = tuple(
            c if isinstance(c, OverlayHint) else OverlayHint.from_dict(c) for c in raw_cues
        )

        return cls(
            step_id=data["step_id"],
            order=data["order"],
            title=data["title"],
            instruction=data["instruction"],
            expected_outcome=outcome,
            hints=tuple(data.get("hints", ())),
            overlay_cues=cues,
        )


@dataclass(frozen=True)
class TaskDefinition:
    """Validated multi-step tutorial task definition."""

    task_id: str
    title: str
    application: str
    goal: str
    steps: tuple[TaskStep, ...]
    version: str = "1.0"

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty_str(self.task_id, "task_id"))
        object.__setattr__(self, "title", _require_non_empty_str(self.title, "title"))
        object.__setattr__(
            self, "application", _require_non_empty_str(self.application, "application")
        )
        object.__setattr__(self, "goal", _require_non_empty_str(self.goal, "goal"))
        object.__setattr__(self, "version", _require_non_empty_str(self.version, "version"))

        if not isinstance(self.steps, (list, tuple)) or len(self.steps) == 0:
            raise TaskValidationError("TaskDefinition requires a non-empty list/tuple of steps.")

        validated_steps: list[TaskStep] = []
        seen_ids: set[str] = set()
        for idx, step in enumerate(self.steps):
            if not isinstance(step, TaskStep):
                raise TaskValidationError(f"Field 'steps[{idx}]' must be a TaskStep instance.")
            if step.step_id in seen_ids:
                raise TaskValidationError(
                    f"Duplicate step_id '{step.step_id}' in TaskDefinition '{self.task_id}'."
                )
            seen_ids.add(step.step_id)
            expected_order = idx + 1
            if step.order != expected_order:
                raise TaskValidationError(
                    f"Step '{step.step_id}' order must be sequential ({expected_order}), "
                    f"got {step.order}."
                )
            validated_steps.append(step)

        object.__setattr__(self, "steps", tuple(validated_steps))

    def get_step(self, step_id: str) -> TaskStep:
        """Return the step with the given step_id, or raise TaskValidationError."""
        for step in self.steps:
            if step.step_id == step_id:
                return step
        raise TaskValidationError(f"Step '{step_id}' not found in task '{self.task_id}'.")

    def to_dict(self) -> dict[str, Any]:
        """Serialize TaskDefinition to a dictionary."""
        return {
            "task_id": self.task_id,
            "version": self.version,
            "title": self.title,
            "application": self.application,
            "goal": self.goal,
            "steps": [step.to_dict() for step in self.steps],
        }

    def to_json(self) -> str:
        """Serialize TaskDefinition to a UTF-8 JSON string."""
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Construct and validate a TaskDefinition from a dictionary."""
        if not isinstance(data, dict):
            raise TaskValidationError("TaskDefinition must be a dictionary.")

        allowed_keys = {"task_id", "version", "title", "application", "goal", "steps"}
        extra = set(data.keys()) - allowed_keys
        if extra:
            raise TaskValidationError(f"Unexpected fields in TaskDefinition: {sorted(extra)}")

        required_keys = {"task_id", "title", "application", "goal", "steps"}
        missing = required_keys - set(data.keys())
        if missing:
            raise TaskValidationError(
                f"Missing required fields in TaskDefinition: {sorted(missing)}"
            )

        raw_steps = data["steps"]
        if not isinstance(raw_steps, (list, tuple)) or len(raw_steps) == 0:
            raise TaskValidationError("Field 'steps' must be a non-empty list of steps.")

        steps = tuple(s if isinstance(s, TaskStep) else TaskStep.from_dict(s) for s in raw_steps)
        return cls(
            task_id=data["task_id"],
            version=data.get("version", "1.0"),
            title=data["title"],
            application=data["application"],
            goal=data["goal"],
            steps=steps,
        )

    @classmethod
    def from_json(cls, raw_json: str | bytes) -> Self:
        """Parse and validate a TaskDefinition from a JSON string or bytes."""
        if isinstance(raw_json, bytes):
            try:
                text = raw_json.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise TaskValidationError(f"Invalid UTF-8 in task JSON: {exc}") from exc
        elif isinstance(raw_json, str):
            text = raw_json
        else:
            raise TaskValidationError("Task JSON input must be str or bytes.")

        if not text.strip():
            raise TaskValidationError("Cannot parse empty task JSON.")

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise TaskValidationError(f"Malformed task JSON: {exc}") from exc

        return cls.from_dict(parsed)


@dataclass(frozen=True)
class GuidanceInstruction:
    """Structured natural-language guidance generated for a specific task step."""

    task_id: str
    step_id: str
    instruction_text: str
    summary: str = ""
    overlay_cues: tuple[OverlayHint, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty_str(self.task_id, "task_id"))
        object.__setattr__(self, "step_id", _require_non_empty_str(self.step_id, "step_id"))
        object.__setattr__(
            self,
            "instruction_text",
            _require_non_empty_str(self.instruction_text, "instruction_text"),
        )
        object.__setattr__(self, "summary", _require_optional_str(self.summary, "summary"))
        if not isinstance(self.overlay_cues, (list, tuple)):
            raise TaskValidationError("Field 'overlay_cues' must be a list or tuple.")
        cues = tuple(
            c if isinstance(c, OverlayHint) else OverlayHint.from_dict(c) for c in self.overlay_cues
        )
        object.__setattr__(self, "overlay_cues", cues)

    def to_dict(self) -> dict[str, Any]:
        """Serialize GuidanceInstruction to a dictionary."""
        return {
            "task_id": self.task_id,
            "step_id": self.step_id,
            "instruction_text": self.instruction_text,
            "summary": self.summary,
            "overlay_cues": [c.to_dict() for c in self.overlay_cues],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Construct and validate a GuidanceInstruction from a dictionary."""
        if not isinstance(data, dict):
            raise TaskValidationError("GuidanceInstruction must be a dictionary.")

        allowed_keys = {"task_id", "step_id", "instruction_text", "summary", "overlay_cues"}
        extra = set(data.keys()) - allowed_keys
        if extra:
            raise TaskValidationError(f"Unexpected fields in GuidanceInstruction: {sorted(extra)}")

        required = {"task_id", "step_id", "instruction_text"}
        missing = required - set(data.keys())
        if missing:
            raise TaskValidationError(
                f"Missing required fields in GuidanceInstruction: {sorted(missing)}"
            )

        raw_cues = data.get("overlay_cues", ())
        if not isinstance(raw_cues, (list, tuple)):
            raise TaskValidationError("Field 'overlay_cues' must be a list or tuple.")

        return cls(
            task_id=data["task_id"],
            step_id=data["step_id"],
            instruction_text=data["instruction_text"],
            summary=data.get("summary", ""),
            overlay_cues=tuple(
                c if isinstance(c, OverlayHint) else OverlayHint.from_dict(c) for c in raw_cues
            ),
        )


@dataclass(frozen=True)
class ProgressResult:
    """Structured progress verification result for a task step.

    Supports complete, incomplete, and uncertain statuses. Contradictory claims
    (such as status=complete when missing_elements are non-empty) are rejected.
    """

    task_id: str
    step_id: str
    status: ProgressStatus
    confidence: float
    reasoning: str
    next_instruction: str = ""
    observed_elements: tuple[str, ...] = ()
    missing_elements: tuple[str, ...] = ()
    overlay_cues: tuple[OverlayHint, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty_str(self.task_id, "task_id"))
        object.__setattr__(self, "step_id", _require_non_empty_str(self.step_id, "step_id"))

        if isinstance(self.status, str) and not isinstance(self.status, ProgressStatus):
            normalized = self.status.strip().lower()
            if not is_valid_progress_status(normalized):
                raise TaskValidationError(
                    f"Field 'status' must be one of {sorted(VALID_PROGRESS_STATUSES)}, "
                    f"got {self.status!r}."
                )
            object.__setattr__(self, "status", ProgressStatus(normalized))
        elif not isinstance(self.status, ProgressStatus):
            raise TaskValidationError(
                f"Field 'status' must be a ProgressStatus, got {self.status!r}."
            )

        if isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float)):
            raise TaskValidationError("Field 'confidence' must be a number between 0.0 and 1.0.")
        conf = float(self.confidence)
        if conf < 0.0 or conf > 1.0:
            raise TaskValidationError(
                f"Field 'confidence' must be between 0.0 and 1.0, got {conf}."
            )
        object.__setattr__(self, "confidence", conf)

        object.__setattr__(self, "reasoning", _require_non_empty_str(self.reasoning, "reasoning"))
        object.__setattr__(
            self,
            "next_instruction",
            _require_optional_str(self.next_instruction, "next_instruction"),
        )
        object.__setattr__(
            self,
            "observed_elements",
            _require_str_tuple(self.observed_elements, "observed_elements"),
        )
        object.__setattr__(
            self,
            "missing_elements",
            _require_str_tuple(self.missing_elements, "missing_elements"),
        )

        if not isinstance(self.overlay_cues, (list, tuple)):
            raise TaskValidationError("Field 'overlay_cues' must be a list or tuple.")
        cues = tuple(
            c if isinstance(c, OverlayHint) else OverlayHint.from_dict(c) for c in self.overlay_cues
        )
        object.__setattr__(self, "overlay_cues", cues)

        if self.status == ProgressStatus.COMPLETE and len(self.missing_elements) > 0:
            raise TaskValidationError(
                "ProgressResult cannot claim 'complete' when 'missing_elements' is non-empty."
            )

    @property
    def is_complete(self) -> bool:
        """Whether the step has been verified as complete."""
        return self.status == ProgressStatus.COMPLETE

    def to_dict(self) -> dict[str, Any]:
        """Serialize ProgressResult to a dictionary."""
        return {
            "task_id": self.task_id,
            "step_id": self.step_id,
            "status": self.status.value,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "next_instruction": self.next_instruction,
            "observed_elements": list(self.observed_elements),
            "missing_elements": list(self.missing_elements),
            "overlay_cues": [c.to_dict() for c in self.overlay_cues],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Construct and validate a ProgressResult from a dictionary."""
        if not isinstance(data, dict):
            raise TaskValidationError("ProgressResult must be a dictionary.")

        allowed_keys = {
            "task_id",
            "step_id",
            "status",
            "confidence",
            "reasoning",
            "next_instruction",
            "observed_elements",
            "missing_elements",
            "overlay_cues",
        }
        extra = set(data.keys()) - allowed_keys
        if extra:
            raise TaskValidationError(f"Unexpected fields in ProgressResult: {sorted(extra)}")

        required = {"task_id", "step_id", "status", "confidence", "reasoning"}
        missing = required - set(data.keys())
        if missing:
            raise TaskValidationError(
                f"Missing required fields in ProgressResult: {sorted(missing)}"
            )

        raw_cues = data.get("overlay_cues", ())
        if not isinstance(raw_cues, (list, tuple)):
            raise TaskValidationError("Field 'overlay_cues' must be a list or tuple.")

        return cls(
            task_id=data["task_id"],
            step_id=data["step_id"],
            status=data["status"],
            confidence=data["confidence"],
            reasoning=data["reasoning"],
            next_instruction=data.get("next_instruction", ""),
            observed_elements=tuple(data.get("observed_elements", ())),
            missing_elements=tuple(data.get("missing_elements", ())),
            overlay_cues=tuple(
                c if isinstance(c, OverlayHint) else OverlayHint.from_dict(c) for c in raw_cues
            ),
        )
