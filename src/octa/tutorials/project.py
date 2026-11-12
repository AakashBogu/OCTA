"""Tutorial plugin project workspace and packaging manager for OCTA (Phase 10.3).

Provides a local project workspace for authoring and bundling tutorial plugins:
- Manages plugin.json manifests, tutorial JSON files, and documentation assets.
- Enforces strict pre-flight workspace validation (schema, symlinks, forbidden
  extensions, quotas, and task ID collision prevention).
- Integrates with Phase 9.1 and Phase 9.2 packaging and Ed25519 digital signing.
- Supports one-click 'Test in OCTA' installation into the managed directory with
  dynamic catalog refresh.
- Ensures private signing keys are loaded only when explicitly requested and
  never persisted or leaked.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Self

from cryptography.hazmat.primitives.asymmetric import ed25519

if TYPE_CHECKING:
    from octa.desktop.controller import DesktopController
from octa.intelligence.tasks import (
    ExpectedOutcome,
    TaskDefinition,
    TaskStep,
    load_task_definition_from_file,
    save_task_definition_to_file,
)
from octa.tutorials.catalog import BUILTIN_TASK_IDS
from octa.tutorials.packaging import (
    FORBIDDEN_EXTENSIONS,
    MAX_PACKAGE_EXPANDED_BYTES,
    MAX_PACKAGE_FILE_BYTES,
    MAX_PACKAGE_FILE_COUNT,
    InstalledPluginInfo,
    PackageIntegrityError,
    PackageVerificationResult,
    TrustStore,
    pack_tutorial_package,
    verify_tutorial_package,
)
from octa.tutorials.plugins import (
    TUTORIAL_PLUGIN_MANIFEST_FILENAME,
    PluginCollisionError,
    PluginManifestError,
    TutorialPluginError,
    TutorialPluginManifest,
)

logger = logging.getLogger("octa.tutorials.project")

_PLUGIN_ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_SEMVER_REGEX = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)(?:\.(0|[1-9]\d*))?(?:-[0-9a-zA-Z.-]+)?(?:\+[0-9a-zA-Z.-]+)?$"
)


class ProjectValidationError(TutorialPluginError):
    """Raised when a plugin project workspace fails pre-flight validation."""


@dataclass(frozen=True)
class ProjectValidationReport:
    """Detailed diagnostic report from validating a plugin project workspace."""

    is_valid: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    file_count: int = 0
    total_bytes: int = 0
    tutorial_count: int = 0


class TutorialPluginProject:
    """Manages an authoring workspace for an external tutorial plugin."""

    def __init__(self, project_dir: Path | str, manifest: TutorialPluginManifest) -> None:
        self._project_dir = Path(project_dir).resolve()
        self._manifest = manifest

    @property
    def project_dir(self) -> Path:
        """Absolute root directory of the plugin project."""
        return self._project_dir

    @property
    def manifest(self) -> TutorialPluginManifest:
        """Current plugin manifest definition."""
        return self._manifest

    @classmethod
    def create(
        cls,
        project_dir: Path | str,
        *,
        plugin_id: str,
        name: str,
        version: str = "1.0.0",
        author: str = "",
        description: str = "",
        initial_task: TaskDefinition | None = None,
    ) -> Self:
        """Initialize a new plugin project workspace in the given directory.

        Creates directory structure, plugin.json, an initial tutorial definition,
        and a starter README.md.
        """
        dest_dir = Path(project_dir).resolve()
        clean_id = str(plugin_id).strip()
        if not _PLUGIN_ID_REGEX.match(clean_id):
            raise ProjectValidationError(
                f"Plugin ID must match {_PLUGIN_ID_REGEX.pattern!r}, got {clean_id!r}."
            )

        clean_name = str(name).strip()
        if not clean_name:
            raise ProjectValidationError("Plugin name must be a non-empty string.")

        clean_version = str(version).strip()
        if not _SEMVER_REGEX.match(clean_version):
            raise ProjectValidationError(
                f"Plugin version must be a SemVer-compatible string, got {clean_version!r}."
            )

        manifest_file = dest_dir / TUTORIAL_PLUGIN_MANIFEST_FILENAME
        if manifest_file.exists():
            raise FileExistsError(
                f"A plugin manifest already exists in project directory: {manifest_file}"
            )

        dest_dir.mkdir(parents=True, exist_ok=True)
        tutorials_dir = dest_dir / "tutorials"
        tutorials_dir.mkdir(parents=True, exist_ok=True)

        if initial_task is not None:
            if initial_task.task_id in BUILTIN_TASK_IDS:
                raise PluginCollisionError(
                    f"Initial task ID '{initial_task.task_id}' collides with "
                    "reserved built-in tutorial."
                )
            tut_file = tutorials_dir / f"{initial_task.task_id}.json"
            save_task_definition_to_file(initial_task, tut_file)
            rel_tut = f"tutorials/{tut_file.name}"
        else:
            starter_task_id = f"{clean_id}-tutorial"
            if starter_task_id in BUILTIN_TASK_IDS:
                starter_task_id = f"{clean_id}-starter-task"
            starter_task = TaskDefinition(
                task_id=starter_task_id,
                version=clean_version,
                title=f"{clean_name} Tutorial",
                application=clean_name,
                goal=f"Learn essential steps for {clean_name}",
                steps=(
                    TaskStep(
                        step_id="step-1",
                        order=1,
                        title="Getting Started",
                        instruction=f"Open {clean_name} and prepare for the guided tutorial.",
                        hints=("Check that the target application window is visible on screen.",),
                        expected_outcome=ExpectedOutcome(
                            description=f"{clean_name} is active and ready",
                            required_elements=(clean_name,),
                        ),
                    ),
                ),
            )
            tut_file = tutorials_dir / f"{starter_task_id}.json"
            save_task_definition_to_file(starter_task, tut_file)
            rel_tut = f"tutorials/{tut_file.name}"

        readme_file = dest_dir / "README.md"
        if not readme_file.exists():
            clean_desc = (
                description.strip()
                if description
                else f"Interactive tutorial plugin for {clean_name}."
            )
            readme_file.write_text(
                f"# {clean_name}\n\n{clean_desc}\n\nAuthored with OCTA Tutorial Studio.\n",
                encoding="utf-8",
            )

        manifest = TutorialPluginManifest(
            schema_version="1.0",
            plugin_id=clean_id,
            name=clean_name,
            version=clean_version,
            author=str(author).strip(),
            description=str(description).strip(),
            tutorial_files=(rel_tut,),
        )

        project = cls(dest_dir, manifest)
        project.save_manifest()
        return project

    @classmethod
    def open(cls, project_dir: Path | str) -> Self:
        """Open an existing tutorial plugin project workspace."""
        src_dir = Path(project_dir).resolve()
        if not src_dir.is_dir():
            raise FileNotFoundError(f"Project directory not found: {src_dir}")

        manifest_file = src_dir / TUTORIAL_PLUGIN_MANIFEST_FILENAME
        if not manifest_file.is_file():
            raise FileNotFoundError(f"Missing '{TUTORIAL_PLUGIN_MANIFEST_FILENAME}' in {src_dir}")

        try:
            content = manifest_file.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise PluginManifestError(
                f"Manifest '{manifest_file.name}' is not valid UTF-8: {exc}"
            ) from exc

        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise PluginManifestError(f"Malformed JSON in plugin manifest: {exc}") from exc

        manifest = TutorialPluginManifest.from_dict(data)
        return cls(src_dir, manifest)

    def save_manifest(self) -> Path:
        """Atomically serialize the current manifest to plugin.json."""
        manifest_file = self._project_dir / TUTORIAL_PLUGIN_MANIFEST_FILENAME
        data = self._manifest.to_dict(include_signature=True)
        # Ensure schema validity by roundtripping through from_dict
        TutorialPluginManifest.from_dict(data)

        json_bytes = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")

        # Atomic replacement via temporary file
        tmp_path = self._project_dir / f".tmp_{manifest_file.stem}_{os.urandom(4).hex()}.json"
        try:
            tmp_path.write_bytes(json_bytes)
            os.replace(str(tmp_path), str(manifest_file))
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

        return manifest_file

    def update_manifest(
        self,
        *,
        name: str | None = None,
        version: str | None = None,
        author: str | None = None,
        description: str | None = None,
        tutorial_files: tuple[str, ...] | None = None,
    ) -> None:
        """Update manifest metadata fields and persist atomically."""
        new_name = name if name is not None else self._manifest.name
        new_version = version if version is not None else self._manifest.version
        new_author = author if author is not None else self._manifest.author
        new_desc = description if description is not None else self._manifest.description
        new_tuts = tutorial_files if tutorial_files is not None else self._manifest.tutorial_files

        updated_manifest = TutorialPluginManifest(
            schema_version=self._manifest.schema_version,
            plugin_id=self._manifest.plugin_id,
            name=new_name,
            version=new_version,
            author=new_author,
            description=new_desc,
            tutorial_files=new_tuts,
            asset_digests=dict(self._manifest.asset_digests),
            signature=self._manifest.signature,
        )
        self._manifest = updated_manifest
        self.save_manifest()

    def add_tutorial_file(
        self,
        source_file: Path | str,
        *,
        copy_into_project: bool = True,
    ) -> str:
        """Add a tutorial JSON file to the project workspace and manifest.

        Validates that the file conforms to TaskDefinition and does not collide
        with built-in or existing tutorial IDs.
        """
        src = Path(source_file).resolve()
        if not src.is_file():
            raise FileNotFoundError(f"Tutorial file not found: {src}")

        # Validate tutorial file schema
        task = load_task_definition_from_file(src)

        # Check collision with reserved built-in tutorial IDs
        if task.task_id in BUILTIN_TASK_IDS:
            reserved_str = ", ".join(sorted(BUILTIN_TASK_IDS))
            raise PluginCollisionError(
                f"Tutorial task ID '{task.task_id}' collides with a reserved built-in tutorial "
                f"({reserved_str})."
            )

        # Check collision with existing tutorials in workspace
        existing_tasks = self.get_tutorial_tasks()
        for existing_rel, existing_def in existing_tasks.items():
            if existing_def.task_id == task.task_id:
                raise PluginCollisionError(
                    f"Tutorial task ID '{task.task_id}' is already present in this project "
                    f"('{existing_rel}')."
                )

        if copy_into_project:
            tutorials_dir = self._project_dir / "tutorials"
            tutorials_dir.mkdir(parents=True, exist_ok=True)
            dest_file = tutorials_dir / src.name
            if dest_file != src:
                shutil.copy2(src, dest_file)
            rel_path = f"tutorials/{dest_file.name}"
        else:
            try:
                rel_path = src.relative_to(self._project_dir).as_posix()
            except ValueError as exc:
                raise ProjectValidationError(
                    "Tutorial file must be inside project directory when "
                    f"copy_into_project=False: {src}"
                ) from exc

        rel_path = rel_path.replace("\\", "/")
        if rel_path not in self._manifest.tutorial_files:
            new_files = self._manifest.tutorial_files + (rel_path,)
            self.update_manifest(tutorial_files=new_files)

        return rel_path

    def remove_tutorial_reference(
        self,
        relative_path: str,
        *,
        delete_from_disk: bool = False,
    ) -> bool:
        """Remove a tutorial file reference from the manifest, optionally deleting the file."""
        clean_rel = relative_path.strip().replace("\\", "/")
        if clean_rel not in self._manifest.tutorial_files:
            return False

        if len(self._manifest.tutorial_files) <= 1:
            raise ProjectValidationError(
                "Cannot remove tutorial: a plugin project must retain at least one tutorial file."
            )

        new_files = tuple(f for f in self._manifest.tutorial_files if f != clean_rel)
        self.update_manifest(tutorial_files=new_files)

        if delete_from_disk:
            target_path = (self._project_dir / clean_rel).resolve()
            if target_path.is_file() and not target_path.is_symlink():
                try:
                    # Guard that target is strictly inside project_dir
                    target_path.relative_to(self._project_dir.resolve())
                    target_path.unlink()
                except (ValueError, OSError) as exc:
                    logger.warning(f"Failed to delete tutorial file '{target_path}': {exc}")

        return True

    def get_tutorial_tasks(self) -> dict[str, TaskDefinition]:
        """Load and parse all referenced tutorials currently existing on disk."""
        tasks: dict[str, TaskDefinition] = {}
        for rel_path in self._manifest.tutorial_files:
            target = (self._project_dir / rel_path).resolve()
            if target.is_file():
                try:
                    tasks[rel_path] = load_task_definition_from_file(target)
                except Exception as exc:
                    logger.warning(f"Failed to load tutorial '{rel_path}': {exc}")
        return tasks

    def validate_workspace(self) -> ProjectValidationReport:
        """Perform comprehensive pre-flight validation on the project workspace.

        Checks:
        - Manifest schema, plugin ID format, and SemVer version.
        - Non-empty tutorial listings and valid relative paths.
        - Strict symlink rejection across the entire workspace directory.
        - Strict rejection of executable and script extensions.
        - Quota compliance: total file count, total expanded size, max file size.
        - Integrity and schema validation of all referenced tutorial files.
        - Detection of task ID collisions with built-ins or duplicate task IDs.
        - Detection of unreferenced .json files or missing documentation.
        """
        errors: list[str] = []
        warnings: list[str] = []

        if not self._project_dir.is_dir():
            return ProjectValidationReport(
                is_valid=False,
                errors=(f"Project directory does not exist: {self._project_dir}",),
            )

        # 1. Manifest structure
        manifest_file = self._project_dir / TUTORIAL_PLUGIN_MANIFEST_FILENAME
        if not manifest_file.is_file():
            errors.append(f"Missing mandatory '{TUTORIAL_PLUGIN_MANIFEST_FILENAME}'.")
        else:
            try:
                raw_data = json.loads(manifest_file.read_text(encoding="utf-8"))
                TutorialPluginManifest.from_dict(raw_data)
            except Exception as exc:
                errors.append(f"Invalid plugin manifest: {exc}")

        if not self._manifest.tutorial_files:
            errors.append("Manifest specifies no tutorial files.")

        # 2. Scan entire workspace folder for symlinks, forbidden extensions, and quota measurements
        total_files = 0
        total_bytes = 0

        for item in self._project_dir.rglob("*"):
            if item.is_symlink():
                rel_path = item.relative_to(self._project_dir)
                errors.append(f"Symlinks are forbidden in package workspace: {rel_path}")
                continue

            if item.is_file():
                # Ignore internal temporary files
                if item.name.startswith(".tmp_"):
                    continue

                total_files += 1
                try:
                    file_size = item.stat().st_size
                except OSError:
                    file_size = 0
                total_bytes += file_size

                ext = item.suffix.lower()
                if ext in FORBIDDEN_EXTENSIONS:
                    rel_path = item.relative_to(self._project_dir)
                    errors.append(
                        f"Forbidden file extension '{ext}' in workspace: {rel_path}. "
                        "Executables and scripts are forbidden."
                    )

                if file_size > MAX_PACKAGE_FILE_BYTES:
                    errors.append(
                        f"File '{item.name}' exceeds maximum permitted file size "
                        f"({file_size} > {MAX_PACKAGE_FILE_BYTES} bytes)."
                    )

        if total_files > MAX_PACKAGE_FILE_COUNT:
            errors.append(
                f"Workspace contains {total_files} files, exceeding maximum quota of "
                f"{MAX_PACKAGE_FILE_COUNT} files."
            )
        if total_bytes > MAX_PACKAGE_EXPANDED_BYTES:
            errors.append(
                f"Workspace expanded size ({total_bytes} bytes) exceeds maximum quota of "
                f"{MAX_PACKAGE_EXPANDED_BYTES} bytes."
            )

        # 3. Validate referenced tutorial files
        seen_task_ids: dict[str, str] = {}
        for rel_file in self._manifest.tutorial_files:
            clean_rel = rel_file.strip().replace("\\", "/")
            path_obj = Path(clean_rel)
            if path_obj.is_absolute() or ".." in path_obj.parts:
                errors.append(f"Invalid path traversal in tutorial reference: {rel_file!r}")
                continue

            target_file = (self._project_dir / clean_rel).resolve()
            if not target_file.is_file():
                errors.append(f"Referenced tutorial file does not exist on disk: '{clean_rel}'")
                continue

            try:
                task = load_task_definition_from_file(target_file)
                if task.task_id in BUILTIN_TASK_IDS:
                    errors.append(
                        f"Tutorial task ID '{task.task_id}' in '{clean_rel}' "
                        "collides with reserved built-in tutorial."
                    )
                if task.task_id in seen_task_ids:
                    errors.append(
                        f"Duplicate task ID '{task.task_id}' in '{clean_rel}' "
                        f"(already in '{seen_task_ids[task.task_id]}')."
                    )
                else:
                    seen_task_ids[task.task_id] = clean_rel
            except Exception as exc:
                errors.append(f"Tutorial '{clean_rel}' failed schema validation: {exc}")

        # 4. Warnings: unreferenced tutorials or missing documentation
        tutorials_dir = self._project_dir / "tutorials"
        if tutorials_dir.is_dir():
            for json_file in tutorials_dir.glob("*.json"):
                rel_json = f"tutorials/{json_file.name}"
                is_unreferenced = (
                    rel_json not in self._manifest.tutorial_files
                    and not json_file.name.startswith(".tmp_")
                )
                if is_unreferenced:
                    warnings.append(
                        f"Tutorial file on disk is not registered in manifest: '{rel_json}'"
                    )

        readme_file = self._project_dir / "README.md"
        if not readme_file.is_file():
            warnings.append("No 'README.md' documentation found in project workspace.")

        if not self._manifest.description:
            warnings.append("Manifest description is empty.")
        if not self._manifest.author:
            warnings.append("Manifest author is empty.")

        return ProjectValidationReport(
            is_valid=len(errors) == 0,
            errors=tuple(errors),
            warnings=tuple(warnings),
            file_count=total_files,
            total_bytes=total_bytes,
            tutorial_count=len(self._manifest.tutorial_files),
        )

    def pack_package(
        self,
        output_path: Path | str | None = None,
        *,
        overwrite: bool = False,
        signing_key: ed25519.Ed25519PrivateKey | None = None,
        key_id: str = "community",
    ) -> Path:
        """Validate workspace and build an atomic .octapack archive.

        Args:
            output_path: Target path for .octapack archive. Defaults to parent/<plugin_id>.octapack.
            overwrite: Whether to overwrite existing package file.
            signing_key: Optional Ed25519 private key for signing.
            key_id: Key identifier associated with the signing key.

        Returns:
            Path to the verified .octapack archive.
        """
        report = self.validate_workspace()
        if not report.is_valid:
            error_list = "\n- ".join(report.errors)
            raise ProjectValidationError(f"Cannot pack invalid project:\n- {error_list}")

        return pack_tutorial_package(
            source_dir=self._project_dir,
            output_path=output_path,
            overwrite=overwrite,
            signing_key=signing_key,
            key_id=key_id,
        )

    def test_install(
        self,
        controller: DesktopController,
        *,
        overwrite: bool = False,
        trust_store: TrustStore | None = None,
        signing_key: ed25519.Ed25519PrivateKey | None = None,
        key_id: str = "community",
    ) -> tuple[InstalledPluginInfo, PackageVerificationResult]:
        """Build package to a secure temporary archive, verify, and install in OCTA.

        Triggers dynamic catalog reload in controller so the newly packaged tutorial
        is immediately available for live testing.

        Args:
            controller: DesktopController with active tutorial engine and managed plugin dir.
            overwrite: Whether to overwrite already-installed plugin of same ID.
            trust_store: Optional TrustStore for evaluating signature trust.
            signing_key: Optional private key to sign the test package.
            key_id: Key identifier for the test package signature.

        Returns:
            Tuple of (InstalledPluginInfo, PackageVerificationResult).
        """
        temp_dir = Path(tempfile.mkdtemp(prefix="octa_test_pack_"))
        temp_pkg = temp_dir / f"{self._manifest.plugin_id}.octapack"

        try:
            self.pack_package(
                output_path=temp_pkg,
                overwrite=True,
                signing_key=signing_key,
                key_id=key_id,
            )

            verification_result = verify_tutorial_package(
                package_path=temp_pkg,
                trust_store=trust_store,
            )

            if not verification_result.is_valid:
                err_msg = ", ".join(verification_result.errors)
                raise PackageIntegrityError(f"Test package verification failed: {err_msg}")

            installed_info = controller.install_tutorial_package(
                package_path=temp_pkg,
                overwrite=overwrite,
                trust_store=trust_store,
            )

            return installed_info, verification_result

        finally:
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
