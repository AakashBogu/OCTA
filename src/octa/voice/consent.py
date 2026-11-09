"""Explicit user consent before sending microphone audio or text to remote speech providers.

Discloses the destination endpoint (sanitized base URL and redacted indicators for any
URL credentials, query parameters, or fragments), provider name, model, purpose, task/step
context, and audio/text metadata. Binds single-use approval internally to the exact raw
endpoint URL digest and exact audio/text payload digest without exposing raw secrets,
audio bytes, transcripts, or hash fingerprints in repr, str, logs, or errors.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Self
from urllib.parse import parse_qsl, urlparse

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from octa.intelligence.consent import (
    format_endpoint_for_disclosure,
    sanitize_endpoint_for_display,
)
from octa.intelligence.exceptions import ConsentError
from octa.voice.audio import RecordedAudio
from octa.voice.exceptions import VoiceConsentDeclinedError, VoiceConsentError

logger = logging.getLogger("octa.voice.consent")


class VoiceTransmissionKind(StrEnum):
    """Type of voice payload being sent to a remote speech provider."""

    SPEECH_TO_TEXT = "speech_to_text"
    TEXT_TO_SPEECH = "text_to_speech"

    def __str__(self) -> str:
        return self.value


VALID_VOICE_TRANSMISSION_KINDS = frozenset(k.value for k in VoiceTransmissionKind)


def _sanitize_voice_endpoint(endpoint_url: str) -> str:
    """Sanitize a voice provider endpoint URL for display."""
    try:
        return sanitize_endpoint_for_display(endpoint_url)
    except ConsentError as exc:
        raise VoiceConsentError(str(exc)) from exc


def _format_voice_endpoint_disclosure(endpoint_url: str) -> str:
    """Format a voice provider endpoint URL with credentials/query/fragment redacted."""
    try:
        return format_endpoint_for_disclosure(endpoint_url)
    except ConsentError as exc:
        raise VoiceConsentError(str(exc)) from exc


def compute_voice_request_sha256(
    *,
    transmission_kind: str,
    purpose: str,
    provider_name: str,
    endpoint_url: str,
    endpoint_sha256: str,
    model: str,
    voice_name: str,
    task_title: str,
    step_title: str,
    payload_sha256: str,
    payload_byte_size: int,
    audio_duration_ms: int,
    audio_sample_rate: int,
    audio_channels: int,
    text_char_length: int,
) -> str:
    """Compute a deterministic internal SHA-256 digest binding the complete voice request."""
    canonical = {
        "audio_channels": audio_channels,
        "audio_duration_ms": audio_duration_ms,
        "audio_sample_rate": audio_sample_rate,
        "endpoint_sha256": endpoint_sha256.lower(),
        "endpoint_url": endpoint_url,
        "model": model,
        "payload_byte_size": payload_byte_size,
        "payload_sha256": payload_sha256.lower(),
        "provider_name": provider_name,
        "purpose": purpose,
        "step_title": step_title,
        "task_title": task_title,
        "text_char_length": text_char_length,
        "transmission_kind": transmission_kind,
        "voice_name": voice_name,
    }
    encoded = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class VoiceConsentRequest:
    """Complete disclosure of a remote speech provider request requiring explicit user consent.

    Internal digests (endpoint_sha256, payload_sha256) and text_to_speak are excluded from
    repr and str so credentials, user text, audio, and hash fingerprints are never logged.
    """

    transmission_kind: VoiceTransmissionKind
    purpose: str
    provider_name: str
    endpoint_url: str
    model: str
    payload_byte_size: int
    payload_sha256: str = field(repr=False)
    task_title: str = "Voice Interaction"
    step_title: str = "Current Step"
    voice_name: str = ""
    audio_duration_ms: int = 0
    audio_sample_rate: int = 0
    audio_channels: int = 0
    text_char_length: int = 0
    text_to_speak: str = field(default="", repr=False)
    endpoint_disclosure: str = ""
    has_url_credentials: bool = False
    query_parameter_keys: tuple[str, ...] = ()
    has_url_fragment: bool = False
    endpoint_sha256: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        if isinstance(self.transmission_kind, str) and not isinstance(
            self.transmission_kind, VoiceTransmissionKind
        ):
            if self.transmission_kind not in VALID_VOICE_TRANSMISSION_KINDS:
                raise VoiceConsentError(
                    f"Invalid transmission_kind '{self.transmission_kind}'. "
                    f"Must be one of {sorted(VALID_VOICE_TRANSMISSION_KINDS)}."
                )
            object.__setattr__(
                self, "transmission_kind", VoiceTransmissionKind(self.transmission_kind)
            )
        elif not isinstance(self.transmission_kind, VoiceTransmissionKind):
            raise VoiceConsentError("transmission_kind must be a VoiceTransmissionKind.")

        for name in ("purpose", "provider_name", "model", "task_title", "step_title"):
            val = getattr(self, name)
            if not isinstance(val, str) or not val.strip():
                raise VoiceConsentError(f"VoiceConsentRequest {name} must be a non-empty string.")
            object.__setattr__(self, name, val.strip())

        if not isinstance(self.voice_name, str):
            raise VoiceConsentError("VoiceConsentRequest voice_name must be a string.")
        object.__setattr__(self, "voice_name", self.voice_name.strip())

        if not isinstance(self.payload_sha256, str) or not self.payload_sha256.strip():
            raise VoiceConsentError("VoiceConsentRequest payload_sha256 must be non-empty.")
        object.__setattr__(self, "payload_sha256", self.payload_sha256.strip().lower())

        if (
            isinstance(self.payload_byte_size, bool)
            or not isinstance(self.payload_byte_size, int)
            or self.payload_byte_size <= 0
        ):
            raise VoiceConsentError("VoiceConsentRequest payload_byte_size must be positive.")

        if not isinstance(self.endpoint_url, str) or not self.endpoint_url.strip():
            raise VoiceConsentError("Endpoint URL must be a non-empty string.")
        raw_endpoint = self.endpoint_url.strip()
        clean_endpoint = _sanitize_voice_endpoint(raw_endpoint)
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

        exact_endpoint_sha256 = (
            self.endpoint_sha256.strip().lower()
            if self.endpoint_sha256 and self.endpoint_sha256.strip()
            else hashlib.sha256(raw_endpoint.encode("utf-8")).hexdigest()
        )
        disclosed_endpoint = (
            self.endpoint_disclosure.strip()
            if self.endpoint_disclosure and self.endpoint_disclosure.strip()
            else _format_voice_endpoint_disclosure(raw_endpoint)
        )

        object.__setattr__(self, "endpoint_url", clean_endpoint)
        object.__setattr__(self, "endpoint_sha256", exact_endpoint_sha256)
        object.__setattr__(self, "endpoint_disclosure", disclosed_endpoint)
        object.__setattr__(self, "has_url_credentials", has_creds)
        object.__setattr__(self, "query_parameter_keys", query_keys)
        object.__setattr__(self, "has_url_fragment", has_frag)

    def __repr__(self) -> str:
        return (
            f"VoiceConsentRequest(transmission_kind={self.transmission_kind.value!r}, "
            f"provider_name={self.provider_name!r}, purpose={self.purpose!r}, "
            f"endpoint_url={self.endpoint_url!r}, "
            f"endpoint_disclosure={self.endpoint_disclosure!r}, "
            f"has_url_credentials={self.has_url_credentials}, "
            f"query_parameter_keys={self.query_parameter_keys!r}, "
            f"has_url_fragment={self.has_url_fragment}, model={self.model!r}, "
            f"voice_name={self.voice_name!r}, task_title={self.task_title!r}, "
            f"step_title={self.step_title!r}, payload_byte_size={self.payload_byte_size}, "
            f"audio_duration_ms={self.audio_duration_ms}, "
            f"text_char_length={self.text_char_length})"
        )

    def __str__(self) -> str:
        return self.__repr__()

    @property
    def request_sha256(self) -> str:
        """Internal deterministic SHA-256 digest binding all fields of the voice request."""
        return compute_voice_request_sha256(
            transmission_kind=self.transmission_kind.value,
            purpose=self.purpose,
            provider_name=self.provider_name,
            endpoint_url=self.endpoint_url,
            endpoint_sha256=self.endpoint_sha256,
            model=self.model,
            voice_name=self.voice_name,
            task_title=self.task_title,
            step_title=self.step_title,
            payload_sha256=self.payload_sha256,
            payload_byte_size=self.payload_byte_size,
            audio_duration_ms=self.audio_duration_ms,
            audio_sample_rate=self.audio_sample_rate,
            audio_channels=self.audio_channels,
            text_char_length=self.text_char_length,
        )

    @classmethod
    def from_audio(
        cls,
        audio: RecordedAudio,
        *,
        purpose: str = "Transcribe push-to-talk microphone audio",
        provider_name: str,
        endpoint_url: str,
        model: str,
        task_title: str = "Voice Interaction",
        step_title: str = "Push-to-Talk Request",
    ) -> Self:
        """Build a VoiceConsentRequest for sending recorded push-to-talk audio to a remote STT."""
        if not isinstance(audio, RecordedAudio):
            raise VoiceConsentError("Expected a RecordedAudio instance for STT consent.")
        return cls(
            transmission_kind=VoiceTransmissionKind.SPEECH_TO_TEXT,
            purpose=purpose,
            provider_name=provider_name,
            endpoint_url=endpoint_url,
            model=model,
            payload_byte_size=audio.byte_size,
            payload_sha256=audio.sha256_digest,
            task_title=task_title,
            step_title=step_title,
            audio_duration_ms=audio.duration_ms,
            audio_sample_rate=audio.sample_rate,
            audio_channels=audio.channels,
        )

    @classmethod
    def from_text(
        cls,
        text: str,
        *,
        purpose: str = "Synthesize spoken instruction audio",
        provider_name: str,
        endpoint_url: str,
        model: str,
        voice_name: str = "alloy",
        task_title: str = "Voice Interaction",
        step_title: str = "Read Instruction Aloud",
    ) -> Self:
        """Build a VoiceConsentRequest for sending text to a remote TTS provider."""
        if not isinstance(text, str) or not text.strip():
            raise VoiceConsentError("TTS consent requires a non-empty text string.")
        cleaned_text = text.strip()
        encoded_bytes = cleaned_text.encode("utf-8")
        return cls(
            transmission_kind=VoiceTransmissionKind.TEXT_TO_SPEECH,
            purpose=purpose,
            provider_name=provider_name,
            endpoint_url=endpoint_url,
            model=model,
            voice_name=voice_name,
            payload_byte_size=len(encoded_bytes),
            payload_sha256=hashlib.sha256(encoded_bytes).hexdigest(),
            task_title=task_title,
            step_title=step_title,
            text_char_length=len(cleaned_text),
            text_to_speak=cleaned_text,
        )

    def to_display_summary(self) -> str:
        """Format a secret-safe summary of the destination and voice payload metadata.

        Shows the sanitized endpoint and redacted indicators ([REDACTED]) without
        exposing raw URL secrets, audio bytes, or hash fingerprints.
        """
        creds_status = "present ([REDACTED])" if self.has_url_credentials else "none"
        if self.query_parameter_keys:
            redacted_params = ", ".join(f"{k}=[REDACTED]" for k in self.query_parameter_keys)
            query_status = f"present ({redacted_params})"
        else:
            query_status = "none"
        fragment_status = "present ([REDACTED])" if self.has_url_fragment else "none"

        lines = [
            f"Transmission Type: {self.transmission_kind.value}",
            f"Speech Provider: {self.provider_name}",
            f"Purpose: {self.purpose}",
            f"Destination Endpoint: {self.endpoint_url}",
            f"Endpoint Redacted Form: {self.endpoint_disclosure}",
            f"Endpoint URL Credentials: {creds_status}",
            f"Endpoint Query Parameters: {query_status}",
            f"Endpoint Fragment: {fragment_status}",
            f"Model: {self.model}",
        ]
        if self.voice_name:
            lines.append(f"Voice: {self.voice_name}")
        lines.extend(
            [
                f"Task: {self.task_title}",
                f"Step: {self.step_title}",
            ]
        )
        if self.transmission_kind == VoiceTransmissionKind.SPEECH_TO_TEXT:
            lines.append(
                f"Microphone Audio: push_to_talk, {self.audio_duration_ms} ms, "
                f"{self.audio_sample_rate} Hz, {self.audio_channels} ch "
                f"({self.payload_byte_size} bytes WAV)"
            )
        else:
            lines.append(
                f"Text Payload Size: {self.text_char_length} characters "
                f"({self.payload_byte_size} bytes UTF-8)"
            )
        return "\n".join(lines)


@dataclass(slots=True)
class VoiceConsentDecision:
    """Single-use record of a user's consent decision bound to an exact voice request.

    Internal digests (request_sha256, endpoint_sha256, payload_sha256) are excluded from
    repr and str so hashes of URL secrets, audio, or text are never exposed.
    """

    approved: bool
    transmission_kind: VoiceTransmissionKind = VoiceTransmissionKind.SPEECH_TO_TEXT
    provider_name: str = ""
    endpoint_url: str = ""
    model: str = ""
    voice_name: str = ""
    purpose: str = ""
    task_title: str = ""
    step_title: str = ""
    request_sha256: str = field(default="", repr=False)
    endpoint_sha256: str = field(default="", repr=False)
    payload_sha256: str = field(default="", repr=False)
    decided_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    reason: str = ""
    _consumed: bool = field(default=False, init=False, repr=False, compare=False)

    def __repr__(self) -> str:
        return (
            f"VoiceConsentDecision(approved={self.approved}, "
            f"transmission_kind={self.transmission_kind.value!r}, "
            f"provider_name={self.provider_name!r}, endpoint_url={self.endpoint_url!r}, "
            f"model={self.model!r}, voice_name={self.voice_name!r}, "
            f"purpose={self.purpose!r}, task_title={self.task_title!r}, "
            f"step_title={self.step_title!r}, consumed={self._consumed}, "
            f"decided_at={self.decided_at!r}, reason={self.reason!r})"
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
        request: VoiceConsentRequest,
        *,
        approved: bool,
        reason: str = "",
    ) -> VoiceConsentDecision:
        """Construct a VoiceConsentDecision bound to every field of the VoiceConsentRequest."""
        return cls(
            approved=approved,
            transmission_kind=request.transmission_kind,
            provider_name=request.provider_name,
            endpoint_url=request.endpoint_url,
            model=request.model,
            voice_name=request.voice_name,
            purpose=request.purpose,
            task_title=request.task_title,
            step_title=request.step_title,
            request_sha256=request.request_sha256,
            endpoint_sha256=request.endpoint_sha256,
            payload_sha256=request.payload_sha256,
            reason=reason,
        )

    def _validate_common_request(self, request: VoiceConsentRequest) -> None:
        if not self.approved:
            raise VoiceConsentDeclinedError(
                f"User declined remote speech transmission ({self.reason or 'cancelled'})."
            )
        if self._consumed:
            raise VoiceConsentDeclinedError(
                "Voice consent approval has already been used and cannot be reused."
            )
        if self.transmission_kind != request.transmission_kind:
            raise VoiceConsentDeclinedError(
                "Voice consent transmission kind does not match the outbound request."
            )
        if self.provider_name != request.provider_name:
            raise VoiceConsentDeclinedError(
                "Voice consent provider_name does not match the outbound request."
            )
        if (
            self.endpoint_url != request.endpoint_url
            or not self.endpoint_sha256
            or self.endpoint_sha256.lower() != request.endpoint_sha256.lower()
        ):
            raise VoiceConsentDeclinedError(
                "Voice consent endpoint does not match the outbound request endpoint."
            )
        if self.model != request.model or self.voice_name != request.voice_name:
            raise VoiceConsentDeclinedError(
                "Voice consent model or voice does not match the outbound request."
            )
        if self.purpose != request.purpose:
            raise VoiceConsentDeclinedError(
                "Voice consent purpose does not match the outbound request purpose."
            )
        if self.task_title != request.task_title or self.step_title != request.step_title:
            raise VoiceConsentDeclinedError(
                "Voice consent task/step context does not match the outbound request."
            )
        if (
            self.payload_sha256.lower() != request.payload_sha256.lower()
            or self.request_sha256.lower() != request.request_sha256.lower()
        ):
            raise VoiceConsentDeclinedError(
                "Voice consent request digest does not match the outbound request."
            )

    def validate_for_audio(
        self,
        request: VoiceConsentRequest,
        audio: RecordedAudio,
        *,
        consume: bool = False,
    ) -> None:
        """Verify that this decision authorizes sending the exact RecordedAudio to the endpoint."""
        self._validate_common_request(request)
        if request.transmission_kind != VoiceTransmissionKind.SPEECH_TO_TEXT:
            raise VoiceConsentDeclinedError("Consent request is not for speech_to_text.")
        actual_sha256 = audio.sha256_digest.lower()
        if (
            self.payload_sha256.lower() != actual_sha256
            or request.payload_sha256.lower() != actual_sha256
        ):
            raise VoiceConsentDeclinedError(
                "Voice consent decision does not match the recorded microphone audio."
            )
        if consume:
            self._consumed = True

    def validate_for_text(
        self,
        request: VoiceConsentRequest,
        text: str,
        *,
        consume: bool = False,
    ) -> None:
        """Verify that this decision authorizes sending the exact text to the TTS endpoint."""
        self._validate_common_request(request)
        if request.transmission_kind != VoiceTransmissionKind.TEXT_TO_SPEECH:
            raise VoiceConsentDeclinedError("Consent request is not for text_to_speech.")
        if not isinstance(text, str) or not text.strip():
            raise VoiceConsentDeclinedError("TTS text must be non-empty.")
        actual_sha256 = hashlib.sha256(text.strip().encode("utf-8")).hexdigest().lower()
        if (
            self.payload_sha256.lower() != actual_sha256
            or request.payload_sha256.lower() != actual_sha256
        ):
            raise VoiceConsentDeclinedError(
                "Voice consent decision does not match the text being sent to TTS."
            )
        if consume:
            self._consumed = True


type VoiceConsentHandlerCallable = Callable[[VoiceConsentRequest], bool]


class VoiceConsentDialog(QDialog):
    """Qt modal dialog disclosing the remote speech provider destination and requesting consent."""

    def __init__(
        self,
        request: VoiceConsentRequest,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._request = request
        self._approved = False

        self.setWindowTitle("OCTA — Confirm Remote Speech Provider Transmission")
        self.setModal(True)
        self.setMinimumSize(540, 380)

        self._setup_ui()

    @property
    def request(self) -> VoiceConsentRequest:
        """The VoiceConsentRequest displayed by this dialog."""
        return self._request

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        if self._request.transmission_kind == VoiceTransmissionKind.SPEECH_TO_TEXT:
            header_html = (
                "<b>Permission Required to Send Push-to-Talk Audio</b><br>"
                "OCTA is requesting permission to send your recorded push-to-talk "
                "microphone audio to the configured remote speech-to-text service. "
                "Review the destination details below and choose <b>Approve &amp; Send</b> "
                "or <b>Cancel</b>."
            )
        else:
            header_html = (
                "<b>Permission Required to Send Text to Speech Provider</b><br>"
                "OCTA is requesting permission to send the instruction text below to "
                "the configured remote text-to-speech service. Review the destination "
                "and text below and choose <b>Approve &amp; Send</b> or <b>Cancel</b>."
            )

        header = QLabel(header_html)
        header.setWordWrap(True)
        layout.addWidget(header)

        self._summary_label = QLabel(self._request.to_display_summary())
        self._summary_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._summary_label.setWordWrap(True)
        self._summary_label.setStyleSheet(
            "background-color: #1e293b; color: #f8fafc; padding: 10px; "
            "border-radius: 6px; font-family: Consolas, monospace; font-size: 11px;"
        )
        layout.addWidget(self._summary_label)

        self._text_box = QPlainTextEdit()
        self._text_box.setReadOnly(True)
        if self._request.transmission_kind == VoiceTransmissionKind.TEXT_TO_SPEECH:
            text_header = QLabel("<b>Complete Text to be Sent to TTS Provider:</b>")
            layout.addWidget(text_header)
            self._text_box.setPlainText(self._request.text_to_speak)
            self._text_box.setMaximumHeight(110)
            layout.addWidget(self._text_box)
        else:
            self._text_box.hide()

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

    def get_text_preview(self) -> str:
        """Return the TTS text preview shown in the dialog (or empty for STT)."""
        return self._text_box.toPlainText()

    def decision(self) -> VoiceConsentDecision:
        """Build a VoiceConsentDecision bound to the exact VoiceConsentRequest."""
        return VoiceConsentDecision.from_request(
            self._request,
            approved=self._approved,
            reason=(
                "Approved by user in voice consent dialog"
                if self._approved
                else "Cancelled by user in voice consent dialog"
            ),
        )


def _qt_voice_dialog_consent_handler(request: VoiceConsentRequest) -> bool:
    """Default handler that opens the modal VoiceConsentDialog."""
    dialog = VoiceConsentDialog(request)
    dialog.exec()
    return dialog.decision().approved


class VoiceConsentManager:
    """Coordinates explicit user consent before sending microphone audio or text remotely."""

    def __init__(
        self,
        *,
        handler: VoiceConsentHandlerCallable | None = None,
    ) -> None:
        self._handler: VoiceConsentHandlerCallable = (
            handler if handler is not None else _qt_voice_dialog_consent_handler
        )

    def request_consent(self, request: VoiceConsentRequest) -> VoiceConsentDecision:
        """Prompt the user with the destination disclosure and return a bound decision."""
        approved = bool(self._handler(request))
        decision = VoiceConsentDecision.from_request(
            request,
            approved=approved,
            reason=(
                "Approved by voice consent handler"
                if approved
                else "Cancelled by voice consent handler"
            ),
        )
        logger.info(
            "Voice transmission consent decision: approved=%s, kind=%s, provider=%s, "
            "endpoint=%s, model=%s, bytes=%d.",
            decision.approved,
            request.transmission_kind.value,
            request.provider_name,
            request.endpoint_url,
            request.model,
            request.payload_byte_size,
        )
        return decision

    def require_audio_consent(
        self,
        request: VoiceConsentRequest,
        audio: RecordedAudio,
        *,
        consume: bool = False,
    ) -> VoiceConsentDecision:
        """Request consent and validate that the user approved sending the exact audio."""
        decision = self.request_consent(request)
        decision.validate_for_audio(request, audio, consume=consume)
        return decision

    def require_text_consent(
        self,
        request: VoiceConsentRequest,
        text: str,
        *,
        consume: bool = False,
    ) -> VoiceConsentDecision:
        """Request consent and validate that the user approved sending the exact text."""
        decision = self.request_consent(request)
        decision.validate_for_text(request, text, consume=consume)
        return decision
