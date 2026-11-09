"""Unit tests for OCTA configuration management."""

from __future__ import annotations

from pathlib import Path

import pytest

from octa.core.config import AppConfig, ConfigError


def test_default_config() -> None:
    """Verify default configuration values."""
    config = AppConfig()
    assert config.app_name == "OCTA"
    assert config.env == "production"
    assert config.log_level == "INFO"
    assert config.log_to_file is True
    assert config.log_to_console is True
    assert config.log_max_bytes == 5 * 1024 * 1024
    assert config.log_backup_count == 3
    assert config.godot_auto_start is False
    assert config.start_minimized is False
    assert config.godot_project_dir == Path("godot")
    assert config.godot_executable is None
    assert config.log_dir == config.app_dir / "logs"


def test_explicit_directory_injection(tmp_path: Path) -> None:
    """Verify that test directories can be injected cleanly."""
    custom_app_dir = tmp_path / "custom_app"
    custom_log_dir = tmp_path / "custom_logs"

    config = AppConfig.load(
        base_dir=custom_app_dir,
        log_dir=custom_log_dir,
    )
    assert config.app_dir == custom_app_dir
    assert config.log_dir == custom_log_dir


def test_environment_variable_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verify that environment variables override defaults."""
    custom_app = tmp_path / "env_app"
    custom_logs = tmp_path / "env_logs"
    custom_godot = tmp_path / "bin" / "godot.exe"

    monkeypatch.setenv("OCTA_APP_NAME", "OCTA-Test")
    monkeypatch.setenv("OCTA_ENV", "development")
    monkeypatch.setenv("OCTA_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("OCTA_LOG_TO_FILE", "false")
    monkeypatch.setenv("OCTA_LOG_TO_CONSOLE", "0")
    monkeypatch.setenv("OCTA_LOG_MAX_BYTES", "1048576")
    monkeypatch.setenv("OCTA_LOG_BACKUP_COUNT", "5")
    monkeypatch.setenv("OCTA_APP_DIR", str(custom_app))
    monkeypatch.setenv("OCTA_LOG_DIR", str(custom_logs))
    monkeypatch.setenv("OCTA_GODOT_PATH", str(custom_godot))
    monkeypatch.setenv("OCTA_GODOT_AUTO_START", "true")
    monkeypatch.setenv("OCTA_START_MINIMIZED", "1")

    config = AppConfig.load()

    assert config.app_name == "OCTA-Test"
    assert config.env == "development"
    assert config.log_level == "DEBUG"
    assert config.log_to_file is False
    assert config.log_to_console is False
    assert config.log_max_bytes == 1048576
    assert config.log_backup_count == 5
    assert config.app_dir == custom_app
    assert config.log_dir == custom_logs
    assert config.godot_executable == custom_godot
    assert config.godot_auto_start is True
    assert config.start_minimized is True


def test_dotenv_loading(tmp_path: Path) -> None:
    """Verify loading configuration from a .env file."""
    env_file = tmp_path / ".env.test"
    env_file.write_text(
        "OCTA_LOG_LEVEL=WARNING\nOCTA_GODOT_AUTO_START=true\nOCTA_LOG_MAX_BYTES=2097152\n",
        encoding="utf-8",
    )

    config = AppConfig.load(env_file=env_file, base_dir=tmp_path)
    assert config.log_level == "WARNING"
    assert config.godot_auto_start is True
    assert config.log_max_bytes == 2097152


def test_precedence_hierarchy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verify precedence: Defaults < .env file < Environment Variables < Keyword Overrides."""
    env_file = tmp_path / ".env.precedence"
    env_file.write_text("OCTA_LOG_LEVEL=WARNING\nOCTA_ENV=staging\n", encoding="utf-8")

    # Set OS environment variable (should override .env for OCTA_LOG_LEVEL)
    monkeypatch.setenv("OCTA_LOG_LEVEL", "ERROR")

    # Pass explicit override (should override OS env and .env)
    config = AppConfig.load(
        env_file=env_file,
        base_dir=tmp_path,
        log_level="CRITICAL",
    )

    # 1. log_level was overridden explicitly -> CRITICAL
    assert config.log_level == "CRITICAL"
    # 2. env was set in .env and not in os.environ or overrides -> staging
    assert config.env == "staging"


