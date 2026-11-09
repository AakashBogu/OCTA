"""Configuration management for OCTA.

Provides strongly-typed dataclasses, environment variable parsing,
safe Windows path resolution, and validation.
"""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from dotenv import dotenv_values


class ConfigError(ValueError):
    """Raised when configuration values are invalid or directories cannot be resolved."""


VALID_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})
VALID_VOICE_PROVIDERS = frozenset({"disabled", "mock", "remote_http"})
DEFAULT_LLM_ENDPOINT = "https://api.openai.com/v1/chat/completions"
DEFAULT_LLM_MODEL = "gpt-4o-mini"
DEFAULT_LLM_TIMEOUT_SECONDS = 30.0
DEFAULT_LLM_ALLOW_LOCAL_HTTP = False
DEFAULT_VOICE_PROVIDER = "disabled"
DEFAULT_VOICE_STT_MODEL = "whisper-1"
DEFAULT_VOICE_TTS_MODEL = "tts-1"
DEFAULT_VOICE_TTS_VOICE = "alloy"
DEFAULT_VOICE_TIMEOUT_SECONDS = 30.0
DEFAULT_VOICE_ALLOW_LOCAL_HTTP = False
DEFAULT_VOICE_MAX_RECORDING_SECONDS = 30.0


def _get_default_app_dir() -> Path:
    """Return default application data directory on Windows safely."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "OCTA"
    return Path.home() / "AppData" / "Local" / "OCTA"


def _parse_bool(value: Any, field_name: str) -> bool:
    """Safely parse boolean values from strings, ints, or booleans."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on", "t"}:
            return True
        if normalized in {"0", "false", "no", "off", "f", ""}:
            return False
    raise ConfigError(
        f"Invalid boolean value '{value}' for '{field_name}'. "
        "Expected 'true', 'false', '1', or '0'."
    )


def _parse_int(value: Any, field_name: str, min_value: int | None = None) -> int:
    """Safely parse integer values with optional minimum constraint."""
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"Invalid integer value '{value}' for '{field_name}'.") from exc
    if min_value is not None and parsed < min_value:
        raise ConfigError(f"Value for '{field_name}' must be at least {min_value}, got {parsed}.")
    return parsed


def _parse_float(value: Any, field_name: str, *, strictly_positive: bool = False) -> float:
    """Safely parse float values with optional strictly-positive constraint."""
    if isinstance(value, bool):
        raise ConfigError(f"Invalid float value '{value}' for '{field_name}'.")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"Invalid float value '{value}' for '{field_name}'.") from exc
    if strictly_positive and parsed <= 0:
        raise ConfigError(f"Value for '{field_name}' must be greater than 0, got {parsed}.")
    return parsed


def is_loopback_host(hostname: str | None) -> bool:
    """Return True only if hostname is localhost or an IPv4/IPv6 loopback address."""
    if not hostname or not isinstance(hostname, str):
        return False
    normalized = hostname.strip().strip("[]").lower()
    if normalized == "localhost":
        return True
    try:
        addr = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    return addr.is_loopback


def _validate_endpoint_url(
    value: Any,
    field_name: str,
    *,
    allow_local_http: bool = False,
) -> str:
    """Validate that an endpoint is HTTPS, or loopback HTTP when explicitly permitted."""
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"Field '{field_name}' must be a non-empty URL string.")
    cleaned = value.strip()
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not parsed.hostname:
        raise ConfigError(
            f"Invalid URL '{cleaned}' for '{field_name}'. Must use https (or loopback http) "
            "with a host."
        )
    if parsed.scheme == "http":
        if not is_loopback_host(parsed.hostname):
            raise ConfigError(
                f"Insecure remote HTTP endpoint '{cleaned}' is not permitted for '{field_name}'. "
                "Remote LLM endpoints must use HTTPS."
            )
        if not allow_local_http:
            raise ConfigError(
                f"Loopback HTTP endpoint '{cleaned}' for '{field_name}' requires "
                "llm_allow_local_http=True (OCTA_LLM_ALLOW_LOCAL_HTTP=true)."
            )
    return cleaned


