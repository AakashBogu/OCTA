"""Unit tests for STT/TTS provider interfaces, mocks, HTTPS/redirect policies, and consent gates."""

from __future__ import annotations

import io
import json
import logging
import secrets
import urllib.error
import urllib.request

import pytest

from octa.core.config import AppConfig
from octa.voice.audio import RecordedAudio, encode_pcm16_to_wav_bytes
from octa.voice.consent import VoiceConsentManager
from octa.voice.exceptions import (
    MalformedSpeechResponseError,
    VoiceCancelledError,
    VoiceConfigurationError,
    VoiceConsentDeclinedError,
    VoiceMissingCredentialsError,
    VoiceTransportError,
)
from octa.voice.providers import (
    VOICE_PROVIDER_OPTIONS,
    AudioPlaybackBackend,
    DisabledSTTProvider,
    DisabledTTSProvider,
    MockAudioPlaybackBackend,
    MockSTTProvider,
    MockTTSProvider,
    QtAudioPlaybackBackend,
    RemoteHTTPSTTProvider,
    RemoteHTTPTTSProvider,
    RemoteVoiceConfig,
    SpeechToTextProvider,
    TextToSpeechProvider,
    VoiceNoRedirectHandler,
    build_voice_no_redirect_opener,
    create_playback_backend,
    create_stt_provider,
    create_tts_provider,
    validate_voice_endpoint,
)


def _sample_audio() -> RecordedAudio:
    wav_bytes = encode_pcm16_to_wav_bytes(b"\x15\x00" * 800, sample_rate=16000, channels=1)
    return RecordedAudio(
        wav_bytes=wav_bytes,
        sample_rate=16000,
        channels=1,
        duration_ms=50,
        source="push_to_talk",
    )


def test_default_providers_are_disabled_and_do_not_assume_paid_cloud() -> None:
    """By default no voice provider is enabled and no cloud service is assumed."""
    cfg = AppConfig()
    stt = create_stt_provider(cfg)
    tts = create_tts_provider(cfg)

    assert isinstance(stt, DisabledSTTProvider)
    assert isinstance(tts, DisabledTTSProvider)
    assert isinstance(stt, SpeechToTextProvider)
    assert isinstance(tts, TextToSpeechProvider)
    assert stt.is_remote is False
    assert tts.is_remote is False

    with pytest.raises(VoiceConfigurationError, match="No Speech-to-Text"):
        stt.transcribe(_sample_audio())

    with pytest.raises(VoiceConfigurationError, match="No Text-to-Speech"):
        tts.synthesize("Read this step.")

    playback = create_playback_backend(cfg)
    assert isinstance(playback, QtAudioPlaybackBackend)
    assert isinstance(playback, AudioPlaybackBackend)

    implemented_values = {opt.config_value for opt in VOICE_PROVIDER_OPTIONS if opt.implemented}
    assert implemented_values == {"disabled", "mock", "remote_http"}
    assert all(opt.config_value != "local" for opt in VOICE_PROVIDER_OPTIONS)


def test_mock_stt_tts_and_playback_providers() -> None:
    """MockSTTProvider, MockTTSProvider, and MockAudioPlaybackBackend satisfy protocols."""
    stt = MockSTTProvider(responses=["Next step please"])
    tts = MockTTSProvider()
    playback = MockAudioPlaybackBackend()

    assert isinstance(stt, SpeechToTextProvider)
    assert isinstance(tts, TextToSpeechProvider)
    assert isinstance(playback, AudioPlaybackBackend)

    audio = _sample_audio()
    res = stt.transcribe(audio)
    assert res.text == "Next step please"
    assert stt.call_count == 1

    speech = tts.synthesize("Click the Save button.")
    assert speech.byte_size > 44
    assert tts.call_count == 1

    playback.play(speech)
    assert playback.is_playing is True
    assert playback.play_count == 1

    playback.stop()
    assert playback.is_playing is False
    assert playback.stop_count == 1

    # Cancellation
    stt.cancel()
    with pytest.raises(VoiceCancelledError):
        stt.transcribe(audio)

    tts.cancel()
    with pytest.raises(VoiceCancelledError):
        tts.synthesize("Click the Save button.")


