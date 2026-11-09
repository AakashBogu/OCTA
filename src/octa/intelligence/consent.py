"""Explicit user consent management before sending screen captures to remote services.

Binds user approval internally to the complete outbound request (image SHA-256
digest, exact destination endpoint URL digest, model, system and user prompts,
task/step context, and purpose). Displays the sanitized destination endpoint
(scheme, host, path) and indicates whether URL credentials, query parameters,
or a fragment are present with their values redacted ([REDACTED]), without
exposing raw URL secrets or any endpoint/request/component hash fingerprints in
the dialog, repr, str, logs, or errors.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Self
from urllib.parse import parse_qsl, urlparse, urlunparse

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from octa.intelligence.capture import CapturedImage, CaptureRegion
from octa.intelligence.exceptions import ConsentDeclinedError, ConsentError

logger = logging.getLogger("octa.intelligence.consent")


def sanitize_endpoint_for_display(endpoint_url: str) -> str:
    """Return a clean base URL without embedded credentials, query strings, or fragments."""
    if not isinstance(endpoint_url, str) or not endpoint_url.strip():
        raise ConsentError("Endpoint URL must be a non-empty string.")
    parsed = urlparse(endpoint_url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not parsed.hostname:
        raise ConsentError("Invalid endpoint URL for consent disclosure.")

    hostname = parsed.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    netloc = f"{hostname}:{parsed.port}" if parsed.port else hostname
    return urlunparse((parsed.scheme, netloc, parsed.path, "", "", ""))


def format_endpoint_for_disclosure(endpoint_url: str) -> str:
    """Format an endpoint URL with any credentials, query values, or fragments redacted.

    Never includes raw secret values or hash fingerprints of credentials, query
    values, or fragments.
    """
    base_clean = sanitize_endpoint_for_display(endpoint_url)
    raw = endpoint_url.strip()
    parsed = urlparse(raw)

    has_userinfo = (
        parsed.username is not None or parsed.password is not None or "@" in parsed.netloc
    )
    has_query = bool(parsed.query) or "?" in raw.split("#", 1)[0]
    has_fragment = bool(parsed.fragment) or "#" in raw

    if not has_userinfo and not has_query and not has_fragment:
        return base_clean

    hostname = parsed.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    host_port = f"{hostname}:{parsed.port}" if parsed.port else hostname

    netloc = f"[REDACTED]@{host_port}" if has_userinfo else host_port

    query_disclosure = ""
    if has_query:
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
        if pairs:
            query_disclosure = "&".join(f"{k}=[REDACTED]" for k, _ in pairs)
        else:
            query_disclosure = "[REDACTED]"

    fragment_disclosure = "[REDACTED]" if has_fragment else ""

    return urlunparse(
        (
            parsed.scheme,
            netloc,
            parsed.path,
            "",
            query_disclosure,
            fragment_disclosure,
        )
    )


def compute_outbound_request_sha256(
    *,
    purpose: str,
    endpoint_url: str,
    endpoint_sha256: str | None = None,
    model: str,
    task_title: str,
    step_title: str,
    system_prompt: str,
    user_prompt: str,
    image_sha256: str,
    image_width: int,
    image_height: int,
    image_byte_size: int,
    capture_source: str,
    capture_region: CaptureRegion,
) -> str:
    """Compute a deterministic SHA-256 hex digest of the complete outbound request."""
    resolved_endpoint_sha256 = (
        endpoint_sha256.lower()
        if endpoint_sha256
        else hashlib.sha256(endpoint_url.strip().encode("utf-8")).hexdigest()
    )
    canonical_payload = {
        "capture_region": capture_region.to_dict(),
        "capture_source": capture_source,
        "endpoint_sha256": resolved_endpoint_sha256,
        "endpoint_url": endpoint_url,
        "image_byte_size": image_byte_size,
        "image_height": image_height,
        "image_sha256": image_sha256.lower(),
        "image_width": image_width,
        "model": model,
        "purpose": purpose,
        "step_title": step_title,
        "system_prompt": system_prompt,
        "task_title": task_title,
        "user_prompt": user_prompt,
    }
    encoded = json.dumps(
        canonical_payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ConsentRequest:
    """Complete disclosure of the outbound request requiring user approval before transmission.

    Binds the exact raw endpoint URL internally via `endpoint_sha256` while
    sanitizing `endpoint_url` and `endpoint_disclosure` so URL credentials,
    query values, fragments, or their hash fingerprints never appear in the
    consent dialog, repr, str, logs, or errors.
    """

    purpose: str
    endpoint_url: str
    model: str
    task_title: str
    step_title: str
    image_width: int
    image_height: int
    image_byte_size: int
    capture_region: CaptureRegion
    capture_source: str
    image_sha256: str
    system_prompt: str = ""
    user_prompt: str = ""
    prompt_summary: str = ""
    endpoint_disclosure: str = ""
    has_url_credentials: bool = False
    query_parameter_keys: tuple[str, ...] = ()
    has_url_fragment: bool = False
    endpoint_sha256: str = field(default="", repr=False)
    preview_png_bytes: bytes = field(default=b"", repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.purpose, str) or not self.purpose.strip():
            raise ConsentError("ConsentRequest purpose must be a non-empty string.")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ConsentError("ConsentRequest model must be a non-empty string.")
        if not isinstance(self.task_title, str) or not self.task_title.strip():
            raise ConsentError("ConsentRequest task_title must be a non-empty string.")
        if not isinstance(self.step_title, str) or not self.step_title.strip():
            raise ConsentError("ConsentRequest step_title must be a non-empty string.")
        if not isinstance(self.system_prompt, str):
            raise ConsentError("ConsentRequest system_prompt must be a string.")
        if not isinstance(self.user_prompt, str):
            raise ConsentError("ConsentRequest user_prompt must be a string.")
        if not isinstance(self.prompt_summary, str):
            raise ConsentError("ConsentRequest prompt_summary must be a string.")

        effective_user_prompt = self.user_prompt.strip() or self.prompt_summary.strip()
        if not effective_user_prompt:
            raise ConsentError(
                "ConsentRequest requires a non-empty user_prompt (or prompt_summary)."
            )

        if not isinstance(self.image_sha256, str) or not self.image_sha256.strip():
            raise ConsentError("ConsentRequest image_sha256 must be a non-empty hex digest.")
        if not isinstance(self.capture_region, CaptureRegion):
            raise ConsentError("ConsentRequest capture_region must be a CaptureRegion.")
        if self.image_width <= 0 or self.image_height <= 0 or self.image_byte_size <= 0:
            raise ConsentError("ConsentRequest image dimensions and byte size must be positive.")

        if not isinstance(self.endpoint_url, str) or not self.endpoint_url.strip():
            raise ConsentError("Endpoint URL must be a non-empty string.")
        raw_endpoint = self.endpoint_url.strip()
        clean_endpoint = sanitize_endpoint_for_display(raw_endpoint)
        parsed = urlparse(raw_endpoint)

        has_creds = (
            bool(self.has_url_credentials)
            or parsed.username is not None
            or parsed.password is not None
            or "@" in parsed.netloc
        )
        has_query = bool(parsed.query) or "?" in raw_endpoint.split("#", 1)[0]
        if self.query_parameter_keys:
            query_keys = tuple(str(k) for k in self.query_parameter_keys)
        elif has_query:
            pairs = parse_qsl(parsed.query, keep_blank_values=True)
            query_keys = tuple(k for k, _ in pairs) if pairs else ("[REDACTED]",)
        else:
            query_keys = ()
        has_frag = bool(self.has_url_fragment) or bool(parsed.fragment) or "#" in raw_endpoint

        # Bind the exact raw endpoint URL internally before stripping query/userinfo/fragment
        exact_endpoint_sha256 = (
            self.endpoint_sha256.strip().lower()
            if self.endpoint_sha256 and self.endpoint_sha256.strip()
            else hashlib.sha256(raw_endpoint.encode("utf-8")).hexdigest()
        )
        disclosed_endpoint = (
            self.endpoint_disclosure.strip()
            if self.endpoint_disclosure and self.endpoint_disclosure.strip()
            else format_endpoint_for_disclosure(raw_endpoint)
        )

        object.__setattr__(self, "endpoint_url", clean_endpoint)
        object.__setattr__(self, "endpoint_sha256", exact_endpoint_sha256)
        object.__setattr__(self, "endpoint_disclosure", disclosed_endpoint)
        object.__setattr__(self, "has_url_credentials", has_creds)
        object.__setattr__(self, "query_parameter_keys", query_keys)
        object.__setattr__(self, "has_url_fragment", has_frag)
        object.__setattr__(self, "purpose", self.purpose.strip())
        object.__setattr__(self, "model", self.model.strip())
        object.__setattr__(self, "task_title", self.task_title.strip())
        object.__setattr__(self, "step_title", self.step_title.strip())
        object.__setattr__(self, "system_prompt", self.system_prompt.strip())
        object.__setattr__(self, "user_prompt", effective_user_prompt)
        object.__setattr__(
            self,
            "prompt_summary",
            self.prompt_summary.strip() or effective_user_prompt,
        )
        object.__setattr__(self, "image_sha256", self.image_sha256.strip().lower())

    def __repr__(self) -> str:
        return (
            f"ConsentRequest(purpose={self.purpose!r}, endpoint_url={self.endpoint_url!r}, "
            f"endpoint_disclosure={self.endpoint_disclosure!r}, "
            f"has_url_credentials={self.has_url_credentials}, "
            f"query_parameter_keys={self.query_parameter_keys!r}, "
            f"has_url_fragment={self.has_url_fragment}, "
            f"model={self.model!r}, task_title={self.task_title!r}, "
            f"step_title={self.step_title!r}, image_width={self.image_width}, "
            f"image_height={self.image_height}, image_byte_size={self.image_byte_size}, "
            f"image_sha256_prefix={self.image_sha256[:12]!r})"
        )

    def __str__(self) -> str:
        return self.__repr__()

    @property
    def request_sha256(self) -> str:
        """Internal deterministic SHA-256 digest binding all fields of the outbound request."""
        return compute_outbound_request_sha256(
            purpose=self.purpose,
            endpoint_url=self.endpoint_url,
            endpoint_sha256=self.endpoint_sha256,
            model=self.model,
            task_title=self.task_title,
            step_title=self.step_title,
            system_prompt=self.system_prompt,
            user_prompt=self.user_prompt,
            image_sha256=self.image_sha256,
            image_width=self.image_width,
            image_height=self.image_height,
            image_byte_size=self.image_byte_size,
            capture_source=self.capture_source,
            capture_region=self.capture_region,
        )

    @classmethod
    def from_capture(
        cls,
        image: CapturedImage,
        *,
        purpose: str,
        endpoint_url: str,
        model: str,
        task_title: str = "General Guidance",
        step_title: str = "Current Step",
        system_prompt: str = "",
        user_prompt: str = "",
        prompt_summary: str = "",
    ) -> Self:
        """Create a ConsentRequest from a CapturedImage and complete outbound request details."""
        effective_user = user_prompt.strip() or prompt_summary.strip()
        return cls(
            purpose=purpose,
            endpoint_url=endpoint_url,
            model=model,
            task_title=task_title,
            step_title=step_title,
            system_prompt=system_prompt,
            user_prompt=effective_user,
            prompt_summary=prompt_summary.strip() or effective_user,
            image_width=image.width,
            image_height=image.height,
            image_byte_size=image.byte_size,
            capture_region=image.region,
            capture_source=image.source,
            image_sha256=image.sha256_digest,
            preview_png_bytes=image.png_bytes,
        )

    def to_complete_prompt_text(self) -> str:
        """Return the complete text payload (system and user prompts) that will be sent."""
        if self.system_prompt:
            return f"[System Prompt]\n{self.system_prompt}\n\n[User Prompt]\n{self.user_prompt}"
        return self.user_prompt

    def to_display_summary(self) -> str:
        """Format a secret-safe summary of the destination and metadata to be transmitted.

        Shows the sanitized endpoint (scheme, host, path) and indicates whether
        URL credentials, query parameters, or a fragment are present with their
        values redacted ([REDACTED]). Never displays endpoint or request hashes.
        """
        r = self.capture_region
        creds_status = "present ([REDACTED])" if self.has_url_credentials else "none"
        if self.query_parameter_keys:
            redacted_params = ", ".join(f"{k}=[REDACTED]" for k in self.query_parameter_keys)
            query_status = f"present ({redacted_params})"
        else:
            query_status = "none"
        fragment_status = "present ([REDACTED])" if self.has_url_fragment else "none"

        lines = [
            f"Purpose: {self.purpose}",
            f"Destination Endpoint: {self.endpoint_url}",
            f"Endpoint Redacted Form: {self.endpoint_disclosure}",
            f"Endpoint URL Credentials: {creds_status}",
            f"Endpoint Query Parameters: {query_status}",
            f"Endpoint Fragment: {fragment_status}",
            f"Model: {self.model}",
            f"Task: {self.task_title}",
            f"Step: {self.step_title}",
            f"Capture Source: {self.capture_source} (x={r.x}, y={r.y}, w={r.width}, h={r.height})",
            (
                f"Image Size: {self.image_width}x{self.image_height} px "
                f"({self.image_byte_size} bytes PNG)"
            ),
            f"Image SHA-256: {self.image_sha256}",
        ]
        return "\n".join(lines)


@dataclass(slots=True)
class ConsentDecision:
    """Single-use record of a user's consent decision bound to an exact outbound request.

    Internal request and endpoint digests are excluded from repr/str so hashes
    of URL secrets or prompts are never exposed.
    """

    approved: bool
    request_sha256: str = field(default="", repr=False)
    image_sha256: str = ""
    endpoint_url: str = ""
    endpoint_sha256: str = field(default="", repr=False)
    model: str = ""
    purpose: str = ""
    task_title: str = ""
    step_title: str = ""
    system_prompt: str = field(default="", repr=False)
    user_prompt: str = field(default="", repr=False)
    decided_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    reason: str = ""
    _consumed: bool = field(default=False, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.image_sha256 and self.request_sha256:
            self.image_sha256 = self.request_sha256

    def __repr__(self) -> str:
        return (
            f"ConsentDecision(approved={self.approved}, endpoint_url={self.endpoint_url!r}, "
            f"model={self.model!r}, purpose={self.purpose!r}, "
            f"task_title={self.task_title!r}, step_title={self.step_title!r}, "
            f"image_sha256_prefix={self.image_sha256[:12]!r}, "
            f"consumed={self._consumed}, decided_at={self.decided_at!r}, "
            f"reason={self.reason!r})"
        )

    def __str__(self) -> str:
        return self.__repr__()

    @property
    def consumed(self) -> bool:
        """Return True if this approval has already been consumed by an outbound request."""
        return self._consumed

    @classmethod
    def from_request(
        cls,
        request: ConsentRequest,
        *,
        approved: bool,
        reason: str = "",
    ) -> ConsentDecision:
        """Construct a ConsentDecision bound to every field of the ConsentRequest."""
        return cls(
            approved=approved,
            request_sha256=request.request_sha256,
            image_sha256=request.image_sha256,
            endpoint_url=request.endpoint_url,
            endpoint_sha256=request.endpoint_sha256,
            model=request.model,
            purpose=request.purpose,
            task_title=request.task_title,
            step_title=request.step_title,
            system_prompt=request.system_prompt,
            user_prompt=request.user_prompt,
            reason=reason,
        )

    def validate_for_image(self, image: CapturedImage) -> None:
        """Verify that this decision is approved, unconsumed, and matches the image SHA-256."""
        if not self.approved:
            raise ConsentDeclinedError(
                f"User declined screen content transmission ({self.reason or 'cancelled'})."
            )
        if self._consumed:
            raise ConsentDeclinedError(
                "Consent approval has already been used and cannot be reused."
            )
        if self.image_sha256.lower() != image.sha256_digest.lower():
            raise ConsentDeclinedError(
                "Consent decision SHA-256 digest does not match the captured image being sent."
            )

    def validate_for_request(
        self,
        request: ConsentRequest,
        image: CapturedImage,
        *,
        consume: bool = False,
    ) -> None:
        """Verify that this decision authorizes the exact outbound request and image.

        Rejects declined, already-consumed, or mismatched approvals (different image,
        exact endpoint URL including query/credentials/fragment, model, system prompt,
        user prompt, task/step context, or purpose) before any network call can occur.
        """
        self.validate_for_image(image)

        if request.image_sha256.lower() != image.sha256_digest.lower():
            raise ConsentDeclinedError(
                "ConsentRequest image SHA-256 digest does not match the captured image."
            )
        if (
            self.endpoint_url != request.endpoint_url
            or not self.endpoint_sha256
            or self.endpoint_sha256.lower() != request.endpoint_sha256.lower()
        ):
            raise ConsentDeclinedError(
                "Consent decision endpoint does not match the outbound request endpoint."
            )
        if self.model != request.model:
            raise ConsentDeclinedError(
                "Consent decision model does not match the outbound request model."
            )
        if self.purpose != request.purpose:
            raise ConsentDeclinedError(
                "Consent decision purpose does not match the outbound request purpose."
            )
        if self.task_title != request.task_title or self.step_title != request.step_title:
            raise ConsentDeclinedError(
                "Consent decision task/step context does not match the outbound request."
            )
        if self.system_prompt != request.system_prompt or self.user_prompt != request.user_prompt:
            raise ConsentDeclinedError(
                "Consent decision prompt content does not match the outbound request prompt."
            )
        if self.request_sha256.lower() != request.request_sha256.lower():
            raise ConsentDeclinedError(
                "Consent decision request digest does not match the outbound request."
            )
        if consume:
            self._consumed = True


type ConsentHandlerCallable = Callable[[ConsentRequest], bool]


class ScreenShareConsentDialog(QDialog):
    """Qt modal dialog showing the user the complete outbound request and allowing cancellation."""

    def __init__(
        self,
        request: ConsentRequest,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._request = request
        self._approved = False

        self.setWindowTitle("OCTA — Confirm Screen Content Transmission")
        self.setModal(True)
        self.setMinimumSize(580, 520)

        self._setup_ui()

    @property
    def request(self) -> ConsentRequest:
        """The ConsentRequest displayed by this dialog."""
        return self._request

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        header = QLabel(
            "<b>Permission Required to Send Screen Capture</b><br>"
            "OCTA is requesting permission to send the captured screenshot and "
            "complete prompt payload below to the configured remote AI service. "
            "Review what will be sent and choose <b>Approve &amp; Send</b> or <b>Cancel</b>."
        )
        header.setWordWrap(True)
        layout.addWidget(header)

        # Disclosure summary (sanitized endpoint, redacted indicators, model, task, step)
        self._summary_label = QLabel(self._request.to_display_summary())
        self._summary_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._summary_label.setWordWrap(True)
        self._summary_label.setStyleSheet(
            "background-color: #1e293b; color: #f8fafc; padding: 10px; "
            "border-radius: 6px; font-family: Consolas, monospace; font-size: 11px;"
        )
        layout.addWidget(self._summary_label)

        # Screenshot preview thumbnail
        self._preview_label = QLabel()
        self._preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_label.setMinimumHeight(160)
        self._preview_label.setStyleSheet(
            "background-color: #0f172a; border: 1px solid #334155; border-radius: 6px;"
        )

        if self._request.preview_png_bytes:
            qimg = QImage.fromData(self._request.preview_png_bytes, "PNG")
            if not qimg.isNull():
                pixmap = QPixmap.fromImage(qimg).scaled(
                    480,
                    200,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self._preview_label.setPixmap(pixmap)
            else:
                self._preview_label.setText("Preview unavailable")
        else:
            self._preview_label.setText("Preview unavailable")

        layout.addWidget(self._preview_label)

        # Complete prompt text disclosure (both system and user prompts)
        prompt_header = QLabel(
            "<b>Complete Prompt Content to be Sent (System &amp; User Prompts):</b>"
        )
        layout.addWidget(prompt_header)

        self._prompt_box = QPlainTextEdit()
        self._prompt_box.setReadOnly(True)
        self._prompt_box.setPlainText(self._request.to_complete_prompt_text())
        self._prompt_box.setMaximumHeight(130)
        layout.addWidget(self._prompt_box)

        # Action buttons (Cancel is default to prevent accidental submission)
        btn_layout = QHBoxLayout()
        btn_layout.addStretch(1)

        self._btn_cancel = QPushButton("Cancel")
        self._btn_cancel.setMinimumWidth(120)
        self._btn_cancel.setDefault(True)
        self._btn_cancel.clicked.connect(self._on_cancel)

        self._btn_approve = QPushButton("Approve && Send")
        self._btn_approve.setMinimumWidth(150)
        self._btn_approve.clicked.connect(self._on_approve)

        btn_layout.addWidget(self._btn_cancel)
        btn_layout.addWidget(self._btn_approve)
        layout.addLayout(btn_layout)

    def _on_approve(self) -> None:
        self._approved = True
        self.accept()

    def _on_cancel(self) -> None:
        self._approved = False
        self.reject()

    def get_disclosure_text(self) -> str:
        """Return the disclosure summary text shown in the dialog."""
        return self._summary_label.text()

    def get_prompt_preview_text(self) -> str:
        """Return the complete prompt text shown in the dialog."""
        return self._prompt_box.toPlainText()

    def has_image_preview(self) -> bool:
        """Return True if a valid screenshot thumbnail pixmap is loaded."""
        pm = self._preview_label.pixmap()
        return pm is not None and not pm.isNull()

    def decision(self) -> ConsentDecision:
        """Build a ConsentDecision bound to the exact ConsentRequest from the dialog state."""
        return ConsentDecision.from_request(
            self._request,
            approved=self._approved,
            reason="Approved by user in consent dialog"
            if self._approved
            else "Cancelled by user in consent dialog",
        )


def _qt_dialog_consent_handler(request: ConsentRequest) -> bool:
    """Default handler that opens the modal ScreenShareConsentDialog."""
    dialog = ScreenShareConsentDialog(request)
    dialog.exec()
    return dialog.decision().approved


class ConsentManager:
    """Coordinates explicit user consent before sending captured screenshots to an LLM."""

    def __init__(
        self,
        *,
        handler: ConsentHandlerCallable | None = None,
    ) -> None:
        self._handler: ConsentHandlerCallable = (
            handler if handler is not None else _qt_dialog_consent_handler
        )

    def request_consent(self, request: ConsentRequest) -> ConsentDecision:
        """Prompt the user with what will be sent and return a bound ConsentDecision."""
        approved = bool(self._handler(request))
        decision = ConsentDecision.from_request(
            request,
            approved=approved,
            reason=("Approved by consent handler" if approved else "Cancelled by consent handler"),
        )
        logger.info(
            "Screen transmission consent decision: approved=%s, endpoint=%s, "
            "model=%s, image_sha256_prefix=%s.",
            decision.approved,
            request.endpoint_url,
            request.model,
            request.image_sha256[:12],
        )
        return decision

    def require_consent(
        self,
        request: ConsentRequest,
        image: CapturedImage,
        *,
        consume: bool = False,
    ) -> ConsentDecision:
        """Request consent and validate that the user approved the exact outbound request.

        Raises:
            ConsentDeclinedError: If the user cancels or if the request/image does not match.
        """
        decision = self.request_consent(request)
        decision.validate_for_request(request, image, consume=consume)
        return decision
