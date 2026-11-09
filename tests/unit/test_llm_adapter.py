"""Unit tests for the isolated OpenAI-compatible LLM adapter."""

from __future__ import annotations

import email.message
import io
import json
import logging
import secrets
import urllib.error
import urllib.request
import urllib.response

import pytest
from PySide6.QtGui import QColor, QImage

from octa.core.config import AppConfig
from octa.intelligence.capture import (
    CapturedImage,
    CaptureRegion,
    encode_qimage_to_png_bytes,
)
from octa.intelligence.consent import ConsentDecision, ConsentManager, ConsentRequest
from octa.intelligence.exceptions import (
    ConsentDeclinedError,
    LLMError,
    LLMTransportError,
    MalformedModelOutputError,
    MissingCredentialsError,
)
from octa.intelligence.llm import (
    REDIRECT_STATUS_CODES,
    LLMConfig,
    OpenAICompatibleAdapter,
    build_no_redirect_opener,
    extract_json_object,
)


def _make_sample_capture() -> CapturedImage:
    img = QImage(32, 24, QImage.Format.Format_ARGB32)
    img.fill(QColor(50, 100, 150))
    return CapturedImage(
        png_bytes=encode_qimage_to_png_bytes(img),
        width=32,
        height=24,
        region=CaptureRegion(x=0, y=0, width=32, height=24),
        source="full_screen",
    )


def _encode_chat_response(content: str, model: str = "gpt-4o-mini") -> bytes:
    payload = {
        "id": "chatcmpl-123",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
    }
    return json.dumps(payload).encode("utf-8")


