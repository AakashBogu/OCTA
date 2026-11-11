"""User testing and feedback loop models and collection for OCTA tutorials (Phase 6.0).

Enables users to provide structured feedback (star rating, difficulty rating,
and free-form comments) on tutorial steps and complete sessions, while recording
privacy-safe session metrics (elapsed time, hint counts, AI verifications vs manual
completions) without persisting screenshots, credentials, or sensitive inputs.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self

from octa.intelligence.exceptions import TaskValidationError

logger = logging.getLogger("octa.tutorials.feedback")

VALID_DIFFICULTIES: frozenset[str] = frozenset({"easy", "just_right", "hard"})
MAX_COMMENT_LENGTH: int = 1000


@dataclass(frozen=True)
class TutorialFeedback:
    """User-submitted rating and qualitative feedback for a tutorial or step."""

    task_id: str
    step_id: str
    rating: int  # 1 to 5 stars
    difficulty: str = "just_right"  # "easy" | "just_right" | "hard"
    comment: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, str) or not self.task_id.strip():
            raise TaskValidationError("TutorialFeedback task_id must be a non-empty string.")
        if not isinstance(self.step_id, str) or not self.step_id.strip():
            raise TaskValidationError("TutorialFeedback step_id must be a non-empty string.")

        if (
            isinstance(self.rating, bool)
            or not isinstance(self.rating, int)
            or self.rating < 1
            or self.rating > 5
        ):
            raise TaskValidationError(
                f"TutorialFeedback rating must be an integer between 1 and 5, got {self.rating!r}."
            )

        diff_clean = self.difficulty.strip().lower() if isinstance(self.difficulty, str) else ""
        if diff_clean not in VALID_DIFFICULTIES:
            valid_list = ", ".join(sorted(VALID_DIFFICULTIES))
            raise TaskValidationError(
                f"TutorialFeedback difficulty must be one of {{{valid_list}}}, "
                f"got {self.difficulty!r}."
            )

        if not isinstance(self.comment, str):
            raise TaskValidationError("TutorialFeedback comment must be a string.")
        cleaned_comment = self.comment.strip()
        if len(cleaned_comment) > MAX_COMMENT_LENGTH:
            cleaned_comment = cleaned_comment[:MAX_COMMENT_LENGTH]

        object.__setattr__(self, "task_id", self.task_id.strip())
        object.__setattr__(self, "step_id", self.step_id.strip())
        object.__setattr__(self, "difficulty", diff_clean)
        object.__setattr__(self, "comment", cleaned_comment)

    def to_dict(self) -> dict[str, Any]:
        """Serialize feedback to a JSON-compatible dictionary."""
        return {
            "task_id": self.task_id,
            "step_id": self.step_id,
            "rating": self.rating,
            "difficulty": self.difficulty,
            "comment": self.comment,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Construct TutorialFeedback from a dictionary."""
        if not isinstance(data, dict):
            raise TaskValidationError("TutorialFeedback data must be a dictionary.")
        return cls(
            task_id=data.get("task_id", ""),
            step_id=data.get("step_id", ""),
            rating=data.get("rating", 0),
            difficulty=data.get("difficulty", "just_right"),
            comment=data.get("comment", ""),
            timestamp=data.get("timestamp", datetime.now(UTC).isoformat()),
        )


@dataclass(frozen=True)
class TutorialSessionMetrics:
    """Quantitative session metrics tracking user journey and subsystem interaction."""

    task_id: str
    start_time: str
    end_time: str | None = None
    duration_seconds: float = 0.0
    total_steps: int = 0
    completed_steps: int = 0
    hints_requested: int = 0
    ai_verifications_attempted: int = 0
    ai_verifications_passed: int = 0
    manual_completions: int = 0
    overlay_toggles: int = 0
    completed: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialize metrics to a JSON-compatible dictionary."""
        return {
            "task_id": self.task_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_seconds": round(self.duration_seconds, 2),
            "total_steps": self.total_steps,
            "completed_steps": self.completed_steps,
            "hints_requested": self.hints_requested,
            "ai_verifications_attempted": self.ai_verifications_attempted,
            "ai_verifications_passed": self.ai_verifications_passed,
            "manual_completions": self.manual_completions,
            "overlay_toggles": self.overlay_toggles,
            "completed": self.completed,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Construct TutorialSessionMetrics from a dictionary."""
        if not isinstance(data, dict):
            raise TaskValidationError("TutorialSessionMetrics data must be a dictionary.")
        return cls(
            task_id=str(data.get("task_id", "")),
            start_time=str(data.get("start_time", "")),
            end_time=data.get("end_time"),
            duration_seconds=float(data.get("duration_seconds", 0.0)),
            total_steps=int(data.get("total_steps", 0)),
            completed_steps=int(data.get("completed_steps", 0)),
            hints_requested=int(data.get("hints_requested", 0)),
            ai_verifications_attempted=int(data.get("ai_verifications_attempted", 0)),
            ai_verifications_passed=int(data.get("ai_verifications_passed", 0)),
            manual_completions=int(data.get("manual_completions", 0)),
            overlay_toggles=int(data.get("overlay_toggles", 0)),
            completed=bool(data.get("completed", False)),
        )


