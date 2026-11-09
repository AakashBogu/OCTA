"""Isolated, configurable OpenAI-compatible LLM adapter for OCTA.

Reads endpoint, model, and API key from AppConfig/environment. Requires HTTPS
for all remote LLM endpoints (allowing plain HTTP only for loopback hosts when
explicitly enabled via allow_local_http / OCTA_LLM_ALLOW_LOCAL_HTTP). Never
sends a bearer API key to a non-HTTPS remote endpoint, disables automatic HTTP
redirects so credentials and screenshots are never forwarded to an unapproved
destination, never logs API keys or captured image payloads, requires explicit
user consent bound to the complete outbound request (including exact endpoint
URL digest) before transmitting screen content, and blocks live HTTP calls
during pytest.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from octa.core.config import (
    DEFAULT_LLM_ALLOW_LOCAL_HTTP,
    DEFAULT_LLM_ENDPOINT,
    DEFAULT_LLM_MODEL,
    DEFAULT_LLM_TIMEOUT_SECONDS,
    AppConfig,
    is_loopback_host,
)
from octa.intelligence.capture import CapturedImage
from octa.intelligence.consent import (
    ConsentDecision,
    ConsentManager,
    ConsentRequest,
    sanitize_endpoint_for_display,
)
from octa.intelligence.exceptions import (
    ConsentDeclinedError,
    LLMError,
    LLMTransportError,
    MalformedModelOutputError,
    MissingCredentialsError,
)

logger = logging.getLogger("octa.intelligence.llm")

_BEARER_PATTERN = re.compile(r"(Bearer\s+)\S+", re.IGNORECASE)
_URL_USERINFO_PATTERN = re.compile(r"(https?://)([^/@\s'\"]+)@", re.IGNORECASE)
_URL_QUERY_OR_FRAGMENT_PATTERN = re.compile(r"(https?://[^\s?#'\"]+)[?#][^\s'\"]*", re.IGNORECASE)
_CODE_FENCE_PATTERN = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)
REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})


def redact_sensitive_text(text: str, api_key: str | None = None) -> str:
    """Redact API keys, Bearer tokens, URL credentials, and URL queries/fragments."""
    if not text:
        return ""
    redacted = _BEARER_PATTERN.sub(r"\1[REDACTED]", text)
    redacted = _URL_USERINFO_PATTERN.sub(r"\1[REDACTED]@", redacted)
    redacted = _URL_QUERY_OR_FRAGMENT_PATTERN.sub(r"\1", redacted)
    if api_key and api_key.strip():
        redacted = redacted.replace(api_key.strip(), "[REDACTED]")
    return redacted


def validate_llm_endpoint(endpoint: str, *, allow_local_http: bool = False) -> str:
    """Validate an LLM endpoint URL and enforce HTTPS for all non-loopback hosts.

    Plain HTTP is permitted only when the target host is a loopback address
    (localhost, 127.0.0.0/8, ::1) AND allow_local_http is explicitly True.
    """
    if not isinstance(endpoint, str) or not endpoint.strip():
        raise LLMError("LLMConfig endpoint must be a non-empty URL string.")
    if not isinstance(allow_local_http, bool):
        raise LLMError("LLMConfig allow_local_http must be a boolean.")

    cleaned_endpoint = endpoint.strip()
    parsed = urlparse(cleaned_endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not parsed.hostname:
        raise LLMError(
            f"Invalid LLM endpoint URL '{redact_sensitive_text(cleaned_endpoint)}'. "
            "Must use https (or http for loopback local development)."
        )

    if parsed.scheme == "http":
        if not is_loopback_host(parsed.hostname):
            raise LLMError(
                f"Insecure HTTP endpoint '{redact_sensitive_text(cleaned_endpoint)}' is rejected: "
                "remote LLM endpoints must use HTTPS."
            )
        if not allow_local_http:
            raise LLMError(
                f"Local HTTP endpoint '{redact_sensitive_text(cleaned_endpoint)}' is disabled "
                "by default. Set allow_local_http=True (or OCTA_LLM_ALLOW_LOCAL_HTTP=true) "
                "to permit loopback HTTP for local development."
            )

    return cleaned_endpoint


@dataclass(frozen=True)
class LLMConfig:
    """Configuration for the OpenAI-compatible LLM adapter.

    The API key is excluded from repr/str so it can never be exposed in logs.
    """

    endpoint: str = field(default=DEFAULT_LLM_ENDPOINT, repr=False)
    model: str = DEFAULT_LLM_MODEL
    api_key: str | None = field(default=None, repr=False)
    timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS
    max_tokens: int = 1024
    temperature: float = 0.1
    allow_local_http: bool = DEFAULT_LLM_ALLOW_LOCAL_HTTP

    def __post_init__(self) -> None:
        cleaned_endpoint = validate_llm_endpoint(
            self.endpoint,
            allow_local_http=self.allow_local_http,
        )
        object.__setattr__(self, "endpoint", cleaned_endpoint)

        if not isinstance(self.model, str) or not self.model.strip():
            raise LLMError("LLMConfig model must be a non-empty string.")
        object.__setattr__(self, "model", self.model.strip())

        if self.api_key is not None:
            if not isinstance(self.api_key, str):
                raise LLMError("LLMConfig api_key must be a string or None.")
            cleaned_key = self.api_key.strip()
            object.__setattr__(self, "api_key", cleaned_key if cleaned_key else None)

        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or self.timeout_seconds <= 0
        ):
            raise LLMError("LLMConfig timeout_seconds must be a positive number.")
        object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))

        if (
            isinstance(self.max_tokens, bool)
            or not isinstance(self.max_tokens, int)
            or self.max_tokens < 1
        ):
            raise LLMError("LLMConfig max_tokens must be a positive integer.")

        if (
            isinstance(self.temperature, bool)
            or not isinstance(self.temperature, (int, float))
            or self.temperature < 0.0
            or self.temperature > 2.0
        ):
            raise LLMError("LLMConfig temperature must be between 0.0 and 2.0.")
        object.__setattr__(self, "temperature", float(self.temperature))

    def __repr__(self) -> str:
        safe_ep = sanitize_endpoint_for_display(self.endpoint)
        return (
            f"LLMConfig(endpoint={safe_ep!r}, model={self.model!r}, "
            f"has_api_key={self.has_credentials}, timeout_seconds={self.timeout_seconds}, "
            f"max_tokens={self.max_tokens}, temperature={self.temperature}, "
            f"allow_local_http={self.allow_local_http})"
        )

    def __str__(self) -> str:
        return self.__repr__()

    @property
    def has_credentials(self) -> bool:
        """Return True if a non-empty API key is configured."""
        return bool(self.api_key and self.api_key.strip())

    def require_api_key(self) -> str:
        """Return the configured API key or raise MissingCredentialsError."""
        if not self.api_key or not self.api_key.strip():
            raise MissingCredentialsError(
                "No LLM API key configured. Set OCTA_LLM_API_KEY in the environment or .env file."
            )
        return self.api_key.strip()

    @classmethod
    def from_app_config(
        cls,
        config: AppConfig | None = None,
        **overrides: Any,
    ) -> LLMConfig:
        """Create an LLMConfig from an AppConfig instance or environment defaults."""
        resolved = config if config is not None else AppConfig.load()
        kwargs: dict[str, Any] = {
            "endpoint": resolved.llm_endpoint,
            "model": resolved.llm_model,
            "api_key": resolved.llm_api_key,
            "timeout_seconds": resolved.llm_timeout_seconds,
            "allow_local_http": resolved.llm_allow_local_http,
        }
        kwargs.update(overrides)
        return cls(**kwargs)


@dataclass(frozen=True)
class LLMResponse:
    """Validated response returned from an OpenAI-compatible Chat Completions endpoint."""

    content: str
    model: str
    finish_reason: str = ""
    usage: dict[str, int] = field(default_factory=dict)


def extract_json_object(raw_text: str) -> dict[str, Any]:
    """Extract and parse a JSON object dictionary from model text output.

    Supports both bare JSON objects and markdown-fenced ```json ... ``` blocks.
    Raises MalformedModelOutputError if the content is not a valid JSON object.
    """
    if not isinstance(raw_text, str) or not raw_text.strip():
        raise MalformedModelOutputError("Model output is empty; expected a JSON object.")

    cleaned = raw_text.strip()
    fence_match = _CODE_FENCE_PATTERN.match(cleaned)
    if fence_match:
        cleaned = fence_match.group(1).strip()

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise MalformedModelOutputError(
            f"Model output is not valid JSON: {exc.msg} (line {exc.lineno}, col {exc.colno})."
        ) from exc

    if not isinstance(parsed, dict):
        raise MalformedModelOutputError(
            f"Expected JSON object dictionary from model, got {type(parsed).__name__}."
        )
    return parsed


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Explicitly reject all HTTP redirects for LLM requests.

    Prevents urllib from automatically forwarding the Authorization Bearer key
    or captured screen content to a redirected destination without fresh consent.
    """

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        raise LLMTransportError(
            f"HTTP {code} redirect from LLM endpoint is disabled by policy: "
            "refusing to forward bearer credentials or screen content to a "
            "redirected destination without fresh user consent."
        )