def test_remote_voice_endpoint_enforces_https_and_loopback_http_opt_in() -> None:
    """Remote speech endpoints must use HTTPS; loopback HTTP requires allow_local_http=True."""
    with pytest.raises(VoiceConfigurationError, match="remote speech providers must use HTTPS"):
        validate_voice_endpoint("http://speech.example.com/v1/audio/transcriptions")

    with pytest.raises(VoiceConfigurationError, match="remote speech providers must use HTTPS"):
        RemoteVoiceConfig(
            endpoint="http://speech.example.com/v1/audio/transcriptions",
            allow_local_http=True,
        )

    with pytest.raises(VoiceConfigurationError, match="OCTA_VOICE_ALLOW_LOCAL_HTTP=true"):
        RemoteVoiceConfig(endpoint="http://127.0.0.1:9000/v1/audio/transcriptions")

    local_cfg = RemoteVoiceConfig(
        endpoint="http://127.0.0.1:9000/v1/audio/transcriptions",
        allow_local_http=True,
    )
    assert local_cfg.endpoint == "http://127.0.0.1:9000/v1/audio/transcriptions"


def test_remote_stt_and_tts_require_explicit_consent_before_credentials_or_network() -> None:
    """RemoteHTTPSTTProvider and RemoteHTTPTTSProvider require consent before checking API keys."""
    audio = _sample_audio()
    cfg_no_key = RemoteVoiceConfig(
        endpoint="https://speech.example.com/v1/audio/transcriptions",
        api_key=None,
    )
    transport_calls: list[urllib.request.Request] = []

    def spy_transport(req: urllib.request.Request, _timeout: float) -> bytes:
        transport_calls.append(req)
        return b"{}"

    # 1. Without consent manager or decision -> VoiceConsentDeclinedError (before checking api_key!)
    stt_no_consent = RemoteHTTPSTTProvider(cfg_no_key, transport=spy_transport)
    with pytest.raises(VoiceConsentDeclinedError, match="Explicit user consent is required"):
        stt_no_consent.transcribe(audio)
    assert transport_calls == []

    tts_no_consent = RemoteHTTPTTSProvider(cfg_no_key, transport=spy_transport)
    with pytest.raises(VoiceConsentDeclinedError, match="Explicit user consent is required"):
        tts_no_consent.synthesize("Read step 1.")
    assert transport_calls == []

    # 2. With declining consent manager -> VoiceConsentDeclinedError
    declining_mgr = VoiceConsentManager(handler=lambda _r: False)
    stt_declined = RemoteHTTPSTTProvider(
        cfg_no_key,
        transport=spy_transport,
        consent_manager=declining_mgr,
    )
    with pytest.raises(VoiceConsentDeclinedError, match="Cancelled"):
        stt_declined.transcribe(audio)
    assert transport_calls == []

    # 3. With approving consent manager but missing API key -> VoiceMissingCredentialsError
    approving_mgr = VoiceConsentManager(handler=lambda _r: True)
    stt_missing_key = RemoteHTTPSTTProvider(
        cfg_no_key,
        transport=spy_transport,
        consent_manager=approving_mgr,
    )
    with pytest.raises(VoiceMissingCredentialsError, match="No voice provider API key"):
        stt_missing_key.transcribe(audio)
    assert transport_calls == []