def test_llm_config_loads_from_environment_and_masks_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLMConfig reads endpoint, model, and credential without exposing the secret."""
    runtime_credential = secrets.token_urlsafe(16)
    monkeypatch.setenv("OCTA_LLM_ENDPOINT", "https://llm.example.com/v1/chat/completions")
    monkeypatch.setenv("OCTA_LLM_MODEL", "vision-instruct-v1")
    monkeypatch.setenv("OCTA_LLM_API_KEY", runtime_credential)

    app_cfg = AppConfig.load()
    llm_cfg = LLMConfig.from_app_config(app_cfg)

    assert llm_cfg.endpoint == "https://llm.example.com/v1/chat/completions"
    assert llm_cfg.model == "vision-instruct-v1"
    assert llm_cfg.has_credentials is True
    assert llm_cfg.require_api_key() == runtime_credential

    # Secret must never appear in repr or str
    assert runtime_credential not in repr(llm_cfg)
    assert runtime_credential not in str(llm_cfg)


def test_missing_credentials_raises_before_network_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Attempting completion without a configured credential raises MissingCredentialsError."""
    monkeypatch.delenv("OCTA_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    transport_called = False

    def fail_transport(_req: urllib.request.Request, _timeout: float) -> bytes:
        nonlocal transport_called
        transport_called = True
        return b""

    cfg = LLMConfig(api_key=None)
    adapter = OpenAICompatibleAdapter(cfg, transport=fail_transport)

    with pytest.raises(MissingCredentialsError, match="No LLM API key configured"):
        adapter.complete_text(
            system_prompt="You are a tutor.",
            user_prompt="Explain step 1.",
        )

    assert transport_called is False


def test_live_network_calls_blocked_during_pytest() -> None:
    """Default transport refuses to make live HTTP calls during pytest execution."""
    runtime_credential = secrets.token_urlsafe(16)
    cfg = LLMConfig(api_key=runtime_credential)
    adapter = OpenAICompatibleAdapter(cfg)

    with pytest.raises(LLMTransportError, match="Live network calls are disabled"):
        adapter.complete_text(
            system_prompt="System",
            user_prompt="User",
        )


def test_complete_with_image_requires_consent_and_allows_cancellation() -> None:
    """Cancelling user consent raises ConsentDeclinedError and prevents HTTP transmission."""
    runtime_credential = secrets.token_urlsafe(16)
    captured = _make_sample_capture()
    transport_calls: list[urllib.request.Request] = []

    def spy_transport(req: urllib.request.Request, _timeout: float) -> bytes:
        transport_calls.append(req)
        return _encode_chat_response('{"status": "complete"}')

    # 1. No ConsentManager provided -> declined
    adapter_no_consent = OpenAICompatibleAdapter(
        LLMConfig(api_key=runtime_credential),
        transport=spy_transport,
    )
    with pytest.raises(ConsentDeclinedError, match="User consent is required"):
        adapter_no_consent.complete_with_image(
            system_prompt="System",
            user_prompt="Verify step",
            image=captured,
        )
    assert transport_calls == []

    # 2. User cancels via ConsentManager -> declined, no HTTP request sent
    cancel_manager = ConsentManager(handler=lambda _req: False)
    adapter_cancelled = OpenAICompatibleAdapter(
        LLMConfig(api_key=runtime_credential),
        transport=spy_transport,
        consent_manager=cancel_manager,
    )
    with pytest.raises(ConsentDeclinedError, match="Cancelled by consent handler"):
        adapter_cancelled.complete_with_image(
            system_prompt="System",
            user_prompt="Verify step",
            image=captured,
        )
    assert transport_calls == []


def test_changed_prompt_or_endpoint_or_reused_approval_rejected_before_network() -> None:
    """A changed prompt, changed endpoint, or reused ConsentDecision cannot send the same image."""
    runtime_credential = secrets.token_urlsafe(16)
    captured = _make_sample_capture()
    transport_calls: list[urllib.request.Request] = []

    def spy_transport(req: urllib.request.Request, _timeout: float) -> bytes:
        transport_calls.append(req)
        return _encode_chat_response('{"status": "complete"}')

    adapter_primary = OpenAICompatibleAdapter(
        LLMConfig(
            endpoint="https://api.primary.example.com/v1/chat/completions",
            model="gpt-4o-mini",
            api_key=runtime_credential,
        ),
        transport=spy_transport,
    )

    approved_request = ConsentRequest.from_capture(
        captured,
        purpose="Verify step 1",
        endpoint_url="https://api.primary.example.com/v1/chat/completions",
        model="gpt-4o-mini",
        task_title="Notepad Task",
        step_title="Step 1",
        system_prompt="Verify screenshot.",
        user_prompt="Check if File menu is open.",
    )
    decision = ConsentDecision.from_request(approved_request, approved=True)

    # 1. Attempting to send the SAME image with a changed user prompt -> rejected before network
    with pytest.raises(ConsentDeclinedError, match="prompt content does not match"):
        adapter_primary.complete_with_image(
            system_prompt="Verify screenshot.",
            user_prompt="Read all private text in the window.",
            image=captured,
            purpose="Verify step 1",
            task_title="Notepad Task",
            step_title="Step 1",
            consent_decision=decision,
        )
    assert transport_calls == []

    # 2. Attempting to send the SAME image with a changed system prompt -> rejected before network
    with pytest.raises(ConsentDeclinedError, match="prompt content does not match"):
        adapter_primary.complete_with_image(
            system_prompt="Modified system prompt.",
            user_prompt="Check if File menu is open.",
            image=captured,
            purpose="Verify step 1",
            task_title="Notepad Task",
            step_title="Step 1",
            consent_decision=decision,
        )
    assert transport_calls == []

    # 3. Attempting to send the SAME image to a different endpoint -> rejected before network
    adapter_secondary = OpenAICompatibleAdapter(
        LLMConfig(
            endpoint="https://api.secondary.example.com/v1/chat/completions",
            model="gpt-4o-mini",
            api_key=runtime_credential,
        ),
        transport=spy_transport,
    )
    with pytest.raises(ConsentDeclinedError, match="endpoint does not match"):
        adapter_secondary.complete_with_image(
            system_prompt="Verify screenshot.",
            user_prompt="Check if File menu is open.",
            image=captured,
            purpose="Verify step 1",
            task_title="Notepad Task",
            step_title="Step 1",
            consent_decision=decision,
        )
    assert transport_calls == []

    # 4. Exact approved request succeeds once
    adapter_primary.complete_with_image(
        system_prompt="Verify screenshot.",
        user_prompt="Check if File menu is open.",
        image=captured,
        purpose="Verify step 1",
        task_title="Notepad Task",
        step_title="Step 1",
        consent_decision=decision,
    )
    assert len(transport_calls) == 1

    # 5. Reusing the same ConsentDecision for a second identical call -> rejected before network
    with pytest.raises(ConsentDeclinedError, match="already been used"):
        adapter_primary.complete_with_image(
            system_prompt="Verify screenshot.",
            user_prompt="Check if File menu is open.",
            image=captured,
            purpose="Verify step 1",
            task_title="Notepad Task",
            step_title="Step 1",
            consent_decision=decision,
        )
    assert len(transport_calls) == 1


def test_changing_only_query_parameter_or_url_credential_rejected_by_adapter() -> None:
    """Adapter rejects approval reuse when only a query parameter or URL credential changes."""
    runtime_credential = secrets.token_urlsafe(16)
    url_cred_1 = secrets.token_urlsafe(12)
    url_cred_2 = secrets.token_urlsafe(12)
    captured = _make_sample_capture()
    transport_calls: list[urllib.request.Request] = []

    def spy_transport(req: urllib.request.Request, _timeout: float) -> bytes:
        transport_calls.append(req)
        return _encode_chat_response('{"status": "complete"}')

    # 1. Approval bound to query parameter ?deployment=alpha cannot be used for ?deployment=beta
    req_query_alpha = ConsentRequest.from_capture(
        captured,
        purpose="Verify step 1",
        endpoint_url="https://api.example.com/v1/chat/completions?deployment=alpha",
        model="gpt-4o-mini",
        task_title="Notepad Task",
        step_title="Step 1",
        system_prompt="Verify screenshot.",
        user_prompt="Check if File menu is open.",
    )
    decision_query_alpha = ConsentDecision.from_request(req_query_alpha, approved=True)

    adapter_query_beta = OpenAICompatibleAdapter(
        LLMConfig(
            endpoint="https://api.example.com/v1/chat/completions?deployment=beta",
            model="gpt-4o-mini",
            api_key=runtime_credential,
        ),
        transport=spy_transport,
    )
    with pytest.raises(ConsentDeclinedError, match="endpoint does not match"):
        adapter_query_beta.complete_with_image(
            system_prompt="Verify screenshot.",
            user_prompt="Check if File menu is open.",
            image=captured,
            purpose="Verify step 1",
            task_title="Notepad Task",
            step_title="Step 1",
            consent_decision=decision_query_alpha,
        )
    assert transport_calls == []

    # 2. Approval bound to URL credential user1:cred1 cannot be used for user2:cred2
    req_cred_1 = ConsentRequest.from_capture(
        captured,
        purpose="Verify step 1",
        endpoint_url=f"https://user1:{url_cred_1}@api.example.com/v1/chat/completions",
        model="gpt-4o-mini",
        task_title="Notepad Task",
        step_title="Step 1",
        system_prompt="Verify screenshot.",
        user_prompt="Check if File menu is open.",
    )
    decision_cred_1 = ConsentDecision.from_request(req_cred_1, approved=True)

    cfg_cred_2 = LLMConfig(
        endpoint=f"https://user2:{url_cred_2}@api.example.com/v1/chat/completions",
        model="gpt-4o-mini",
        api_key=runtime_credential,
    )
    # Ensure URL credential is never exposed in repr(LLMConfig)
    assert url_cred_2 not in repr(cfg_cred_2)
    assert "user2" not in repr(cfg_cred_2)

    adapter_cred_2 = OpenAICompatibleAdapter(cfg_cred_2, transport=spy_transport)
    with pytest.raises(ConsentDeclinedError, match="endpoint does not match"):
        adapter_cred_2.complete_with_image(
            system_prompt="Verify screenshot.",
            user_prompt="Check if File menu is open.",
            image=captured,
            purpose="Verify step 1",
            task_title="Notepad Task",
            step_title="Step 1",
            consent_decision=decision_cred_1,
        )
    assert transport_calls == []


@pytest.mark.parametrize("redirect_code", sorted(REDIRECT_STATUS_CODES))
def test_redirect_policy_blocks_forwarding_image_and_bearer_key(redirect_code: int) -> None:
    """HTTP redirects are blocked and never forward the image or bearer key to a new URL."""
    runtime_credential = secrets.token_urlsafe(16)
    captured = _make_sample_capture()
    seen_urls: list[str] = []

    class _MockRedirectHTTPSHandler(urllib.request.HTTPSHandler):
        def https_open(self, req: urllib.request.Request) -> urllib.response.addinfourl:
            seen_urls.append(req.full_url)
            hdrs = email.message.Message()
            hdrs["Location"] = "https://unapproved-redirect.example.com/v1/chat/completions"
            raise urllib.error.HTTPError(
                url=req.full_url,
                code=redirect_code,
                msg="Redirect",
                hdrs=hdrs,
                fp=io.BytesIO(b""),
            )

    opener = build_no_redirect_opener(_MockRedirectHTTPSHandler())

    def opener_transport(req: urllib.request.Request, timeout: float) -> bytes:
        with opener.open(req, timeout=timeout) as resp:
            return bytes(resp.read())

    adapter = OpenAICompatibleAdapter(
        LLMConfig(
            endpoint="https://api.example.com/v1/chat/completions",
            model="gpt-4o-mini",
            api_key=runtime_credential,
        ),
        transport=opener_transport,
        consent_manager=ConsentManager(handler=lambda _req: True),
    )

    with pytest.raises(
        LLMTransportError,
        match=f"HTTP {redirect_code} redirect.*without fresh user consent",
    ) as exc_info:
        adapter.complete_with_image(
            system_prompt="Verify screenshot.",
            user_prompt="Check if File menu is open.",
            image=captured,
            purpose="Verify step 1",
            task_title="Notepad Task",
            step_title="Step 1",
        )

    assert runtime_credential not in str(exc_info.value)
    # Only the original approved endpoint was contacted; the redirect target was never called
    assert seen_urls == ["https://api.example.com/v1/chat/completions"]


def test_remote_http_rejected_before_sending_and_loopback_http_requires_opt_in() -> None:
    """Remote HTTP is always rejected before sending; loopback HTTP requires allow_local_http."""
    runtime_credential = secrets.token_urlsafe(16)
    transport_calls: list[urllib.request.Request] = []

    def spy_transport(req: urllib.request.Request, _timeout: float) -> bytes:
        transport_calls.append(req)
        return _encode_chat_response('{"status": "complete"}')

    # 1. Remote HTTP endpoint rejected at config creation (even if allow_local_http=True)
    with pytest.raises(LLMError, match="remote LLM endpoints must use HTTPS"):
        LLMConfig(
            endpoint="http://api.example.com/v1/chat/completions",
            api_key=runtime_credential,
        )

    with pytest.raises(LLMError, match="remote LLM endpoints must use HTTPS"):
        LLMConfig(
            endpoint="http://api.example.com/v1/chat/completions",
            api_key=runtime_credential,
            allow_local_http=True,
        )

    # 2. Even if _config.endpoint is mutated after creation to a remote HTTP URL,
    #    _send_chat_completion blocks it BEFORE sending any request or bearer token.
    cfg = LLMConfig(
        endpoint="https://api.example.com/v1/chat/completions",
        api_key=runtime_credential,
        allow_local_http=True,
    )
    adapter = OpenAICompatibleAdapter(cfg, transport=spy_transport)
    object.__setattr__(cfg, "endpoint", "http://remote-attacker.example.com/v1/chat/completions")

    with pytest.raises(LLMError, match="remote LLM endpoints must use HTTPS"):
        adapter.complete_text(system_prompt="System", user_prompt="User")
    assert transport_calls == []

    # 3. Loopback HTTP is rejected when allow_local_http=False (default)
    with pytest.raises(LLMError, match="allow_local_http=True"):
        LLMConfig(
            endpoint="http://127.0.0.1:11434/v1/chat/completions",
            api_key=runtime_credential,
        )

    # 4. Loopback HTTP is permitted only when allow_local_http=True
    local_cfg = LLMConfig(
        endpoint="http://127.0.0.1:11434/v1/chat/completions",
        api_key=runtime_credential,
        allow_local_http=True,
    )
    local_adapter = OpenAICompatibleAdapter(local_cfg, transport=spy_transport)
    resp = local_adapter.complete_text(system_prompt="System", user_prompt="User")
    assert resp.content == '{"status": "complete"}'
    assert len(transport_calls) == 1


def test_complete_with_image_sends_after_consent_without_logging_secrets_or_image(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Approved multimodal completion sends expected payload while keeping logs secret-free."""
    runtime_credential = secrets.token_urlsafe(16)
    captured = _make_sample_capture()
    captured_requests: list[urllib.request.Request] = []
    seen_consent_requests: list[ConsentRequest] = []

    def mock_transport(req: urllib.request.Request, _timeout: float) -> bytes:
        captured_requests.append(req)
        return _encode_chat_response('{"status": "complete", "confidence": 0.95}')

    def approve_handler(consent_req: ConsentRequest) -> bool:
        seen_consent_requests.append(consent_req)
        return True

    approve_manager = ConsentManager(handler=approve_handler)
    adapter = OpenAICompatibleAdapter(
        LLMConfig(
            endpoint="https://api.example.com/v1/chat/completions",
            model="gpt-4o-mini",
            api_key=runtime_credential,
        ),
        transport=mock_transport,
        consent_manager=approve_manager,
    )

    with caplog.at_level(logging.DEBUG, logger="octa.intelligence"):
        response = adapter.complete_with_image(
            system_prompt="Verify screenshot.",
            user_prompt="Check if File menu is open.",
            image=captured,
            purpose="Step verification",
            task_title="Notepad Task",
            step_title="Step 1",
        )

    assert response.content == '{"status": "complete", "confidence": 0.95}'
    assert response.usage["total_tokens"] == 20
    assert len(captured_requests) == 1
    assert len(seen_consent_requests) == 1
    assert seen_consent_requests[0].system_prompt == "Verify screenshot."
    assert seen_consent_requests[0].user_prompt == "Check if File menu is open."

    sent_req = captured_requests[0]
    body_json = json.loads(sent_req.data.decode("utf-8"))  # type: ignore[union-attr]
    assert body_json["model"] == "gpt-4o-mini"
    user_content = body_json["messages"][1]["content"]
    assert user_content[0] == {"type": "text", "text": "Check if File menu is open."}
    assert user_content[1]["image_url"]["url"] == captured.to_data_url()

    # Verify neither runtime credential nor base64 image appears in any log record
    b64_data = captured.to_base64()
    for record in caplog.records:
        msg = record.getMessage()
        assert runtime_credential not in msg
        assert b64_data not in msg
        assert "data:image/png" not in msg


def test_transport_error_redacts_credentials(caplog: pytest.LogCaptureFixture) -> None:
    """HTTP and network errors redact any accidental credential strings from messages and logs."""
    runtime_credential = secrets.token_urlsafe(16)

    def failing_transport(_req: urllib.request.Request, _timeout: float) -> bytes:
        raise urllib.error.URLError(
            f"Connection refused with header Authorization: Bearer {runtime_credential}"
        )

    adapter = OpenAICompatibleAdapter(
        LLMConfig(api_key=runtime_credential),
        transport=failing_transport,
    )

    with caplog.at_level(logging.DEBUG, logger="octa.intelligence.llm"):
        with pytest.raises(LLMTransportError) as exc_info:
            adapter.complete_text(
                system_prompt="System",
                user_prompt="User",
            )

    assert runtime_credential not in str(exc_info.value)
    assert "[REDACTED]" in str(exc_info.value)
    for record in caplog.records:
        assert runtime_credential not in record.getMessage()


@pytest.mark.parametrize(
    "raw_body,expected_match",
    [
        (b"", "empty HTTP response"),
        (b"not-valid-json", "non-JSON response"),
        (b'{"choices": []}', "non-empty 'choices' list"),
        (b'{"choices": [{"message": {}}]}', "non-empty string"),
        (b'{"choices": [{"message": {"content": "   "}}]}', "non-empty string"),
    ],
)
def test_malformed_http_responses_raise_malformed_model_output(
    raw_body: bytes,
    expected_match: str,
) -> None:
    """Adapter rejects empty, non-JSON, or schema-invalid chat completion payloads."""
    runtime_credential = secrets.token_urlsafe(16)
    adapter = OpenAICompatibleAdapter(
        LLMConfig(api_key=runtime_credential),
        transport=lambda _req, _t: raw_body,
    )

    with pytest.raises(MalformedModelOutputError, match=expected_match):
        adapter.complete_text(
            system_prompt="System",
            user_prompt="User",
        )


def test_extract_json_object_handles_fences_and_rejects_malformed() -> None:
    """extract_json_object parses bare or fenced JSON objects and rejects invalid text."""
    assert extract_json_object('{"a": 1}') == {"a": 1}
    assert extract_json_object('```json\n{"status": "complete"}\n```') == {"status": "complete"}

    with pytest.raises(MalformedModelOutputError, match="empty"):
        extract_json_object("   ")

    with pytest.raises(MalformedModelOutputError, match="not valid JSON"):
        extract_json_object("{bad json")

    with pytest.raises(MalformedModelOutputError, match="Expected JSON object"):
        extract_json_object("[1, 2, 3]")


def test_llm_config_validation_and_http_error_redaction() -> None:
    """LLMConfig validates parameters and HTTPError redacts credentials."""
    with pytest.raises(LLMError, match="Invalid LLM endpoint URL"):
        LLMConfig(endpoint="ws://localhost:8765")

    with pytest.raises(LLMError, match="timeout_seconds"):
        LLMConfig(timeout_seconds=-1.0)

    with pytest.raises(LLMError, match="max_tokens"):
        LLMConfig(max_tokens=0)

    with pytest.raises(LLMError, match="temperature"):
        LLMConfig(temperature=3.5)

    runtime_credential = secrets.token_urlsafe(16)

    def http_error_transport(req: urllib.request.Request, _timeout: float) -> bytes:
        raise urllib.error.HTTPError(
            url=req.full_url,
            code=401,
            msg=f"Unauthorized for Bearer {runtime_credential}",
            hdrs=None,  # type: ignore[arg-type]
            fp=io.BytesIO(b""),
        )

    adapter = OpenAICompatibleAdapter(
        LLMConfig(api_key=runtime_credential),
        transport=http_error_transport,
    )
    with pytest.raises(LLMTransportError) as exc_info:
        adapter.complete_text(system_prompt="Sys", user_prompt="Usr")

    assert "401" in str(exc_info.value)
    assert runtime_credential not in str(exc_info.value)
    assert "[REDACTED]" in str(exc_info.value)