def build_no_redirect_opener(
    *extra_handlers: urllib.request.BaseHandler,
) -> urllib.request.OpenerDirector:
    """Build a urllib OpenerDirector that blocks all automatic HTTP redirects."""
    return urllib.request.build_opener(*extra_handlers, NoRedirectHandler())


type HTTPTransportCallable = Callable[[urllib.request.Request, float], bytes]


def _default_urllib_transport(request: urllib.request.Request, timeout: float) -> bytes:
    """Execute HTTP request via a no-redirect opener, blocking live calls under pytest."""
    if "pytest" in sys.modules:
        raise LLMTransportError(
            "Live network calls are disabled during automated tests; inject a mock transport."
        )
    opener = build_no_redirect_opener()
    with opener.open(request, timeout=timeout) as response:
        return bytes(response.read())


class OpenAICompatibleAdapter:
    """Isolated client for OpenAI-compatible `/v1/chat/completions` endpoints."""

    def __init__(
        self,
        config: LLMConfig | AppConfig | None = None,
        *,
        transport: HTTPTransportCallable | None = None,
        consent_manager: ConsentManager | None = None,
    ) -> None:
        if isinstance(config, LLMConfig):
            self._config = config
        else:
            self._config = LLMConfig.from_app_config(config)
        self._transport: HTTPTransportCallable = (
            transport if transport is not None else _default_urllib_transport
        )
        self._consent_manager = consent_manager

    @property
    def config(self) -> LLMConfig:
        """Adapter configuration."""
        return self._config

    def build_consent_request(
        self,
        *,
        image: CapturedImage,
        system_prompt: str,
        user_prompt: str,
        purpose: str = "Analyze screen progress",
        task_title: str = "OCTA Task",
        step_title: str = "Task Step",
    ) -> ConsentRequest:
        """Build the canonical ConsentRequest bound to the exact outbound multimodal payload."""
        if not isinstance(image, CapturedImage):
            raise LLMError("Expected a CapturedImage instance for multimodal completion.")
        if not isinstance(system_prompt, str) or not system_prompt.strip():
            raise LLMError("system_prompt must be a non-empty string.")
        if not isinstance(user_prompt, str) or not user_prompt.strip():
            raise LLMError("user_prompt must be a non-empty string.")

        return ConsentRequest.from_capture(
            image,
            purpose=purpose,
            endpoint_url=self._config.endpoint,
            model=self._config.model,
            task_title=task_title,
            step_title=step_title,
            system_prompt=system_prompt.strip(),
            user_prompt=user_prompt.strip(),
        )

    def complete_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        json_mode: bool = True,
    ) -> LLMResponse:
        """Send a text-only chat completion request without screen content."""
        if not isinstance(system_prompt, str) or not system_prompt.strip():
            raise LLMError("system_prompt must be a non-empty string.")
        if not isinstance(user_prompt, str) or not user_prompt.strip():
            raise LLMError("user_prompt must be a non-empty string.")

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt.strip()},
            {"role": "user", "content": user_prompt.strip()},
        ]
        return self._send_chat_completion(
            messages=messages,
            json_mode=json_mode,
            has_image=False,
        )

    def complete_with_image(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        image: CapturedImage,
        purpose: str = "Analyze screen progress",
        task_title: str = "OCTA Task",
        step_title: str = "Task Step",
        consent_decision: ConsentDecision | None = None,
        json_mode: bool = True,
    ) -> LLMResponse:
        """Send a multimodal chat completion request after verifying exact-request user consent."""
        consent_request = self.build_consent_request(
            image=image,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            purpose=purpose,
            task_title=task_title,
            step_title=step_title,
        )

        # 1. Require clear, single-use user consent bound to the exact outbound request
        #    (including the exact endpoint URL digest) BEFORE checking credentials or
        #    building/sending any network payload.
        if consent_decision is not None:
            consent_decision.validate_for_request(
                consent_request,
                image,
                consume=True,
            )
        elif self._consent_manager is not None:
            self._consent_manager.require_consent(
                consent_request,
                image,
                consume=True,
            )
        else:
            raise ConsentDeclinedError(
                "User consent is required before sending captured screen content "
                "to a remote service."
            )

        # 2. Construct multimodal message (image data URL stays in memory, never logged)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt.strip()},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_prompt.strip()},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": image.to_data_url(),
                            "detail": "auto",
                        },
                    },
                ],
            },
        ]
        return self._send_chat_completion(
            messages=messages,
            json_mode=json_mode,
            has_image=True,
        )

    def _send_chat_completion(
        self,
        *,
        messages: list[dict[str, Any]],
        json_mode: bool,
        has_image: bool,
    ) -> LLMResponse:
        """Validate transport security and credentials, execute HTTP POST, and parse response."""
        # Enforce HTTPS for remote endpoints (and explicit allow_local_http for loopback)
        # BEFORE accessing or attaching the Bearer API key.
        validated_endpoint = validate_llm_endpoint(
            self._config.endpoint,
            allow_local_http=self._config.allow_local_http,
        )
        parsed_target = urlparse(validated_endpoint)
        if parsed_target.scheme != "https" and not is_loopback_host(parsed_target.hostname):
            raise LLMError("Refusing to send bearer API key to a non-HTTPS remote endpoint.")

        api_key = self._config.require_api_key()

        payload: dict[str, Any] = {
            "model": self._config.model,
            "messages": messages,
            "temperature": self._config.temperature,
            "max_tokens": self._config.max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        body_bytes = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        request = urllib.request.Request(
            url=validated_endpoint,
            data=body_bytes,
            headers=headers,
            method="POST",
        )

        safe_endpoint = sanitize_endpoint_for_display(validated_endpoint)
        logger.info(
            "Sending LLM request (endpoint=%s, model=%s, has_image=%s).",
            safe_endpoint,
            self._config.model,
            has_image,
        )

        try:
            raw_response = self._transport(request, self._config.timeout_seconds)
        except LLMTransportError:
            raise
        except urllib.error.HTTPError as exc:
            if exc.code in REDIRECT_STATUS_CODES:
                msg = (
                    f"HTTP {exc.code} redirect from {safe_endpoint} is disabled by policy: "
                    "refusing to forward bearer credentials or screen content to a "
                    "redirected destination without fresh user consent."
                )
                logger.error(msg)
                raise LLMTransportError(msg) from None
            safe_reason = redact_sensitive_text(str(exc.reason), api_key)
            msg = f"LLM HTTP error {exc.code} from {safe_endpoint}: {safe_reason}"
            logger.error(msg)
            raise LLMTransportError(msg) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            safe_err = redact_sensitive_text(str(exc), api_key)
            msg = f"LLM transport failure communicating with {safe_endpoint}: {safe_err}"
            logger.error(msg)
            raise LLMTransportError(msg) from None
        except Exception as exc:
            safe_err = redact_sensitive_text(str(exc), api_key)
            msg = f"Unexpected error during LLM transport: {safe_err}"
            logger.error(msg)
            raise LLMTransportError(msg) from None

        return self._parse_completion_response(raw_response)

    def _parse_completion_response(self, raw_bytes: bytes) -> LLMResponse:
        """Validate and extract message content from an OpenAI-compatible JSON response."""
        if not isinstance(raw_bytes, (bytes, bytearray)) or not bytes(raw_bytes).strip():
            raise MalformedModelOutputError("LLM endpoint returned an empty HTTP response body.")

        try:
            decoded = bytes(raw_bytes).decode("utf-8")
            data = json.loads(decoded)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MalformedModelOutputError(
                f"LLM endpoint returned non-JSON response: {exc}"
            ) from exc

        if not isinstance(data, dict):
            raise MalformedModelOutputError("LLM response root must be a JSON object.")

        choices = data.get("choices")
        if not isinstance(choices, list) or len(choices) == 0:
            raise MalformedModelOutputError("LLM response is missing a non-empty 'choices' list.")

        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise MalformedModelOutputError("LLM response 'choices[0]' must be an object.")

        message = first_choice.get("message")
        if not isinstance(message, dict):
            raise MalformedModelOutputError("LLM response 'choices[0].message' must be an object.")

        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise MalformedModelOutputError(
                "LLM response 'choices[0].message.content' must be a non-empty string."
            )

        finish_reason = first_choice.get("finish_reason")
        if not isinstance(finish_reason, str):
            finish_reason = ""

        model_name = data.get("model")
        if not isinstance(model_name, str) or not model_name.strip():
            model_name = self._config.model

        usage_raw = data.get("usage")
        usage: dict[str, int] = {}
        if isinstance(usage_raw, dict):
            for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
                val = usage_raw.get(k)
                if isinstance(val, int) and not isinstance(val, bool):
                    usage[k] = val

        return LLMResponse(
            content=content,
            model=model_name,
            finish_reason=finish_reason,
            usage=usage,
        )