class FeedbackCollector:
    """Manages recording and retrieval of user feedback and session metrics."""

    def __init__(self, *, storage_dir: Path | None = None) -> None:
        self._storage_dir: Path | None = Path(storage_dir) if storage_dir is not None else None
        self._feedback_records: list[TutorialFeedback] = []
        self._session_records: list[TutorialSessionMetrics] = []

    @property
    def storage_dir(self) -> Path | None:
        """Directory where feedback and metrics are optionally persisted."""
        return self._storage_dir

    def record_feedback(self, feedback: TutorialFeedback) -> None:
        """Store a user feedback entry in memory and persist if storage_dir is configured."""
        if not isinstance(feedback, TutorialFeedback):
            raise TaskValidationError("Expected TutorialFeedback instance.")
        self._feedback_records.append(feedback)
        logger.info(
            "User feedback recorded for %s/%s: rating=%d, difficulty=%s.",
            feedback.task_id,
            feedback.step_id,
            feedback.rating,
            feedback.difficulty,
        )
        self._maybe_persist_feedback()

    def get_feedback(self, task_id: str | None = None) -> tuple[TutorialFeedback, ...]:
        """Return recorded feedback entries, optionally filtered by task_id."""
        if task_id is None:
            return tuple(self._feedback_records)
        cleaned = task_id.strip()
        return tuple(f for f in self._feedback_records if f.task_id == cleaned)

    def record_session_metrics(self, metrics: TutorialSessionMetrics) -> None:
        """Store a session metrics entry in memory and persist if storage_dir is configured."""
        if not isinstance(metrics, TutorialSessionMetrics):
            raise TaskValidationError("Expected TutorialSessionMetrics instance.")
        self._session_records.append(metrics)
        logger.info(
            "Session metrics recorded for %s: completed=%s, duration=%.1fs, "
            "hints=%d, verifications=%d.",
            metrics.task_id,
            metrics.completed,
            metrics.duration_seconds,
            metrics.hints_requested,
            metrics.ai_verifications_attempted,
        )
        self._maybe_persist_metrics()

    def get_session_metrics(self, task_id: str | None = None) -> tuple[TutorialSessionMetrics, ...]:
        """Return recorded session metrics, optionally filtered by task_id."""
        if task_id is None:
            return tuple(self._session_records)
        cleaned = task_id.strip()
        return tuple(m for m in self._session_records if m.task_id == cleaned)

    def clear(self) -> None:
        """Clear all in-memory feedback and metrics records."""
        self._feedback_records.clear()
        self._session_records.clear()

    def export_summary(self) -> dict[str, Any]:
        """Export an aggregated summary dictionary of all feedback and sessions."""
        ratings = [f.rating for f in self._feedback_records]
        avg_rating = round(sum(ratings) / len(ratings), 2) if ratings else 0.0
        return {
            "total_feedback_count": len(self._feedback_records),
            "average_rating": avg_rating,
            "total_sessions_count": len(self._session_records),
            "completed_sessions_count": sum(1 for s in self._session_records if s.completed),
            "feedback": [f.to_dict() for f in self._feedback_records],
            "sessions": [s.to_dict() for s in self._session_records],
        }

    def _maybe_persist_feedback(self) -> None:
        if self._storage_dir is None:
            return
        try:
            self._storage_dir.mkdir(parents=True, exist_ok=True)
            path = self._storage_dir / "tutorial_feedback.json"
            data = [f.to_dict() for f in self._feedback_records]
            path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        except OSError as exc:
            logger.warning("Failed to persist tutorial feedback: %s", exc)

    def _maybe_persist_metrics(self) -> None:
        if self._storage_dir is None:
            return
        try:
            self._storage_dir.mkdir(parents=True, exist_ok=True)
            path = self._storage_dir / "tutorial_session_metrics.json"
            data = [s.to_dict() for s in self._session_records]
            path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        except OSError as exc:
            logger.warning("Failed to persist tutorial session metrics: %s", exc)
