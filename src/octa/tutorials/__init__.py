"""Tutorial Engine, sample catalog, and external plugin system for OCTA (Phase 6.0 & 8.1)."""

from __future__ import annotations

from octa.tutorials.catalog import (
    BUILTIN_TASK_IDS,
    BUILTIN_TUTORIALS,
    TutorialCatalog,
    build_explorer_new_folder_tutorial,
    build_notepad_save_tutorial,
    build_octa_welcome_tutorial,
    get_builtin_tutorials,
    get_tutorial_by_id,
    load_tutorial_from_json_file,
)
from octa.tutorials.engine import (
    StepProgressRecord,
    TutorialEngine,
    TutorialSessionState,
)
from octa.tutorials.feedback import (
    FeedbackCollector,
    TutorialFeedback,
    TutorialSessionMetrics,
)
from octa.tutorials.plugins import (
    TUTORIAL_PLUGIN_MANIFEST_FILENAME,
    LoadedTutorialPlugin,
    PluginCollisionError,
    PluginDiscoveryResult,
    PluginLoadDiagnostic,
    PluginManifestError,
    PluginSecurityError,
    TutorialPluginError,
    TutorialPluginManifest,
    discover_tutorial_plugins,
    load_tutorial_plugin,
)
from octa.tutorials.sound import (
    TutorialSoundPlayer,
    create_completion_speech_cue,
    synthesize_completion_chime_wav,
)

__all__ = [
    "BUILTIN_TASK_IDS",
    "BUILTIN_TUTORIALS",
    "FeedbackCollector",
    "LoadedTutorialPlugin",
    "PluginCollisionError",
    "PluginDiscoveryResult",
    "PluginLoadDiagnostic",
    "PluginManifestError",
    "PluginSecurityError",
    "StepProgressRecord",
    "TUTORIAL_PLUGIN_MANIFEST_FILENAME",
    "TutorialCatalog",
    "TutorialEngine",
    "TutorialFeedback",
    "TutorialPluginError",
    "TutorialPluginManifest",
    "TutorialSessionMetrics",
    "TutorialSessionState",
    "TutorialSoundPlayer",
    "build_explorer_new_folder_tutorial",
    "build_notepad_save_tutorial",
    "build_octa_welcome_tutorial",
    "create_completion_speech_cue",
    "discover_tutorial_plugins",
    "get_builtin_tutorials",
    "get_tutorial_by_id",
    "load_tutorial_from_json_file",
    "load_tutorial_plugin",
    "synthesize_completion_chime_wav",
]
