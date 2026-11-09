"""Unit tests for remote speech provider destination disclosure and single-use consent."""

from __future__ import annotations

import hashlib
import logging
import secrets

import pytest
from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

from octa.voice.audio import RecordedAudio, encode_pcm16_to_wav_bytes
from octa.voice.consent import (
    VoiceConsentDialog,
    VoiceConsentManager,
    VoiceConsentRequest,
    VoiceTransmissionKind,
)
from octa.voice.exceptions import VoiceConsentDeclinedError


def _make_recorded_audio(seed: bytes = b"\x11\x22") -> RecordedAudio:
    wav_bytes = encode_pcm16_to_wav_bytes(seed * 800, sample_rate=16000, channels=1)
    return RecordedAudio(
        wav_bytes=wav_bytes,
        sample_rate=16000,
        channels=1,
        duration_ms=50,
        source="push_to_talk",
    )


def test_voice_consent_request_discloses_destination_and_hides_secrets_audio_and_text(
    qtbot: QtBot,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """VoiceConsentRequest discloses sanitized destination and hides secrets, audio, and hashes."""
    audio = _make_recorded_audio()
    url_user = f"usr_{secrets.token_hex(4)}"
    url_pass = secrets.token_urlsafe(12)
    query_val = f"token_{secrets.token_hex(6)}"
    frag_val = f"frag_{secrets.token_hex(6)}"
    private_spoken_text = f"Private instruction text {secrets.token_hex(6)}"

    raw_stt_url = (
        f"https://{url_user}:{url_pass}@speech.example.com:8443/v1/audio/transcriptions"
        f"?key={query_val}#{frag_val}"
    )
    stt_req = VoiceConsentRequest.from_audio(
        audio,
        provider_name="remote_http_stt",
        endpoint_url=raw_stt_url,
        model="whisper-1",
        task_title="Tutorial Task",
        step_title="Step 1",
    )

    tts_req = VoiceConsentRequest.from_text(
        private_spoken_text,
        provider_name="remote_http_tts",
        endpoint_url=raw_stt_url,
        model="tts-1",
        voice_name="alloy",
        task_title="Tutorial Task",
        step_title="Step 1",
    )

    stt_dialog = VoiceConsentDialog(stt_req)
    tts_dialog = VoiceConsentDialog(tts_req)
    qtbot.addWidget(stt_dialog)
    qtbot.addWidget(tts_dialog)

    with caplog.at_level(logging.DEBUG, logger="octa.voice.consent"):
        mgr = VoiceConsentManager(handler=lambda _r: True)
        stt_decision = mgr.require_audio_consent(stt_req, audio)
        tts_decision = mgr.require_text_consent(tts_req, private_spoken_text)

    # Check dialog disclosure for STT and TTS
    stt_disclosure = stt_dialog.get_disclosure_text()
    assert (
        "Destination Endpoint: https://speech.example.com:8443/v1/audio/transcriptions"
        in stt_disclosure
    )
    assert (
        "Endpoint Redacted Form: "
        "https://[REDACTED]@speech.example.com:8443/v1/audio/transcriptions"
        "?key=[REDACTED]#[REDACTED]"
    ) in stt_disclosure
    assert "Endpoint URL Credentials: present ([REDACTED])" in stt_disclosure
    assert "Endpoint Query Parameters: present (key=[REDACTED])" in stt_disclosure
    assert "Endpoint Fragment: present ([REDACTED])" in stt_disclosure
    assert "Transmission Type: speech_to_text" in stt_disclosure
    assert "Speech Provider: remote_http_stt" in stt_disclosure

    # TTS dialog shows the text for user review, but NEVER logs it or puts it in repr/str
    assert tts_dialog.get_text_preview() == private_spoken_text
    assert stt_dialog._btn_cancel.isDefault() is True
    assert tts_dialog._btn_cancel.isDefault() is True

    forbidden_strings = {
        url_user,
        url_pass,
        query_val,
        frag_val,
        private_spoken_text,
        stt_req.endpoint_sha256,
        stt_req.endpoint_sha256[:8],
        stt_req.request_sha256,
        stt_req.request_sha256[:8],
        stt_req.payload_sha256,
        stt_req.payload_sha256[:8],
        tts_req.request_sha256,
        tts_req.request_sha256[:8],
        tts_req.payload_sha256,
        tts_req.payload_sha256[:8],
    }
    for comp in (url_user, url_pass, query_val, frag_val, private_spoken_text):
        digest = hashlib.sha256(comp.encode("utf-8")).hexdigest()
        forbidden_strings.add(digest)
        forbidden_strings.add(digest[:8])

    log_and_repr_surfaces = [
        stt_disclosure,
        tts_dialog.get_disclosure_text(),
        repr(stt_req),
        str(stt_req),
        repr(tts_req),
        str(tts_req),
        repr(stt_decision),
        str(stt_decision),
        repr(tts_decision),
        str(tts_decision),
        *[rec.getMessage() for rec in caplog.records],
    ]
    for surface in log_and_repr_surfaces:
        for forbidden in forbidden_strings:
            assert forbidden not in surface


def test_voice_consent_single_use_and_exact_binding(qtbot: QtBot) -> None:
    """VoiceConsentDecision binds to exact audio/text, exact endpoint URL, and prevents reuse."""
    audio_1 = _make_recorded_audio(b"\x10\x20")
    audio_2 = _make_recorded_audio(b"\x30\x40")

    req = VoiceConsentRequest.from_audio(
        audio_1,
        provider_name="remote_http_stt",
        endpoint_url="https://speech.example.com/v1/audio/transcriptions?tenant=alpha",
        model="whisper-1",
        task_title="Task A",
        step_title="Step 1",
    )

    dialog = VoiceConsentDialog(req)
    qtbot.addWidget(dialog)
    qtbot.mouseClick(dialog._btn_approve, Qt.MouseButton.LeftButton)
    decision = dialog.decision()
    assert decision.approved is True
    assert decision.transmission_kind == VoiceTransmissionKind.SPEECH_TO_TEXT

    # 1. Different audio rejected
    with pytest.raises(
        VoiceConsentDeclinedError, match="does not match the recorded microphone audio"
    ):
        decision.validate_for_audio(req, audio_2)

    # 2. Changed query parameter in endpoint URL rejected
    req_diff_query = VoiceConsentRequest.from_audio(
        audio_1,
        provider_name="remote_http_stt",
        endpoint_url="https://speech.example.com/v1/audio/transcriptions?tenant=beta",
        model="whisper-1",
        task_title="Task A",
        step_title="Step 1",
    )
    with pytest.raises(VoiceConsentDeclinedError, match="endpoint does not match"):
        decision.validate_for_audio(req_diff_query, audio_1)

    # 3. Changed model rejected
    req_diff_model = VoiceConsentRequest.from_audio(
        audio_1,
        provider_name="remote_http_stt",
        endpoint_url="https://speech.example.com/v1/audio/transcriptions?tenant=alpha",
        model="other-model",
        task_title="Task A",
        step_title="Step 1",
    )
    with pytest.raises(VoiceConsentDeclinedError, match="model or voice does not match"):
        decision.validate_for_audio(req_diff_model, audio_1)

    # 4. Exact match succeeds and consumes single-use approval
    decision.validate_for_audio(req, audio_1, consume=True)
    assert decision.consumed is True

    # 5. Reusing consumed approval fails
    with pytest.raises(VoiceConsentDeclinedError, match="already been used"):
        decision.validate_for_audio(req, audio_1)


def test_voice_consent_tts_text_binding_and_cancellation(qtbot: QtBot) -> None:
    """VoiceConsentDecision rejects cancelled or altered TTS text."""
    req = VoiceConsentRequest.from_text(
        "Click the File menu in the top-left corner.",
        provider_name="remote_http_tts",
        endpoint_url="https://speech.example.com/v1/audio/speech",
        model="tts-1",
        voice_name="alloy",
    )

    dialog = VoiceConsentDialog(req)
    qtbot.addWidget(dialog)
    qtbot.mouseClick(dialog._btn_cancel, Qt.MouseButton.LeftButton)
    cancelled_decision = dialog.decision()
    assert cancelled_decision.approved is False

    with pytest.raises(VoiceConsentDeclinedError, match="Cancelled"):
        cancelled_decision.validate_for_text(req, "Click the File menu in the top-left corner.")

    # Approve and verify text tampering is caught
    mgr = VoiceConsentManager(handler=lambda _r: True)
    approved = mgr.request_consent(req)
    with pytest.raises(VoiceConsentDeclinedError, match="does not match the text"):
        approved.validate_for_text(req, "Different text that was not approved.")