@dataclass(frozen=True)
class AppConfig:
    """Central application configuration for OCTA."""

    app_name: str = "OCTA"
    env: str = "production"
    app_dir: Path = field(default_factory=_get_default_app_dir)
    log_dir: Path | None = None
    log_level: str = "INFO"
    log_to_file: bool = True
    log_to_console: bool = True
    log_max_bytes: int = 5 * 1024 * 1024  # 5 MB
    log_backup_count: int = 3
    godot_executable: Path | None = None
    godot_project_dir: Path = field(default_factory=lambda: Path("godot"))
    godot_auto_start: bool = False
    start_minimized: bool = False
    llm_endpoint: str = DEFAULT_LLM_ENDPOINT
    llm_model: str = DEFAULT_LLM_MODEL
    llm_api_key: str | None = field(default=None, repr=False)
    llm_timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS
    llm_allow_local_http: bool = False
    capture_save_screenshots: bool = False
    voice_stt_provider: str = DEFAULT_VOICE_PROVIDER
    voice_tts_provider: str = DEFAULT_VOICE_PROVIDER
    voice_stt_endpoint: str | None = field(default=None, repr=False)
    voice_stt_model: str = DEFAULT_VOICE_STT_MODEL
    voice_stt_api_key: str | None = field(default=None, repr=False)
    voice_tts_endpoint: str | None = field(default=None, repr=False)
    voice_tts_model: str = DEFAULT_VOICE_TTS_MODEL
    voice_tts_voice: str = DEFAULT_VOICE_TTS_VOICE
    voice_tts_api_key: str | None = field(default=None, repr=False)
    voice_timeout_seconds: float = DEFAULT_VOICE_TIMEOUT_SECONDS
    voice_allow_local_http: bool = DEFAULT_VOICE_ALLOW_LOCAL_HTTP
    voice_save_audio: bool = False
    voice_max_recording_seconds: float = DEFAULT_VOICE_MAX_RECORDING_SECONDS

    def __post_init__(self) -> None:
        """Validate configuration values and normalize types."""
        # Validate log level
        object.__setattr__(self, "log_level", self.log_level.upper())
        if self.log_level not in VALID_LOG_LEVELS:
            valid_str = ", ".join(sorted(VALID_LOG_LEVELS))
            raise ConfigError(f"Invalid log_level '{self.log_level}'. Must be one of: {valid_str}.")

        # Validate integers
        object.__setattr__(
            self, "log_max_bytes", _parse_int(self.log_max_bytes, "log_max_bytes", min_value=1)
        )
        object.__setattr__(
            self,
            "log_backup_count",
            _parse_int(self.log_backup_count, "log_backup_count", min_value=0),
        )

        # Validate and normalize booleans
        object.__setattr__(self, "log_to_file", _parse_bool(self.log_to_file, "log_to_file"))
        object.__setattr__(
            self, "log_to_console", _parse_bool(self.log_to_console, "log_to_console")
        )
        object.__setattr__(
            self, "godot_auto_start", _parse_bool(self.godot_auto_start, "godot_auto_start")
        )
        object.__setattr__(
            self, "start_minimized", _parse_bool(self.start_minimized, "start_minimized")
        )
        object.__setattr__(
            self,
            "llm_allow_local_http",
            _parse_bool(self.llm_allow_local_http, "llm_allow_local_http"),
        )
        object.__setattr__(
            self,
            "capture_save_screenshots",
            _parse_bool(self.capture_save_screenshots, "capture_save_screenshots"),
        )

        # Validate LLM settings
        object.__setattr__(
            self,
            "llm_endpoint",
            _validate_endpoint_url(
                self.llm_endpoint,
                "llm_endpoint",
                allow_local_http=self.llm_allow_local_http,
            ),
        )
        if not isinstance(self.llm_model, str) or not self.llm_model.strip():
            raise ConfigError("Field 'llm_model' must be a non-empty string.")
        object.__setattr__(self, "llm_model", self.llm_model.strip())

        if self.llm_api_key is not None:
            if not isinstance(self.llm_api_key, str):
                raise ConfigError("Field 'llm_api_key' must be a string or None.")
            cleaned_key = self.llm_api_key.strip()
            object.__setattr__(self, "llm_api_key", cleaned_key if cleaned_key else None)

        object.__setattr__(
            self,
            "llm_timeout_seconds",
            _parse_float(self.llm_timeout_seconds, "llm_timeout_seconds", strictly_positive=True),
        )

        # Validate Voice settings (Phase 5.0)
        object.__setattr__(
            self,
            "voice_allow_local_http",
            _parse_bool(self.voice_allow_local_http, "voice_allow_local_http"),
        )
        object.__setattr__(
            self,
            "voice_save_audio",
            _parse_bool(self.voice_save_audio, "voice_save_audio"),
        )
        object.__setattr__(
            self,
            "voice_timeout_seconds",
            _parse_float(
                self.voice_timeout_seconds,
                "voice_timeout_seconds",
                strictly_positive=True,
            ),
        )
        object.__setattr__(
            self,
            "voice_max_recording_seconds",
            _parse_float(
                self.voice_max_recording_seconds,
                "voice_max_recording_seconds",
                strictly_positive=True,
            ),
        )

        for provider_field in ("voice_stt_provider", "voice_tts_provider"):
            raw_provider = getattr(self, provider_field)
            if not isinstance(raw_provider, str) or not raw_provider.strip():
                raise ConfigError(f"Field '{provider_field}' must be a non-empty string.")
            normalized_provider = raw_provider.strip().lower()
            if normalized_provider not in VALID_VOICE_PROVIDERS:
                valid_providers = ", ".join(sorted(VALID_VOICE_PROVIDERS))
                raise ConfigError(
                    f"Invalid {provider_field} '{raw_provider}'. Must be one of: {valid_providers}."
                )
            object.__setattr__(self, provider_field, normalized_provider)

        for ep_field in ("voice_stt_endpoint", "voice_tts_endpoint"):
            raw_ep = getattr(self, ep_field)
            if raw_ep is not None:
                if not isinstance(raw_ep, str):
                    raise ConfigError(f"Field '{ep_field}' must be a URL string or None.")
                cleaned_ep = raw_ep.strip()
                if cleaned_ep:
                    validated_ep = _validate_endpoint_url(
                        cleaned_ep,
                        ep_field,
                        allow_local_http=self.voice_allow_local_http,
                    )
                    object.__setattr__(self, ep_field, validated_ep)
                else:
                    object.__setattr__(self, ep_field, None)

        for str_field in ("voice_stt_model", "voice_tts_model", "voice_tts_voice"):
            raw_val = getattr(self, str_field)
            if not isinstance(raw_val, str) or not raw_val.strip():
                raise ConfigError(f"Field '{str_field}' must be a non-empty string.")
            object.__setattr__(self, str_field, raw_val.strip())

        for key_field in ("voice_stt_api_key", "voice_tts_api_key"):
            raw_key = getattr(self, key_field)
            if raw_key is not None:
                if not isinstance(raw_key, str):
                    raise ConfigError(f"Field '{key_field}' must be a string or None.")
                cleaned_voice_key = raw_key.strip()
                object.__setattr__(
                    self,
                    key_field,
                    cleaned_voice_key if cleaned_voice_key else None,
                )

        # Normalize directories to Path
        if not isinstance(self.app_dir, Path):
            object.__setattr__(self, "app_dir", Path(self.app_dir))

        if self.log_dir is None:
            object.__setattr__(self, "log_dir", self.app_dir / "logs")
        elif not isinstance(self.log_dir, Path):
            object.__setattr__(self, "log_dir", Path(self.log_dir))

        if not isinstance(self.godot_project_dir, Path):
            object.__setattr__(self, "godot_project_dir", Path(self.godot_project_dir))

        if self.godot_executable is not None and not isinstance(self.godot_executable, Path):
            object.__setattr__(self, "godot_executable", Path(self.godot_executable))

    @classmethod
    def load(
        cls,
        env_file: Path | str | None = None,
        base_dir: Path | None = None,
        **overrides: Any,
    ) -> AppConfig:
        """Load configuration with precedence.

        Hierarchy: Defaults < .env file < Environment Variables < Overrides.
        """
        # 1. Values from .env file if available
        file_values: dict[str, Any] = {}
        if env_file is not None:
            env_path = Path(env_file)
            if env_path.is_file():
                file_values = {k: v for k, v in dotenv_values(env_path).items() if v is not None}
        else:
            default_env = Path(".env")
            if default_env.is_file():
                file_values = {k: v for k, v in dotenv_values(default_env).items() if v is not None}

        # Helper to retrieve value adhering to precedence: os.environ > file_values > default
        def get_val(env_key: str, default: Any = None) -> Any:
            if env_key in os.environ:
                return os.environ[env_key]
            if env_key in file_values:
                return file_values[env_key]
            return default

        # Resolve app_dir
        injected_app_dir = overrides.get("app_dir") or base_dir
        if injected_app_dir is None:
            env_app_dir = get_val("OCTA_APP_DIR")
            app_dir = Path(env_app_dir) if env_app_dir else _get_default_app_dir()
        else:
            app_dir = Path(injected_app_dir)

        # Resolve log_dir
        injected_log_dir = overrides.get("log_dir")
        if injected_log_dir is None:
            env_log_dir = get_val("OCTA_LOG_DIR")
            log_dir = Path(env_log_dir) if env_log_dir else app_dir / "logs"
        else:
            log_dir = Path(injected_log_dir)

        # Resolve LLM API key (never logged or included in repr)
        if "llm_api_key" in overrides:
            llm_api_key = overrides["llm_api_key"]
        else:
            llm_api_key = get_val("OCTA_LLM_API_KEY") or get_val("OPENAI_API_KEY")

        # Construct configuration kwargs
        kwargs: dict[str, Any] = {
            "app_name": overrides.get("app_name") or get_val("OCTA_APP_NAME", "OCTA"),
            "env": overrides.get("env") or get_val("OCTA_ENV", "production"),
            "app_dir": app_dir,
            "log_dir": log_dir,
            "log_level": overrides.get("log_level") or get_val("OCTA_LOG_LEVEL", "INFO"),
            "log_to_file": (
                overrides["log_to_file"]
                if "log_to_file" in overrides
                else _parse_bool(get_val("OCTA_LOG_TO_FILE", True), "log_to_file")
            ),
            "log_to_console": (
                overrides["log_to_console"]
                if "log_to_console" in overrides
                else _parse_bool(get_val("OCTA_LOG_TO_CONSOLE", True), "log_to_console")
            ),
            "log_max_bytes": (
                overrides["log_max_bytes"]
                if "log_max_bytes" in overrides
                else _parse_int(
                    get_val("OCTA_LOG_MAX_BYTES", 5 * 1024 * 1024),
                    "log_max_bytes",
                    min_value=1,
                )
            ),
            "log_backup_count": (
                overrides["log_backup_count"]
                if "log_backup_count" in overrides
                else _parse_int(
                    get_val("OCTA_LOG_BACKUP_COUNT", 3),
                    "log_backup_count",
                    min_value=0,
                )
            ),
            "godot_project_dir": (
                Path(overrides["godot_project_dir"])
                if "godot_project_dir" in overrides
                else Path(get_val("OCTA_GODOT_PROJECT_DIR", "godot"))
            ),
            "godot_auto_start": (
                overrides["godot_auto_start"]
                if "godot_auto_start" in overrides
                else _parse_bool(get_val("OCTA_GODOT_AUTO_START", False), "godot_auto_start")
            ),
            "start_minimized": (
                overrides["start_minimized"]
                if "start_minimized" in overrides
                else _parse_bool(get_val("OCTA_START_MINIMIZED", False), "start_minimized")
            ),
            "llm_endpoint": (
                overrides["llm_endpoint"]
                if "llm_endpoint" in overrides
                else (
                    get_val("OCTA_LLM_ENDPOINT")
                    or get_val("OPENAI_API_BASE")
                    or DEFAULT_LLM_ENDPOINT
                )
            ),
            "llm_model": (
                overrides["llm_model"]
                if "llm_model" in overrides
                else (get_val("OCTA_LLM_MODEL") or get_val("LLM_MODEL") or DEFAULT_LLM_MODEL)
            ),
            "llm_api_key": llm_api_key,
            "llm_timeout_seconds": (
                overrides["llm_timeout_seconds"]
                if "llm_timeout_seconds" in overrides
                else _parse_float(
                    get_val("OCTA_LLM_TIMEOUT", DEFAULT_LLM_TIMEOUT_SECONDS),
                    "llm_timeout_seconds",
                    strictly_positive=True,
                )
            ),
            "llm_allow_local_http": (
                overrides["llm_allow_local_http"]
                if "llm_allow_local_http" in overrides
                else _parse_bool(
                    get_val("OCTA_LLM_ALLOW_LOCAL_HTTP", False),
                    "llm_allow_local_http",
                )
            ),
            "capture_save_screenshots": (
                overrides["capture_save_screenshots"]
                if "capture_save_screenshots" in overrides
                else _parse_bool(
                    get_val("OCTA_CAPTURE_SAVE_SCREENSHOTS", False),
                    "capture_save_screenshots",
                )
            ),
            "voice_stt_provider": (
                overrides["voice_stt_provider"]
                if "voice_stt_provider" in overrides
                else get_val("OCTA_VOICE_STT_PROVIDER", DEFAULT_VOICE_PROVIDER)
            ),
            "voice_tts_provider": (
                overrides["voice_tts_provider"]
                if "voice_tts_provider" in overrides
                else get_val("OCTA_VOICE_TTS_PROVIDER", DEFAULT_VOICE_PROVIDER)
            ),
            "voice_stt_endpoint": (
                overrides["voice_stt_endpoint"]
                if "voice_stt_endpoint" in overrides
                else get_val("OCTA_VOICE_STT_ENDPOINT")
            ),
            "voice_stt_model": (
                overrides["voice_stt_model"]
                if "voice_stt_model" in overrides
                else get_val("OCTA_VOICE_STT_MODEL", DEFAULT_VOICE_STT_MODEL)
            ),
            "voice_stt_api_key": (
                overrides["voice_stt_api_key"]
                if "voice_stt_api_key" in overrides
                else (get_val("OCTA_VOICE_STT_API_KEY") or get_val("OCTA_VOICE_API_KEY"))
            ),
            "voice_tts_endpoint": (
                overrides["voice_tts_endpoint"]
                if "voice_tts_endpoint" in overrides
                else get_val("OCTA_VOICE_TTS_ENDPOINT")
            ),
            "voice_tts_model": (
                overrides["voice_tts_model"]
                if "voice_tts_model" in overrides
                else get_val("OCTA_VOICE_TTS_MODEL", DEFAULT_VOICE_TTS_MODEL)
            ),
            "voice_tts_voice": (
                overrides["voice_tts_voice"]
                if "voice_tts_voice" in overrides
                else get_val("OCTA_VOICE_TTS_VOICE", DEFAULT_VOICE_TTS_VOICE)
            ),
            "voice_tts_api_key": (
                overrides["voice_tts_api_key"]
                if "voice_tts_api_key" in overrides
                else (get_val("OCTA_VOICE_TTS_API_KEY") or get_val("OCTA_VOICE_API_KEY"))
            ),
            "voice_timeout_seconds": (
                overrides["voice_timeout_seconds"]
                if "voice_timeout_seconds" in overrides
                else _parse_float(
                    get_val("OCTA_VOICE_TIMEOUT", DEFAULT_VOICE_TIMEOUT_SECONDS),
                    "voice_timeout_seconds",
                    strictly_positive=True,
                )
            ),
            "voice_allow_local_http": (
                overrides["voice_allow_local_http"]
                if "voice_allow_local_http" in overrides
                else _parse_bool(
                    get_val("OCTA_VOICE_ALLOW_LOCAL_HTTP", DEFAULT_VOICE_ALLOW_LOCAL_HTTP),
                    "voice_allow_local_http",
                )
            ),
            "voice_save_audio": (
                overrides["voice_save_audio"]
                if "voice_save_audio" in overrides
                else _parse_bool(
                    get_val("OCTA_VOICE_SAVE_AUDIO", False),
                    "voice_save_audio",
                )
            ),
            "voice_max_recording_seconds": (
                overrides["voice_max_recording_seconds"]
                if "voice_max_recording_seconds" in overrides
                else _parse_float(
                    get_val(
                        "OCTA_VOICE_MAX_RECORDING_SECONDS",
                        DEFAULT_VOICE_MAX_RECORDING_SECONDS,
                    ),
                    "voice_max_recording_seconds",
                    strictly_positive=True,
                )
            ),
        }

        # Resolve Godot executable path
        godot_exe = (
            overrides.get("godot_executable")
            or get_val("OCTA_GODOT_PATH")
            or get_val("OCTA_GODOT_EXECUTABLE")
        )
        kwargs["godot_executable"] = Path(godot_exe) if godot_exe else None

        # Apply any explicit extra overrides
        for key, value in overrides.items():
            if key not in kwargs:
                kwargs[key] = value

        return cls(**kwargs)
