"""Secure package archive creation and verification for external tutorial packs (Phase 9.1).

Treats all tutorial packages (.octapack / ZIP archives) as untrusted data:
- Inspects and validates every entry prior to extraction or acceptance.
- Rejects path traversal ('..'), absolute paths, drive letters, and drive-relative paths.
- Rejects symlinks, hardlinks, and executable permissions.
- Rejects executable files and scripts (.exe, .dll, .bat, .ps1, .py, .sh, etc.).
- Rejects duplicate, ambiguous, or case-colliding entry names.
- Enforces strict security quotas: entry count, compressed/expanded sizes, and compression ratios.
- Verifies plugin.json manifest and task definitions in memory without extracting to disk.
- Creates archives atomically using temporary files and safe replacements.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import stat
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from octa.intelligence.tasks import TaskDefinition
from octa.tutorials.plugins import (
    TUTORIAL_PLUGIN_MANIFEST_FILENAME,
    PluginManifestError,
    PluginSecurityError,
    TutorialPluginError,
    TutorialPluginManifest,
)

logger = logging.getLogger(__name__)

PACKAGE_EXTENSION: str = ".octapack"

# ──────────────────────────────────────────
# Package Security Quotas & Limits
# ──────────────────────────────────────────

MAX_PACKAGE_FILE_COUNT: int = 100
MAX_PACKAGE_COMPRESSED_BYTES: int = 10 * 1024 * 1024  # 10 MB
MAX_PACKAGE_EXPANDED_BYTES: int = 25 * 1024 * 1024  # 25 MB
MAX_PACKAGE_FILE_BYTES: int = 5 * 1024 * 1024  # 5 MB per file
MAX_COMPRESSION_RATIO: float = 50.0  # Max uncompressed / compressed ratio (anti-zip-bomb)

# Executables, libraries, and script extensions strictly forbidden in tutorial packages
FORBIDDEN_EXTENSIONS: frozenset[str] = frozenset(
    {
        # Executables, binaries, and dynamic libraries
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".pyd",
        ".bin",
        ".com",
        ".scr",
        ".msi",
        ".msp",
        ".cpl",
        # Windows scripts
        ".bat",
        ".cmd",
        ".ps1",
        ".psm1",
        ".psd1",
        ".vbs",
        ".vbe",
        ".js",
        ".jse",
        ".wsf",
        ".wsh",
        # Unix scripts
        ".sh",
        ".bash",
        ".zsh",
        ".csh",
        ".ksh",
        # Python source, bytecode, and wheels
        ".py",
        ".pyw",
        ".pyc",
        ".pyo",
        ".whl",
        # Web / application code containers
        ".hta",
        ".jar",
        ".app",
        ".gadget",
    }
)


# ──────────────────────────────────────────
# Exception Hierarchy
# ──────────────────────────────────────────


class PackageError(TutorialPluginError):
    """Base exception for package packaging, inspection, and validation failures."""


class PackageSecurityError(PackageError, PluginSecurityError):
    """Raised when an archive violates path traversal, symlink, or executable constraints."""


class PackageQuotaError(PackageSecurityError):
    """Raised when an archive exceeds file count, size, or compression ratio limits."""


class PackageFormatError(PackageError, PluginManifestError):
    """Raised when an archive is corrupted, missing manifest, or improperly formatted."""


# ──────────────────────────────────────────
# Data Models
# ──────────────────────────────────────────


@dataclass(frozen=True)
class PackageVerificationResult:
    """Detailed result from inspecting and verifying a tutorial package."""

    is_valid: bool
    package_path: Path
    manifest: TutorialPluginManifest | None
    file_count: int
    total_compressed_bytes: int
    total_uncompressed_bytes: int
    file_entries: tuple[str, ...]
    sha256_digest: str
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def plugin_id(self) -> str:
        return self.manifest.plugin_id if self.manifest else ""

    @property
    def plugin_name(self) -> str:
        return self.manifest.name if self.manifest else ""

    @property
    def plugin_version(self) -> str:
        return self.manifest.version if self.manifest else ""


# ──────────────────────────────────────────
# Archive Digest & Inspection Helpers
# ──────────────────────────────────────────


def compute_file_sha256(path: Path) -> str:
    """Compute the SHA-256 hexadecimal digest of a file on disk."""
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def _validate_entry_name(name: str) -> None:
    """Validate entry path safety according to Zip Slip and naming constraints."""
    if not isinstance(name, str) or not name.strip():
        raise PackageSecurityError("Package contains entry with empty or whitespace filename.")

    if "\0" in name:
        raise PackageSecurityError("Package entry filename contains null byte.")

    # Standard ZIP paths MUST use forward slashes; backslashes or double slashes indicate evasion
    if "\\" in name or "//" in name:
        raise PackageSecurityError(
            f"Package entry uses backslash or consecutive slash path separator: {name!r}. "
            "Single forward slashes ('/') are required."
        )

    # Reject absolute paths (POSIX root or Windows drive prefixes)
    if name.startswith("/"):
        raise PackageSecurityError(f"Absolute path is forbidden in package entry: {name!r}.")

    if ":" in name:
        raise PackageSecurityError(f"Drive path or colon is forbidden in package entry: {name!r}.")

    # Path traversal check
    clean_path = Path(name)
    if ".." in clean_path.parts:
        raise PackageSecurityError(
            f"Path traversal ('..') is forbidden in package entry: {name!r}."
        )

    for part in clean_path.parts:
        if part in ("..", "."):
            raise PackageSecurityError(
                f"Relative directory escape ('{part}') forbidden in package entry: {name!r}."
            )


# ──────────────────────────────────────────
# Package Verification (Pure Inspection, Zero Extraction)
# ──────────────────────────────────────────


def verify_tutorial_package(
    package_path: Path | str,
    *,
    raise_on_error: bool = False,
) -> PackageVerificationResult:
    """Inspect and verify a tutorial package without extracting any files to disk.

    Validates:
    - Zip file integrity and valid archive structure.
    - Compressed and expanded size limits.
    - Entry count limits and compression ratio (anti-zip-bomb).
    - Path safety (Zip Slip traversal, absolute paths, drive letters).
    - Forbids symlinks, hardlinks, and executable permissions.
    - Forbids executable and script extensions (.exe, .dll, .bat, .ps1, .py, etc.).
    - Forbids duplicate or case-insensitive colliding entry names.
    - Root plugin.json manifest presence and schema conformance.
    - All referenced tutorial files existence, UTF-8 decoding, and TaskDefinition schemas.

    Args:
        package_path: Path to the .octapack or .zip package file.
        raise_on_error: If True, raises the underlying PackageError; if False, returns
                        a PackageVerificationResult with is_valid=False and error details.

    Returns:
        PackageVerificationResult describing the inspection findings.
    """
    pkg_file = Path(package_path).resolve()
    if not pkg_file.is_file():
        err = f"Package file does not exist: {pkg_file}"
        if raise_on_error:
            raise PackageFormatError(err)
        return PackageVerificationResult(
            is_valid=False,
            package_path=pkg_file,
            manifest=None,
            file_count=0,
            total_compressed_bytes=0,
            total_uncompressed_bytes=0,
            file_entries=(),
            sha256_digest="",
            errors=(err,),
        )

    # Check compressed size on disk
    file_size_on_disk = pkg_file.stat().st_size
    if file_size_on_disk > MAX_PACKAGE_COMPRESSED_BYTES:
        err = (
            f"Package archive size ({file_size_on_disk} bytes) exceeds maximum "
            f"compressed limit ({MAX_PACKAGE_COMPRESSED_BYTES} bytes)."
        )
        if raise_on_error:
            raise PackageQuotaError(err)
        return PackageVerificationResult(
            is_valid=False,
            package_path=pkg_file,
            manifest=None,
            file_count=0,
            total_compressed_bytes=file_size_on_disk,
            total_uncompressed_bytes=0,
            file_entries=(),
            sha256_digest=compute_file_sha256(pkg_file),
            errors=(err,),
        )

    sha256_digest = compute_file_sha256(pkg_file)

    try:
        if not zipfile.is_zipfile(pkg_file):
            raise PackageFormatError(f"File is not a valid zip archive: {pkg_file}")

        with zipfile.ZipFile(pkg_file, "r") as zf:
            infolist = zf.infolist()

            if len(infolist) > MAX_PACKAGE_FILE_COUNT:
                raise PackageQuotaError(
                    f"Package contains {len(infolist)} entries, exceeding maximum "
                    f"allowed limit ({MAX_PACKAGE_FILE_COUNT})."
                )

            total_uncompressed = 0
            total_compressed = 0
            file_entries: list[str] = []
            seen_entries_lower: set[str] = set()

            for info in infolist:
                entry_name = info.filename
                _validate_entry_name(entry_name)

                # Check duplicate or case-insensitive colliding names
                lower_name = entry_name.lower().rstrip("/")
                if lower_name in seen_entries_lower:
                    raise PackageSecurityError(
                        f"Duplicate or case-colliding entry in package: {entry_name!r}."
                    )
                seen_entries_lower.add(lower_name)

                # Directory entries
                if info.is_dir():
                    continue

                # File entry validation
                file_entries.append(entry_name)

                # Forbidden extension check
                ext = Path(entry_name).suffix.lower()
                if ext in FORBIDDEN_EXTENSIONS:
                    raise PackageSecurityError(
                        f"Forbidden file extension in package entry {entry_name!r}: '{ext}'. "
                        "Executable files and scripts are forbidden."
                    )

                # Symlink and POSIX permission checks from zip metadata
                mode = info.external_attr >> 16
                if mode != 0:
                    # S_IFLNK = 0o120000
                    if (mode & 0o170000) == 0o120000 or stat.S_ISLNK(mode):
                        raise PackageSecurityError(
                            f"Symlinks are forbidden in package entries: {entry_name!r}."
                        )
                    # S_IXUSR | S_IXGRP | S_IXOTH = 0o111
                    if (mode & 0o111) != 0:
                        raise PackageSecurityError(
                            "Executable permissions are forbidden in package entries: "
                            f"{entry_name!r}."
                        )

                # Individual file size quota
                if info.file_size > MAX_PACKAGE_FILE_BYTES:
                    raise PackageQuotaError(
                        f"Entry {entry_name!r} uncompressed size ({info.file_size} bytes) "
                        f"exceeds maximum file limit ({MAX_PACKAGE_FILE_BYTES} bytes)."
                    )

                total_uncompressed += info.file_size
                total_compressed += info.compress_size

                # Quota thresholds
                if total_uncompressed > MAX_PACKAGE_EXPANDED_BYTES:
                    raise PackageQuotaError(
                        f"Total uncompressed size ({total_uncompressed} bytes) exceeds "
                        f"maximum expanded limit ({MAX_PACKAGE_EXPANDED_BYTES} bytes)."
                    )

                if total_compressed > MAX_PACKAGE_COMPRESSED_BYTES:
                    raise PackageQuotaError(
                        f"Total compressed size ({total_compressed} bytes) exceeds "
                        f"maximum compressed limit ({MAX_PACKAGE_COMPRESSED_BYTES} bytes)."
                    )

                # Compression ratio check (anti-zip bomb)
                if info.compress_size > 0:
                    ratio = info.file_size / info.compress_size
                    if ratio > MAX_COMPRESSION_RATIO:
                        raise PackageQuotaError(
                            f"Compression ratio ({ratio:.1f}) for entry {entry_name!r} "
                            f"exceeds maximum limit ({MAX_COMPRESSION_RATIO})."
                        )
                elif info.file_size > 1024:
                    raise PackageQuotaError(
                        f"Suspicious zero-byte compressed entry with uncompressed size "
                        f"({info.file_size} bytes): {entry_name!r}."
                    )

            # Mandatory root plugin.json
            if TUTORIAL_PLUGIN_MANIFEST_FILENAME not in file_entries:
                raise PackageFormatError(
                    "Package missing mandatory root "
                    f"'{TUTORIAL_PLUGIN_MANIFEST_FILENAME}' manifest."
                )

            # Read and parse plugin.json
            try:
                manifest_bytes = zf.open(TUTORIAL_PLUGIN_MANIFEST_FILENAME).read(
                    MAX_PACKAGE_FILE_BYTES + 1
                )
                if len(manifest_bytes) > MAX_PACKAGE_FILE_BYTES:
                    raise PackageQuotaError("Manifest file exceeds maximum file size limit.")
                manifest_text = manifest_bytes.decode("utf-8")
                manifest_data = json.loads(manifest_text)
            except UnicodeDecodeError as exc:
                raise PackageFormatError(f"plugin.json is not valid UTF-8: {exc}") from exc
            except json.JSONDecodeError as exc:
                raise PackageFormatError(f"plugin.json contains invalid JSON: {exc}") from exc

            manifest = TutorialPluginManifest.from_dict(manifest_data)

            # Validate all referenced tutorial files exist in package and parse as TaskDefinition
            for tutorial_rel_path in manifest.tutorial_files:
                clean_rel = tutorial_rel_path.strip().replace("\\", "/")
                if clean_rel not in file_entries:
                    raise PackageFormatError(
                        f"Manifest references tutorial file '{clean_rel}' which is missing "
                        "from the package archive."
                    )

                try:
                    tutorial_bytes = zf.open(clean_rel).read(MAX_PACKAGE_FILE_BYTES + 1)
                    if len(tutorial_bytes) > MAX_PACKAGE_FILE_BYTES:
                        raise PackageQuotaError(
                            f"Tutorial file '{clean_rel}' exceeds maximum size limit."
                        )
                    tutorial_text = tutorial_bytes.decode("utf-8")
                    tutorial_data = json.loads(tutorial_text)
                    _task = TaskDefinition.from_dict(tutorial_data)
                except UnicodeDecodeError as exc:
                    raise PackageFormatError(
                        f"Tutorial file '{clean_rel}' is not valid UTF-8: {exc}"
                    ) from exc
                except json.JSONDecodeError as exc:
                    raise PackageFormatError(
                        f"Tutorial file '{clean_rel}' contains invalid JSON: {exc}"
                    ) from exc
                except Exception as exc:
                    raise PackageFormatError(
                        f"Tutorial file '{clean_rel}' schema validation failed: {exc}"
                    ) from exc

            return PackageVerificationResult(
                is_valid=True,
                package_path=pkg_file,
                manifest=manifest,
                file_count=len(file_entries),
                total_compressed_bytes=total_compressed,
                total_uncompressed_bytes=total_uncompressed,
                file_entries=tuple(file_entries),
                sha256_digest=sha256_digest,
                errors=(),
            )

    except PackageError as exc:
        if raise_on_error:
            raise
        return PackageVerificationResult(
            is_valid=False,
            package_path=pkg_file,
            manifest=None,
            file_count=0,
            total_compressed_bytes=0,
            total_uncompressed_bytes=0,
            file_entries=(),
            sha256_digest=sha256_digest,
            errors=(str(exc),),
        )
    except Exception as exc:
        msg = f"Unexpected error reading package: {exc}"
        if raise_on_error:
            raise PackageFormatError(msg) from exc
        return PackageVerificationResult(
            is_valid=False,
            package_path=pkg_file,
            manifest=None,
            file_count=0,
            total_compressed_bytes=0,
            total_uncompressed_bytes=0,
            file_entries=(),
            sha256_digest=sha256_digest,
            errors=(msg,),
        )


# ──────────────────────────────────────────
# Package Creation (Atomic & Validated)
# ──────────────────────────────────────────


def pack_tutorial_package(
    source_dir: Path | str,
    output_path: Path | str | None = None,
    *,
    overwrite: bool = False,
) -> Path:
    """Pack an external tutorial plugin directory into a validated .octapack archive.

    - Validates source directory structure and manifest before packing.
    - Gathers manifest, referenced tutorial files, and supporting documentation.
    - Enforces quotas, path safety, and forbids executable scripts.
    - Builds the archive atomically using a temporary file.
    - Runs full verify_tutorial_package() on the temporary archive before moving it into place.

    Args:
        source_dir: Directory containing plugin.json and tutorial JSON files.
        output_path: Target path for .octapack file. Defaults to parent_dir/<id>.octapack.
        overwrite: Whether to overwrite target if it already exists.

    Returns:
        Path to the successfully created package.
    """
    src = Path(source_dir).resolve()
    if not src.is_dir():
        raise PackageFormatError(f"Source directory does not exist or is not a directory: {src}")

    manifest_file = src / TUTORIAL_PLUGIN_MANIFEST_FILENAME
    if not manifest_file.is_file():
        raise PackageFormatError(
            f"Source directory missing mandatory '{TUTORIAL_PLUGIN_MANIFEST_FILENAME}': {src}"
        )

    # Pre-scan entire source directory: reject forbidden files, scripts, or symlinks
    for item in src.rglob("*"):
        if item.is_symlink():
            raise PackageSecurityError(
                f"Symlinks are forbidden in package source directory: {item}"
            )
        if item.is_file():
            ext = item.suffix.lower()
            if ext in FORBIDDEN_EXTENSIONS:
                raise PackageSecurityError(
                    f"Forbidden file extension '{ext}' in source directory: {item}. "
                    "Executable files and scripts are forbidden."
                )

    # Parse and validate manifest
    try:
        manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))
        manifest = TutorialPluginManifest.from_dict(manifest_data)
    except Exception as exc:
        raise PackageFormatError(f"Failed to parse source plugin manifest: {exc}") from exc

    # Resolve output path
    if output_path is None:
        target_file = (src.parent / f"{manifest.plugin_id}{PACKAGE_EXTENSION}").resolve()
    else:
        target_file = Path(output_path).resolve()
        if not target_file.name.endswith(PACKAGE_EXTENSION) and not target_file.name.endswith(
            ".zip"
        ):
            target_file = target_file.with_name(f"{target_file.name}{PACKAGE_EXTENSION}")

    if target_file.exists() and not overwrite:
        raise FileExistsError(
            f"Target package already exists: {target_file}. Use overwrite=True to replace."
        )

    target_file.parent.mkdir(parents=True, exist_ok=True)

    # Collect files to pack: plugin.json, referenced tutorial files, and safe docs/assets
    collected_files: list[tuple[Path, str]] = []
    seen_rel_paths: set[str] = set()

    def add_file(fpath: Path, rel_str: str) -> None:
        cleaned_rel = rel_str.strip().replace("\\", "/")
        _validate_entry_name(cleaned_rel)

        lower_rel = cleaned_rel.lower()
        if lower_rel in seen_rel_paths:
            raise PackageSecurityError(f"Duplicate entry name: {cleaned_rel!r}")
        seen_rel_paths.add(lower_rel)

        if not fpath.is_file():
            raise PackageFormatError(f"Referenced file not found: {fpath}")

        ext = fpath.suffix.lower()
        if ext in FORBIDDEN_EXTENSIONS:
            raise PackageSecurityError(
                f"Forbidden file extension '{ext}' in source file: {fpath}. "
                "Executable files and scripts are forbidden."
            )

        if fpath.is_symlink():
            raise PackageSecurityError(f"Symlinks are forbidden in package sources: {fpath}")

        sz = fpath.stat().st_size
        if sz > MAX_PACKAGE_FILE_BYTES:
            raise PackageQuotaError(
                f"File '{cleaned_rel}' ({sz} bytes) exceeds maximum file limit "
                f"({MAX_PACKAGE_FILE_BYTES} bytes)."
            )

        collected_files.append((fpath, cleaned_rel))

    # Add manifest
    add_file(manifest_file, TUTORIAL_PLUGIN_MANIFEST_FILENAME)

    # Add referenced tutorial files
    for t_rel in manifest.tutorial_files:
        t_path = (src / t_rel).resolve()
        try:
            t_path.relative_to(src)
        except ValueError as exc:
            raise PackageSecurityError(
                f"Tutorial file path escapes source root: {t_rel!r}"
            ) from exc

        add_file(t_path, t_rel)

    # Also include safe documentation files (e.g. README.md, LICENSE.txt) if present in source root
    for entry in src.iterdir():
        if entry.is_file() and not entry.name.startswith("."):
            ext = entry.suffix.lower()
            if ext in {".md", ".txt"} and entry.name.lower() not in seen_rel_paths:
                add_file(entry, entry.name)

    # Check aggregate count and uncompressed size
    if len(collected_files) > MAX_PACKAGE_FILE_COUNT:
        raise PackageQuotaError(
            f"Package contains {len(collected_files)} files, "
            f"exceeding limit ({MAX_PACKAGE_FILE_COUNT})."
        )

    total_uncompressed = sum(f[0].stat().st_size for f in collected_files)
    if total_uncompressed > MAX_PACKAGE_EXPANDED_BYTES:
        raise PackageQuotaError(
            f"Total uncompressed size ({total_uncompressed} bytes) exceeds limit "
            f"({MAX_PACKAGE_EXPANDED_BYTES} bytes)."
        )

    # Create package atomically via temporary file
    tmp_fd, tmp_file_str = tempfile.mkstemp(
        dir=target_file.parent,
        prefix=".tmp_octapack_",
        suffix=PACKAGE_EXTENSION,
    )
    os.close(tmp_fd)
    tmp_path = Path(tmp_file_str)

    try:
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for source_file, archive_rel in collected_files:
                # Use standard file permissions (0o644) without executable bits
                zinfo = zipfile.ZipInfo(filename=archive_rel)
                # Standard regular file mode 0o644 (no exec bits, no symlinks)
                zinfo.external_attr = 0o644 << 16
                zinfo.compress_type = zipfile.ZIP_DEFLATED
                with open(source_file, "rb") as sf:
                    data = sf.read()
                zf.writestr(zinfo, data)

        # Verify temporary package before committing to destination
        verification = verify_tutorial_package(tmp_path, raise_on_error=True)
        if not verification.is_valid:
            err_msg = "; ".join(verification.errors)
            raise PackageFormatError(f"Newly built package failed verification: {err_msg}")

        # Atomic replacement
        tmp_path.replace(target_file)
        logger.info(
            "Successfully packed tutorial package '%s' (%s) -> %s",
            manifest.plugin_id,
            manifest.version,
            target_file,
        )
        return target_file

    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass


# ──────────────────────────────────────────
# CLI Command Handlers
# ──────────────────────────────────────────


def run_pack_cli(args: argparse.Namespace) -> int:
    """Execute the 'octa pack' CLI command."""
    source_dir: Path = args.source_dir
    output_path: Path | None = args.output
    overwrite: bool = bool(args.overwrite)

    print(f"OCTA Tutorial Packager — Packing '{source_dir}'...")
    try:
        out_file = pack_tutorial_package(source_dir, output_path=output_path, overwrite=overwrite)
        result = verify_tutorial_package(out_file, raise_on_error=True)
        print("  [SUCCESS] Package created successfully.")
        print(f"  Package Path:  {out_file}")
        print(f"  Plugin ID:     {result.plugin_id}")
        print(f"  Name:          {result.plugin_name}")
        print(f"  Version:       {result.plugin_version}")
        print(f"  Files:         {result.file_count} entry(ies)")
        print(
            f"  Size:          {result.total_uncompressed_bytes:,} bytes uncompressed -> "
            f"{result.total_compressed_bytes:,} bytes compressed"
        )
        print(f"  SHA-256:       {result.sha256_digest}")
        return 0
    except Exception as exc:
        print(f"  [ERROR] Packaging failed: {exc}", file=sys.stderr)
        return 1


def run_verify_cli(args: argparse.Namespace) -> int:
    """Execute the 'octa verify' CLI command."""
    package_path: Path = args.package_path
    verbose: bool = bool(getattr(args, "verbose", False))

    print(f"OCTA Tutorial Package Verifier — Inspecting '{package_path}'...")
    result = verify_tutorial_package(package_path, raise_on_error=False)

    if not result.is_valid:
        print("  [FAIL] Package validation failed:", file=sys.stderr)
        for err in result.errors:
            print(f"    - {err}", file=sys.stderr)
        return 1

    print("  [PASS] Valid OCTA tutorial package.")
    print(f"  Plugin ID:     {result.plugin_id}")
    print(f"  Name:          {result.plugin_name}")
    print(f"  Version:       {result.plugin_version}")
    if result.manifest and result.manifest.author:
        print(f"  Author:        {result.manifest.author}")
    if result.manifest and result.manifest.description:
        print(f"  Description:   {result.manifest.description}")
    print(f"  Files:         {result.file_count} entry(ies)")
    print(
        f"  Size:          {result.total_uncompressed_bytes:,} bytes uncompressed, "
        f"{result.total_compressed_bytes:,} bytes compressed"
    )
    print(f"  SHA-256:       {result.sha256_digest}")

    if verbose:
        print("  Entries:")
        for entry in result.file_entries:
            print(f"    - {entry}")

    return 0