def test_invalid_log_level() -> None:
    """Verify rejection of invalid log levels."""
    with pytest.raises(ConfigError, match="Invalid log_level 'INVALID'"):
        AppConfig(log_level="INVALID")


def test_invalid_boolean_parsing() -> None:
    """Verify rejection of invalid boolean strings in AppConfig.load."""
    with pytest.raises(ConfigError, match="Invalid boolean value 'not_a_bool'"):
        AppConfig.load(log_to_file="not_a_bool")


def test_invalid_integer_constraints() -> None:
    """Verify rejection of invalid integer values."""
    with pytest.raises(ConfigError, match="log_max_bytes"):
        AppConfig(log_max_bytes=0)

    with pytest.raises(ConfigError, match="log_backup_count"):
        AppConfig(log_backup_count=-1)

    with pytest.raises(ConfigError, match="Invalid integer value"):
        AppConfig.load(log_max_bytes="abc")


def test_windows_path_resolution(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verify path resolution with LOCALAPPDATA and fallback."""
    fake_localappdata = tmp_path / "AppData" / "Local"
    monkeypatch.setenv("LOCALAPPDATA", str(fake_localappdata))

    config = AppConfig()
    assert config.app_dir == fake_localappdata / "OCTA"
    assert config.log_dir == fake_localappdata / "OCTA" / "logs"

    # Test fallback when LOCALAPPDATA is absent
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    config_fallback = AppConfig()
    assert "OCTA" in str(config_fallback.app_dir)


def test_phase4_llm_and_capture_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verify LLM endpoint, model, timeout, screenshot saving defaults, and secret masking."""
    import secrets

    runtime_token = secrets.token_urlsafe(16)
    monkeypatch.setenv("OCTA_LLM_ENDPOINT", "https://example.org/v1/chat/completions")
    monkeypatch.setenv("OCTA_LLM_MODEL", "custom-vision-model")
    monkeypatch.setenv("OCTA_LLM_API_KEY", runtime_token)
    monkeypatch.setenv("OCTA_LLM_TIMEOUT", "15.5")
    monkeypatch.setenv("OCTA_CAPTURE_SAVE_SCREENSHOTS", "false")

    config = AppConfig.load(base_dir=tmp_path)
    assert config.llm_endpoint == "https://example.org/v1/chat/completions"
    assert config.llm_model == "custom-vision-model"
    assert config.llm_api_key == runtime_token
    assert config.llm_timeout_seconds == 15.5
    assert config.llm_allow_local_http is False
    assert config.capture_save_screenshots is False

    # Ensure API key never appears in repr or str of AppConfig
    assert runtime_token not in repr(config)
    assert runtime_token not in str(config)

    # Invalid endpoint or timeout raises ConfigError
    with pytest.raises(ConfigError, match="Invalid URL"):
        AppConfig(llm_endpoint="ftp://invalid-scheme.local")

    with pytest.raises(ConfigError, match="llm_timeout_seconds"):
        AppConfig(llm_timeout_seconds=0)


def test_phase4_https_and_loopback_http_config_policy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verify remote HTTP is always rejected and loopback HTTP requires llm_allow_local_http."""
    # Remote HTTP is rejected by default AND even when llm_allow_local_http=True
    with pytest.raises(ConfigError, match="Remote LLM endpoints must use HTTPS"):
        AppConfig(llm_endpoint="http://api.example.com/v1/chat/completions")

    with pytest.raises(ConfigError, match="Remote LLM endpoints must use HTTPS"):
        AppConfig(
            llm_endpoint="http://api.example.com/v1/chat/completions",
            llm_allow_local_http=True,
        )

    # Loopback HTTP is rejected by default
    with pytest.raises(ConfigError, match="OCTA_LLM_ALLOW_LOCAL_HTTP"):
        AppConfig(llm_endpoint="http://127.0.0.1:11434/v1/chat/completions")

    with pytest.raises(ConfigError, match="OCTA_LLM_ALLOW_LOCAL_HTTP"):
        AppConfig(llm_endpoint="http://localhost:8000/v1/chat/completions")

    # Loopback HTTP is permitted when explicitly enabled
    local_cfg = AppConfig(
        llm_endpoint="http://127.0.0.1:11434/v1/chat/completions",
        llm_allow_local_http=True,
    )
    assert local_cfg.llm_endpoint == "http://127.0.0.1:11434/v1/chat/completions"
    assert local_cfg.llm_allow_local_http is True

    monkeypatch.setenv("OCTA_LLM_ENDPOINT", "http://[::1]:8080/v1/chat/completions")
    monkeypatch.setenv("OCTA_LLM_ALLOW_LOCAL_HTTP", "true")
    env_cfg = AppConfig.load(base_dir=tmp_path)
    assert env_cfg.llm_endpoint == "http://[::1]:8080/v1/chat/completions"
    assert env_cfg.llm_allow_local_http is True


def test_phase5_voice_config_defaults_and_privacy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verify Phase 5 voice config defaults to disabled and hides credentials in repr/str."""
    import secrets

    default_cfg = AppConfig()
    assert default_cfg.voice_stt_provider == "disabled"
    assert default_cfg.voice_tts_provider == "disabled"
    assert default_cfg.voice_stt_endpoint is None
    assert default_cfg.voice_tts_endpoint is None
    assert default_cfg.voice_save_audio is False
    assert default_cfg.voice_allow_local_http is False
    assert default_cfg.voice_max_recording_seconds == 30.0

    stt_key = secrets.token_urlsafe(16)
    tts_key = secrets.token_urlsafe(16)
    monkeypatch.setenv("OCTA_VOICE_STT_PROVIDER", "remote_http")
    monkeypatch.setenv("OCTA_VOICE_TTS_PROVIDER", "remote_http")
    monkeypatch.setenv(
        "OCTA_VOICE_STT_ENDPOINT", "https://speech.example.com/v1/audio/transcriptions"
    )
    monkeypatch.setenv("OCTA_VOICE_TTS_ENDPOINT", "https://speech.example.com/v1/audio/speech")
    monkeypatch.setenv("OCTA_VOICE_STT_API_KEY", stt_key)
    monkeypatch.setenv("OCTA_VOICE_TTS_API_KEY", tts_key)
    monkeypatch.setenv("OCTA_VOICE_SAVE_AUDIO", "false")

    cfg = AppConfig.load(base_dir=tmp_path)
    assert cfg.voice_stt_provider == "remote_http"
    assert cfg.voice_tts_provider == "remote_http"
    assert cfg.voice_stt_endpoint == "https://speech.example.com/v1/audio/transcriptions"
    assert cfg.voice_tts_endpoint == "https://speech.example.com/v1/audio/speech"
    assert cfg.voice_stt_api_key == stt_key
    assert cfg.voice_tts_api_key == tts_key
    assert cfg.voice_save_audio is False

    assert stt_key not in repr(cfg)
    assert stt_key not in str(cfg)
    assert tts_key not in repr(cfg)
    assert tts_key not in str(cfg)

    with pytest.raises(ConfigError, match="Invalid voice_stt_provider"):
        AppConfig(voice_stt_provider="cloud_unknown")

    # Unsupported 'local' provider option is rejected rather than mapped to a mock
    with pytest.raises(ConfigError, match="Invalid voice_stt_provider"):
        AppConfig(voice_stt_provider="local")
    with pytest.raises(ConfigError, match="Invalid voice_tts_provider"):
        AppConfig(voice_tts_provider="local")

    with pytest.raises(ConfigError, match="Invalid URL"):
        AppConfig(voice_stt_endpoint="ftp://invalid.example.com")

    with pytest.raises(ConfigError, match="Insecure remote HTTP endpoint"):
        AppConfig(voice_tts_endpoint="http://speech.example.com/v1/audio/speech")

    with pytest.raises(ConfigError, match="Loopback HTTP endpoint"):
        AppConfig(voice_stt_endpoint="http://127.0.0.1:9000/v1/audio/transcriptions")

    loopback_voice_cfg = AppConfig(
        voice_stt_endpoint="http://127.0.0.1:9000/v1/audio/transcriptions",
        voice_allow_local_http=True,
    )
    assert loopback_voice_cfg.voice_stt_endpoint == "http://127.0.0.1:9000/v1/audio/transcriptions"
