"""Local-only plugin loader and schema validation for external tutorial packs (Phase 8.1).

Treats all external tutorial plugins as untrusted data:
- Strictly validates plugin manifest fields and schema versions.
- Rejects absolute paths, path traversal ('..'), and symlinks resolving outside the plugin root.
- Never executes plugin code (strictly declarative JSON definitions).
- Keeps all plugin files read-only.
- Prevents tutorial ID collisions and forbids shadowing built-in core tutorials.
- Produces safe, structured diagnostics on invalid plugins without crashing application startup.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from octa.intelligence.exceptions import TaskValidationError
from octa.intelligence.tasks import TaskDefinition

logger = logging.getLogger(__name__)

TUTORIAL_PLUGIN_MANIFEST_FILENAME: str = "plugin.json"
SUPPORTED_PLUGIN_SCHEMA_VERSIONS: frozenset[str] = frozenset({"1.0"})

# Strict identifiers: 1-64 chars, alphanumeric, hyphens, and underscores only
_PLUGIN_ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

# SemVer-compatible version string regex (e.g., 1.0, 1.0.0, 2.1.0-beta.1)
_SEMVER_REGEX = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)(?:\.(0|[1-9]\d*))?(?:-[0-9a-zA-Z.-]+)?(?:\+[0-9a-zA-Z.-]+)?$"
)


class TutorialPluginError(Exception):
    """Base exception for tutorial plugin loading and validation failures."""


class PluginManifestError(TutorialPluginError):
    """Raised when a plugin.json manifest is missing, malformed, or invalid."""


class PluginSecurityError(TutorialPluginError):
    """Raised when a plugin attempts path traversal, symlink escape, or illegal access."""


class PluginCollisionError(TutorialPluginError):
    """Raised when a plugin tutorial ID collides with an existing or built-in tutorial."""


@dataclass(frozen=True)
class TutorialPluginManifest:
    """Validated metadata from a plugin.json manifest."""

    plugin_id: str
    name: str
    version: str
    tutorial_files: tuple[str, ...]
    schema_version: str = "1.0"
    author: str = ""
    description: str = ""
    asset_digests: dict[str, str] = field(default_factory=dict)
    signature: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.schema_version not in SUPPORTED_PLUGIN_SCHEMA_VERSIONS:
            supported = ", ".join(sorted(SUPPORTED_PLUGIN_SCHEMA_VERSIONS))
            raise PluginManifestError(
                f"Unsupported manifest schema_version '{self.schema_version}'. "
                f"Supported versions: {supported}."
            )

        if not isinstance(self.plugin_id, str) or not _PLUGIN_ID_REGEX.match(self.plugin_id):
            raise PluginManifestError(
                f"Plugin 'id' must match {_PLUGIN_ID_REGEX.pattern!r}, got {self.plugin_id!r}."
            )

        if not isinstance(self.name, str) or not self.name.strip():
            raise PluginManifestError("Plugin 'name' must be a non-empty string.")

        if not isinstance(self.version, str) or not _SEMVER_REGEX.match(self.version.strip()):
            raise PluginManifestError(
                f"Plugin 'version' must be a SemVer-compatible string, got {self.version!r}."
            )

        if not isinstance(self.tutorial_files, (list, tuple)) or len(self.tutorial_files) == 0:
            raise PluginManifestError("Plugin manifest must specify at least one tutorial file.")

        clean_files: list[str] = []
        for idx, file_str in enumerate(self.tutorial_files):
            if not isinstance(file_str, str) or not file_str.strip():
                raise PluginManifestError(
                    f"tutorial_files[{idx}] must be a non-empty relative file path."
                )
            cleaned = file_str.strip().replace("\\", "/")
            path_obj = Path(cleaned)
            if path_obj.is_absolute():
                raise PluginSecurityError(
                    f"Absolute paths are forbidden in tutorial_files: {file_str!r}."
                )
            if ".." in path_obj.parts:
                raise PluginSecurityError(
                    f"Path traversal ('..') is forbidden in tutorial_files: {file_str!r}."
                )
            clean_files.append(cleaned)

        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(self, "version", self.version.strip())
        author_val = self.author.strip() if isinstance(self.author, str) else ""
        object.__setattr__(self, "author", author_val)
        object.__setattr__(
            self,
            "description",
            self.description.strip() if isinstance(self.description, str) else "",
        )
        object.__setattr__(self, "tutorial_files", tuple(clean_files))

        if not isinstance(self.asset_digests, dict):
            raise PluginManifestError("Field 'asset_digests' must be a dictionary.")
        clean_digests: dict[str, str] = {}
        for path_key, digest_val in self.asset_digests.items():
            if not isinstance(path_key, str) or not isinstance(digest_val, str):
                raise PluginManifestError("Entries in 'asset_digests' must be strings.")
            cleaned_p = path_key.strip().replace("\\", "/")
            path_obj = Path(cleaned_p)
            if path_obj.is_absolute():
                raise PluginSecurityError(
                    f"Absolute paths are forbidden in asset_digests: {path_key!r}."
                )
            if ".." in path_obj.parts:
                raise PluginSecurityError(
                    f"Path traversal ('..') is forbidden in asset_digests: {path_key!r}."
                )
            clean_digests[cleaned_p] = digest_val.strip().lower()
        object.__setattr__(self, "asset_digests", clean_digests)

        if self.signature is not None and not isinstance(self.signature, dict):
            raise PluginManifestError("Field 'signature' must be a dictionary.")

    def to_dict(self, *, include_signature: bool = True) -> dict[str, Any]:
        """Convert manifest to dictionary representation."""
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "id": self.plugin_id,
            "name": self.name,
            "version": self.version,
            "tutorial_files": list(self.tutorial_files),
        }
        if self.author:
            result["author"] = self.author
        if self.description:
            result["description"] = self.description
        if self.asset_digests:
            result["asset_digests"] = dict(self.asset_digests)
        if include_signature and self.signature is not None:
            result["signature"] = dict(self.signature)
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TutorialPluginManifest:
        """Parse and validate a dictionary loaded from plugin.json."""
        if not isinstance(data, dict):
            raise PluginManifestError("Plugin manifest root must be a JSON object.")

        allowed_keys = {
            "schema_version",
            "id",
            "name",
            "version",
            "author",
            "description",
            "tutorial_file",
            "tutorial_files",
            "asset_digests",
            "signature",
        }
        extra_keys = set(data.keys()) - allowed_keys
        if extra_keys:
            raise PluginManifestError(
                f"Unexpected fields in plugin manifest: {sorted(extra_keys)}."
            )

        required_keys = {"schema_version", "id", "name", "version"}
        missing_keys = required_keys - set(data.keys())
        if missing_keys:
            raise PluginManifestError(
                f"Missing required fields in plugin manifest: {sorted(missing_keys)}."
            )

        raw_files: list[str] = []
        if "tutorial_file" in data and data["tutorial_file"]:
            if not isinstance(data["tutorial_file"], str):
                raise PluginManifestError("Field 'tutorial_file' must be a string.")
            raw_files.append(data["tutorial_file"])
        if "tutorial_files" in data and data["tutorial_files"]:
            if not isinstance(data["tutorial_files"], (list, tuple)):
                raise PluginManifestError("Field 'tutorial_files' must be a list of strings.")
            for item in data["tutorial_files"]:
                if not isinstance(item, str):
                    raise PluginManifestError("Field 'tutorial_files' entries must be strings.")
                raw_files.append(item)

        if not raw_files:
            raise PluginManifestError(
                "Plugin manifest must specify 'tutorial_file' or 'tutorial_files'."
            )

        return cls(
            schema_version=str(data["schema_version"]).strip(),
            plugin_id=str(data["id"]).strip(),
            name=str(data["name"]),
            version=str(data["version"]),
            author=str(data.get("author", "")),
            description=str(data.get("description", "")),
            tutorial_files=tuple(raw_files),
            asset_digests=dict(data.get("asset_digests", {})),
            signature=data.get("signature"),
        )


@dataclass(frozen=True)
class LoadedTutorialPlugin:
    """A successfully loaded tutorial plugin and its validated tutorials."""

    manifest: TutorialPluginManifest
    plugin_dir: Path
    tutorials: tuple[TaskDefinition, ...]


@dataclass(frozen=True)
class PluginLoadDiagnostic:
    """Structured diagnostic entry for a failed or partially loaded plugin."""

    plugin_dir: Path | None
    error_code: str
    message: str


@dataclass(frozen=True)
class PluginDiscoveryResult:
    """Aggregate result from discovering and loading tutorial plugins."""

    loaded_plugins: tuple[LoadedTutorialPlugin, ...]
    tutorials: tuple[TaskDefinition, ...]
    diagnostics: tuple[PluginLoadDiagnostic, ...]


def _verify_safe_relative_file(plugin_dir: Path, rel_path: str) -> Path:
    """Resolve a relative path safely within plugin_dir, rejecting escapes and symlinks."""
    cleaned = rel_path.strip().replace("\\", "/")
    path_obj = Path(cleaned)
    if path_obj.is_absolute():
        raise PluginSecurityError(f"Absolute file path forbidden: {rel_path!r}")
    if ".." in path_obj.parts:
        raise PluginSecurityError(f"Path traversal ('..') forbidden: {rel_path!r}")

    canonical_plugin_dir = plugin_dir.resolve(strict=True)
    target = (plugin_dir / path_obj).resolve(strict=False)

    try:
        is_rel = target.is_relative_to(canonical_plugin_dir)
    except (ValueError, AttributeError):
        is_rel = False

    if not is_rel:
        raise PluginSecurityError(f"Plugin file path escapes plugin root directory: {rel_path!r}")

    if not target.exists():
        raise PluginManifestError(f"Plugin tutorial file does not exist: {rel_path!r}")

    if not target.is_file():
        raise PluginManifestError(f"Plugin tutorial target is not a regular file: {rel_path!r}")

    return target


def load_tutorial_plugin(
    plugin_dir: Path | str,
    *,
    reserved_task_ids: frozenset[str] | set[str] | None = None,
) -> tuple[LoadedTutorialPlugin | None, tuple[PluginLoadDiagnostic, ...]]:
    """Load and validate a single tutorial plugin from its folder."""
    p_dir = Path(plugin_dir)
    diagnostics: list[PluginLoadDiagnostic] = []

    if not p_dir.is_dir():
        diag = PluginLoadDiagnostic(
            plugin_dir=p_dir,
            error_code="NOT_A_DIRECTORY",
            message=f"Plugin path is not a directory: {p_dir}",
        )
        return None, (diag,)

    manifest_path = p_dir / TUTORIAL_PLUGIN_MANIFEST_FILENAME
    if not manifest_path.is_file():
        diag = PluginLoadDiagnostic(
            plugin_dir=p_dir,
            error_code="MANIFEST_NOT_FOUND",
            message=f"Manifest '{TUTORIAL_PLUGIN_MANIFEST_FILENAME}' not found in: {p_dir}",
        )
        return None, (diag,)

    # Read manifest file in read-only mode
    try:
        manifest_bytes = manifest_path.read_bytes()
        manifest_data = json.loads(manifest_bytes.decode("utf-8"))
    except UnicodeDecodeError as exc:
        diag = PluginLoadDiagnostic(
            plugin_dir=p_dir,
            error_code="INVALID_MANIFEST_ENCODING",
            message=f"Manifest is not valid UTF-8: {exc}",
        )
        return None, (diag,)
    except json.JSONDecodeError as exc:
        diag = PluginLoadDiagnostic(
            plugin_dir=p_dir,
            error_code="INVALID_MANIFEST_JSON",
            message=f"Manifest JSON parsing failed: {exc}",
        )
        return None, (diag,)

    # Validate manifest schema
    try:
        manifest = TutorialPluginManifest.from_dict(manifest_data)
    except (PluginManifestError, PluginSecurityError) as exc:
        is_sec = isinstance(exc, PluginSecurityError)
        code = "PATH_TRAVERSAL_ATTEMPT" if is_sec else "SCHEMA_VALIDATION_ERROR"
        diag = PluginLoadDiagnostic(
            plugin_dir=p_dir,
            error_code=code,
            message=str(exc),
        )
        return None, (diag,)

    # Track seen task IDs for collision checks
    reserved = set(reserved_task_ids) if reserved_task_ids is not None else set()
    loaded_tutorials: list[TaskDefinition] = []

    for rel_file in manifest.tutorial_files:
        try:
            safe_target = _verify_safe_relative_file(p_dir, rel_file)
            raw_bytes = safe_target.read_bytes()
            tutorial_def = TaskDefinition.from_json(raw_bytes)
        except PluginSecurityError as exc:
            diagnostics.append(
                PluginLoadDiagnostic(
                    plugin_dir=p_dir,
                    error_code="PATH_TRAVERSAL_ATTEMPT",
                    message=f"Security violation in '{rel_file}': {exc}",
                )
            )
            continue
        except (PluginManifestError, FileNotFoundError) as exc:
            diagnostics.append(
                PluginLoadDiagnostic(
                    plugin_dir=p_dir,
                    error_code="FILE_NOT_FOUND",
                    message=f"Missing tutorial file '{rel_file}': {exc}",
                )
            )
            continue
        except (TaskValidationError, json.JSONDecodeError) as exc:
            diagnostics.append(
                PluginLoadDiagnostic(
                    plugin_dir=p_dir,
                    error_code="TUTORIAL_VALIDATION_ERROR",
                    message=f"Invalid tutorial schema in '{rel_file}': {exc}",
                )
            )
            continue

        # Enforce ID collision behavior: never shadow built-ins or duplicate registered tutorials
        if tutorial_def.task_id in reserved:
            diagnostics.append(
                PluginLoadDiagnostic(
                    plugin_dir=p_dir,
                    error_code="ID_COLLISION",
                    message=(
                        f"Plugin tutorial ID '{tutorial_def.task_id}' in '{manifest.plugin_id}' "
                        "collides with an existing or built-in tutorial and was rejected "
                        "to prevent shadowing."
                    ),
                )
            )
            continue

        reserved.add(tutorial_def.task_id)
        loaded_tutorials.append(tutorial_def)

    if not loaded_tutorials:
        diag = PluginLoadDiagnostic(
            plugin_dir=p_dir,
            error_code="NO_VALID_TUTORIALS",
            message=f"Plugin '{manifest.plugin_id}' contains no valid, non-colliding tutorials.",
        )
        diagnostics.append(diag)
        return None, tuple(diagnostics)

    plugin_obj = LoadedTutorialPlugin(
        manifest=manifest,
        plugin_dir=p_dir,
        tutorials=tuple(loaded_tutorials),
    )
    return plugin_obj, tuple(diagnostics)


def discover_tutorial_plugins(
    plugins_root: Path | str | None,
    *,
    reserved_task_ids: frozenset[str] | set[str] | None = None,
) -> PluginDiscoveryResult:
    """Scan directory for valid tutorial plugins, returning loaded plugins and diagnostics."""
    if plugins_root is None:
        return PluginDiscoveryResult(loaded_plugins=(), tutorials=(), diagnostics=())

    root_path = Path(plugins_root)
    if not root_path.exists() or not root_path.is_dir():
        return PluginDiscoveryResult(loaded_plugins=(), tutorials=(), diagnostics=())

    reserved = set(reserved_task_ids) if reserved_task_ids is not None else set()
    loaded_plugins: list[LoadedTutorialPlugin] = []
    all_tutorials: list[TaskDefinition] = []
    all_diagnostics: list[PluginLoadDiagnostic] = []

    # Check if plugins_root itself is a single plugin directory containing plugin.json
    single_manifest = root_path / TUTORIAL_PLUGIN_MANIFEST_FILENAME
    if single_manifest.is_file():
        plugin_obj, diags = load_tutorial_plugin(root_path, reserved_task_ids=reserved)
        all_diagnostics.extend(diags)
        if plugin_obj is not None:
            loaded_plugins.append(plugin_obj)
            all_tutorials.extend(plugin_obj.tutorials)
        return PluginDiscoveryResult(
            loaded_plugins=tuple(loaded_plugins),
            tutorials=tuple(all_tutorials),
            diagnostics=tuple(all_diagnostics),
        )

    # Otherwise scan child directories deterministically
    try:
        entries = sorted(root_path.iterdir(), key=lambda p: p.name.lower())
    except OSError as exc:
        diag = PluginLoadDiagnostic(
            plugin_dir=root_path,
            error_code="SCAN_ERROR",
            message=f"Failed to scan plugins root directory: {exc}",
        )
        return PluginDiscoveryResult(
            loaded_plugins=(),
            tutorials=(),
            diagnostics=(diag,),
        )

    for entry in entries:
        if entry.is_dir():
            plugin_obj, diags = load_tutorial_plugin(entry, reserved_task_ids=reserved)
            all_diagnostics.extend(diags)
            if plugin_obj is not None:
                loaded_plugins.append(plugin_obj)
                for tut in plugin_obj.tutorials:
                    reserved.add(tut.task_id)
                    all_tutorials.append(tut)

    return PluginDiscoveryResult(
        loaded_plugins=tuple(loaded_plugins),
        tutorials=tuple(all_tutorials),
        diagnostics=tuple(all_diagnostics),
    )