def test_remote_stt_and_tts_block_redirects_and_protect_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Remote STT and TTS block all HTTP redirects and never log keys, audio, or transcripts."""
    audio = _sample_audio()
    runtime_key = secrets.token_urlsafe(18)
    private_transcript = f"Spoken secret phrase {secrets.token_hex(6)}"
    private_tts_text = f"Instruction text {secrets.token_hex(6)}"

    cfg_stt = RemoteVoiceConfig(
        endpoint="https://speech.example.com/v1/audio/transcriptions",
        model="whisper-1",
        api_key=runtime_key,
    )
    cfg_tts = RemoteVoiceConfig(
        endpoint="https://speech.example.com/v1/audio/speech",
        model="tts-1",
        voice_name="alloy",
        api_key=runtime_key,
    )

    # Verify VoiceNoRedirectHandler rejects 301, 302, 303, 307, 308
    handler = VoiceNoRedirectHandler()
    opener = build_voice_no_redirect_opener()
    assert any(isinstance(h, VoiceNoRedirectHandler) for h in opener.handlers)
    dummy_req = urllib.request.Request("https://speech.example.com/v1/audio/transcriptions")
    for redirect_code in (301, 302, 303, 307, 308):
        with pytest.raises(VoiceTransportError, match=f"HTTP {redirect_code} redirect"):
            handler.redirect_request(
                dummy_req,
                io.BytesIO(b""),
                redirect_code,
                "Redirect",
                {},
                "https://untrusted.example.org/v1/audio",
            )

        def redirect_transport(
            req: urllib.request.Request,
            _timeout: float,
            code: int = redirect_code,
        ) -> bytes:
            raise urllib.error.HTTPError(
                url=req.full_url,
                code=code,
                msg="Redirect",
                hdrs=None,  # type: ignore[arg-type]
                fp=io.BytesIO(b""),
            )

        stt_redir = RemoteHTTPSTTProvider(
            cfg_stt,
            transport=redirect_transport,
            consent_manager=VoiceConsentManager(handler=lambda _r: True),
        )
        with pytest.raises(VoiceTransportError, match=f"HTTP {redirect_code} redirect"):
            stt_redir.transcribe(audio)

    # Successful mocked transport roundtrip for STT and TTS with log inspection
    def mock_stt_transport(req: urllib.request.Request, _timeout: float) -> bytes:
        assert req.get_header("Authorization") == f"Bearer {runtime_key}"
        return json.dumps(
            {"text": private_transcript, "language": "en", "confidence": 0.98}
        ).encode("utf-8")

    wav_reply = encode_pcm16_to_wav_bytes(b"\x04\x00" * 800, sample_rate=16000, channels=1)

    def mock_tts_transport(req: urllib.request.Request, _timeout: float) -> bytes:
        assert req.get_header("Authorization") == f"Bearer {runtime_key}"
        return wav_reply

    with caplog.at_level(logging.DEBUG):
        stt = RemoteHTTPSTTProvider(
            cfg_stt,
            transport=mock_stt_transport,
            consent_manager=VoiceConsentManager(handler=lambda _r: True),
        )
        res = stt.transcribe(audio)
        assert res.text == private_transcript

        tts = RemoteHTTPTTSProvider(
            cfg_tts,
            transport=mock_tts_transport,
            consent_manager=VoiceConsentManager(handler=lambda _r: True),
        )
        speech = tts.synthesize(private_tts_text)
        assert speech.byte_size == len(wav_reply)

    for record in caplog.records:
        msg = record.getMessage()
        assert runtime_key not in msg
        assert private_transcript not in msg
        assert private_tts_text not in msg


def test_remote_providers_block_live_network_under_pytest_and_reject_malformed_json() -> None:
    """Default transport blocks live network calls in pytest; malformed responses fail safely."""
    audio = _sample_audio()
    cfg = RemoteVoiceConfig(
        endpoint="https://speech.example.com/v1/audio/transcriptions",
        api_key="test-key",
    )
    stt_live = RemoteHTTPSTTProvider(
        cfg,
        consent_manager=VoiceConsentManager(handler=lambda _r: True),
    )
    with pytest.raises(VoiceTransportError, match="Live network calls are disabled"):
        stt_live.transcribe(audio)

    stt_bad_json = RemoteHTTPSTTProvider(
        cfg,
        transport=lambda _req, _t: b"not-json",
        consent_manager=VoiceConsentManager(handler=lambda _r: True),
    )
    with pytest.raises(MalformedSpeechResponseError, match="invalid JSON"):
        stt_bad_json.transcribe(audio)
