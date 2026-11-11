"""Secure package archive creation and verification for external tutorial packs (Phase 9.1 & 9.2).

Treats all tutorial packages (.octapack / ZIP archives) as untrusted data:
- Inspects and validates every entry prior to extraction or acceptance.
- Rejects path traversal ('..'), absolute paths, drive letters, and drive-relative paths.
- Rejects symlinks, hardlinks, and executable permissions.
- Rejects executable files and scripts (.exe, .dll, .bat, .ps1, .py, .sh, etc.).
- Rejects duplicate, ambiguous, or case-colliding entry names.
- Enforces strict security quotas: entry count, compressed/expanded sizes, and compression ratios.
- Verifies deterministic SHA-256 digests for all packaged assets.
- Verifies Ed25519 cryptographic signatures against a pinned trust store without network calls.
- Classifies package trust levels: TRUSTED_SIGNED, UNSIGNED_COMMUNITY, or INVALID_TAMPERED.
- Creates archives atomically using temporary files and safe replacements.
"""

from __future__ import annotations

import argparse
import enum
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
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ed25519

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


class PackageIntegrityError(PackageSecurityError):
    """Raised when asset digests mismatch or cryptographic signature verification fails."""


# ──────────────────────────────────────────
# Trust Model & Data Structures (Phase 9.2)
# ──────────────────────────────────────────


class PackageTrustLevel(enum.Enum):
    """Trust classification for tutorial packages.

    - TRUSTED_SIGNED: Package has valid asset digests, valid manifest, and a mathematically
      valid Ed25519 signature whose public key is pinned in the active TrustStore.
    - SIGNED_UNKNOWN_KEY: Package has valid asset digests and a mathematically valid Ed25519
      signature, but the signing public key is unpinned / unknown in the active TrustStore.
    - UNSIGNED_COMMUNITY: Package has valid asset digests and valid schema, but carries no
      cryptographic digital signature.
    - INVALID_TAMPERED: Package has asset digest mismatches, corrupted or forged signatures,
      unsupported cryptographic algorithms, or schema violations.
    """

    TRUSTED_SIGNED = "trusted_signed"
    SIGNED_UNKNOWN_KEY = "signed_unknown_key"
    UNSIGNED_COMMUNITY = "unsigned_community"
    INVALID_TAMPERED = "invalid_tampered"


@dataclass(frozen=True)
class TrustedKey:
    """A pinned public key in the trust store."""

    key_id: str
    name: str
    algorithm: str
    public_key_hex: str
    description: str = ""

    def __post_init__(self) -> None:
        if not self.key_id or not isinstance(self.key_id, str) or not self.key_id.strip():
            raise ValueError("TrustedKey 'key_id' must be a non-empty string.")
        if not self.name or not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("TrustedKey 'name' must be a non-empty string.")
        if self.algorithm != "ed25519":
            raise ValueError(
                f"Unsupported algorithm '{self.algorithm}'. Only 'ed25519' is supported."
            )
        clean_hex = self.public_key_hex.strip().lower()
        if len(clean_hex) != 64:
            raise ValueError(
                f"Ed25519 public key hex must be exactly 64 characters (32 bytes), "
                f"got {len(clean_hex)}."
            )
        try:
            bytes.fromhex(clean_hex)
        except ValueError as exc:
            raise ValueError(f"Invalid hexadecimal string for public_key_hex: {exc}") from exc
        object.__setattr__(self, "public_key_hex", clean_hex)


# Default pinned trusted keys provisioned out-of-the-box.
# By default, OCTA ships with an empty trust store until an official project signing key
# is established and published through an authentic, verified distribution channel.
# Trusted keys can be pinned by configuring a TrustStore with TrustedKey instances,
# loading a verified trust store JSON file (TrustStore.from_json_file),
# or passing --trust-store <path> via the CLI.
DEFAULT_TRUSTED_KEYS: tuple[TrustedKey, ...] = ()


class TrustStore:
    """In-memory and file-backed store of pinned public signing keys."""

    def __init__(
        self,
        keys: tuple[TrustedKey, ...] | list[TrustedKey] | None = None,
    ) -> None:
        self._keys: dict[str, TrustedKey] = {}
        if keys:
            for k in keys:
                self.add_key(k)

    def add_key(self, key: TrustedKey) -> None:
        """Add or replace a trusted pinned key."""
        self._keys[key.public_key_hex.lower()] = key

    def get_by_public_key(self, public_key_hex: str) -> TrustedKey | None:
        """Lookup a trusted key by its 64-character public key hex string."""
        return self._keys.get(public_key_hex.strip().lower())

    def get_by_key_id(self, key_id: str) -> TrustedKey | None:
        """Lookup a trusted key by its key identifier string."""
        for k in self._keys.values():
            if k.key_id == key_id:
                return k
        return None

    def is_trusted(self, public_key_hex: str) -> bool:
        """Check whether a public key is pinned and trusted in this store."""
        return public_key_hex.strip().lower() in self._keys

    def __len__(self) -> int:
        return len(self._keys)

    @classmethod
    def default(cls) -> TrustStore:
        """Create a TrustStore populated with default pinned OCTA keys."""
        return cls(DEFAULT_TRUSTED_KEYS)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TrustStore:
        """Load a TrustStore from a validated dictionary structure."""
        if not isinstance(data, dict):
            raise ValueError("TrustStore root must be a JSON object.")
        keys_list = data.get("trusted_keys", [])
        if not isinstance(keys_list, (list, tuple)):
            raise ValueError("Field 'trusted_keys' must be a list.")

        store = cls()
        for idx, entry in enumerate(keys_list):
            if not isinstance(entry, dict):
                raise ValueError(f"trusted_keys[{idx}] must be a dictionary object.")
            key = TrustedKey(
                key_id=str(entry.get("key_id", "")),
                name=str(entry.get("name", "")),
                algorithm=str(entry.get("algorithm", "ed25519")),
                public_key_hex=str(entry.get("public_key_hex", "")),
                description=str(entry.get("description", "")),
            )
            store.add_key(key)
        return store

    @classmethod
    def from_json_file(cls, path: Path | str) -> TrustStore:
        """Load a TrustStore from a JSON file on disk."""
        p = Path(path).resolve()
        if not p.is_file():
            raise FileNotFoundError(f"Trust store file not found: {p}")
        try:
            content = p.read_text(encoding="utf-8")
            data = json.loads(content)
            return cls.from_dict(data)
        except Exception as exc:
            raise ValueError(f"Failed to load trust store from {p}: {exc}") from exc


# ──────────────────────────────────────────
# Canonical Serialization & Digests
# ──────────────────────────────────────────


def canonical_manifest_bytes(manifest: dict[str, Any] | TutorialPluginManifest) -> bytes:
    """Serialize manifest data into deterministic, canonical UTF-8 JSON bytes.

    OCTA Canonical Manifest Byte Format Specification:
    - Scope: Covers all security-relevant manifest fields (schema_version, id, name, version,
      author, description, tutorial_files, and asset_digests).
    - Signature Exclusion: The 'signature' field is strictly excluded from serialization
      so that the computed bytes represent only the immutable manifest content.
    - Key Ordering: Keys are sorted lexicographically at all nesting depths (sort_keys=True)
      to ensure serialization is independent of dictionary key insertion order.
    - Token Separators: Compact separators without superfluous whitespace: ',' between items
      and ':' between key and value (separators=(',', ':')).
    - Character Encoding: Direct UTF-8 byte encoding without ASCII escaping (ensure_ascii=False),
      ensuring literal representations of Unicode characters across platforms.
    """
    if isinstance(manifest, TutorialPluginManifest):
        payload = manifest.to_dict(include_signature=False)
    elif isinstance(manifest, dict):
        payload = dict(manifest)
        payload.pop("signature", None)
    else:
        raise TypeError(f"Expected TutorialPluginManifest or dict, got {type(manifest).__name__}.")

    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


# Backward-compatibility alias
canonical_json_bytes = canonical_manifest_bytes


def compute_file_sha256(path: Path) -> str:
    """Compute the SHA-256 hexadecimal digest of a file on disk."""
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def compute_bytes_sha256(data: bytes) -> str:
    """Compute the SHA-256 hexadecimal digest of a byte sequence."""
    return hashlib.sha256(data).hexdigest()


# ──────────────────────────────────────────
# Cryptographic Signing & Verification Helpers
# ──────────────────────────────────────────


def sign_manifest(
    manifest_dict: dict[str, Any] | TutorialPluginManifest,
    signing_key: ed25519.Ed25519PrivateKey,
    key_id: str = "community",
) -> dict[str, Any]:
    """Cryptographically sign a manifest using Ed25519.

    Computes the signature over the OCTA canonical manifest bytes of the payload
    excluding the 'signature' field itself.

    Args:
        manifest_dict: Manifest dictionary or TutorialPluginManifest instance.
        signing_key: Ed25519 private key instance.
        key_id: Key identifier associated with the public key.

    Returns:
        Signature dictionary block to embed into the manifest.
    """
    canonical_bytes = canonical_manifest_bytes(manifest_dict)
    sig_bytes = signing_key.sign(canonical_bytes)
    pub_bytes = signing_key.public_key().public_bytes_raw()

    return {
        "algorithm": "ed25519",
        "key_id": key_id,
        "public_key": pub_bytes.hex(),
        "signature": sig_bytes.hex(),
    }


def verify_manifest_signature(
    manifest_dict: dict[str, Any],
    trust_store: TrustStore | None = None,
) -> tuple[PackageTrustLevel, str, str]:
    """Verify the Ed25519 cryptographic signature of a manifest dictionary.

    Args:
        manifest_dict: The manifest dictionary as loaded from plugin.json.
        trust_store: Pinned trust store to check public key validity against.

    Returns:
        tuple[PackageTrustLevel, signer_id, signer_name]
    """
    store = trust_store or TrustStore.default()
    sig_block = manifest_dict.get("signature")

    if not sig_block:
        return PackageTrustLevel.UNSIGNED_COMMUNITY, "", ""

    if not isinstance(sig_block, dict):
        raise PackageFormatError("Manifest 'signature' block must be a dictionary object.")

    algorithm = str(sig_block.get("algorithm", "")).strip().lower()
    if algorithm != "ed25519":
        raise PackageSecurityError(
            f"Unsupported signature algorithm '{algorithm}'. Only 'ed25519' is supported."
        )

    pub_hex = str(sig_block.get("public_key", "")).strip().lower()
    sig_hex = str(sig_block.get("signature", "")).strip().lower()
    key_id = str(sig_block.get("key_id", "")).strip()

    if len(pub_hex) != 64:
        raise PackageIntegrityError(
            f"Ed25519 public key hex must be 64 characters, got {len(pub_hex)}."
        )
    if len(sig_hex) != 128:
        raise PackageIntegrityError(
            f"Ed25519 signature hex must be 128 characters (64 bytes), got {len(sig_hex)}."
        )

    try:
        pub_bytes = bytes.fromhex(pub_hex)
        sig_bytes = bytes.fromhex(sig_hex)
    except ValueError as exc:
        raise PackageIntegrityError(f"Malformed hexadecimal in signature block: {exc}") from exc

    # Canonical payload bytes excluding signature field
    canonical_bytes = canonical_manifest_bytes(manifest_dict)

    try:
        public_key = ed25519.Ed25519PublicKey.from_public_bytes(pub_bytes)
        public_key.verify(sig_bytes, canonical_bytes)
    except InvalidSignature as exc:
        raise PackageIntegrityError(
            "Cryptographic signature verification failed: signature mismatch or payload tampered."
        ) from exc
    except Exception as exc:
        raise PackageIntegrityError(f"Cryptographic public key verification error: {exc}") from exc

    # Signature is mathematically valid; now evaluate trust store pinning
    trusted_entry = store.get_by_public_key(pub_hex)
    if trusted_entry is not None:
        return PackageTrustLevel.TRUSTED_SIGNED, trusted_entry.key_id, trusted_entry.name

    # Valid signature, but key is unpinned / unknown
    signer_name = f"Unknown Signer ({key_id or pub_hex[:16]}...)"
    return PackageTrustLevel.SIGNED_UNKNOWN_KEY, key_id, signer_name


# ──────────────────────────────────────────
# Package Verification Result Model
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
    trust_level: PackageTrustLevel = PackageTrustLevel.INVALID_TAMPERED
    signer_id: str = ""
    signer_name: str = ""
    asset_digests_verified: int = 0
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

    @property
    def is_trusted_signed(self) -> bool:
        """True when package is valid and signed by a trusted pinned key."""
        return self.is_valid and self.trust_level == PackageTrustLevel.TRUSTED_SIGNED

    @property
    def is_signed_unknown_key(self) -> bool:
        """True when valid and signed, but the key is not pinned in TrustStore."""
        return self.is_valid and self.trust_level == PackageTrustLevel.SIGNED_UNKNOWN_KEY

    @property
    def is_unsigned_community(self) -> bool:
        """True when package is valid and completely unsigned."""
        return self.is_valid and self.trust_level == PackageTrustLevel.UNSIGNED_COMMUNITY


# ──────────────────────────────────────────
# Archive Entry Safety Helper
# ──────────────────────────────────────────


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
    trust_store: TrustStore | None = None,
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
    - Deterministic SHA-256 asset digests for all packaged assets.
    - Ed25519 digital signature against the configured TrustStore (offline, no network).

    Args:
        package_path: Path to the .octapack or .zip package file.
        trust_store: Optional custom TrustStore (defaults to TrustStore.default()).
        raise_on_error: If True, raises the underlying PackageError; if False, returns
                        a PackageVerificationResult with is_valid=False and error details.

    Returns:
        PackageVerificationResult describing the inspection findings.
    """
    pkg_file = Path(package_path).resolve()
    active_trust_store = trust_store or TrustStore.default()

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
            trust_level=PackageTrustLevel.INVALID_TAMPERED,
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
            trust_level=PackageTrustLevel.INVALID_TAMPERED,
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

            # ──────────────────────────────────────────
            # Deterministic Asset Digest Verification (Phase 9.2)
            # ──────────────────────────────────────────
            digests_verified = 0
            if manifest.asset_digests:
                for asset_name, expected_digest in manifest.asset_digests.items():
                    clean_asset_name = asset_name.strip().replace("\\", "/")
                    if clean_asset_name not in file_entries:
                        raise PackageIntegrityError(
                            f"Asset digest specified for file '{clean_asset_name}' "
                            "missing from package."
                        )
                    raw_bytes = zf.open(clean_asset_name).read(MAX_PACKAGE_FILE_BYTES + 1)
                    actual_digest = compute_bytes_sha256(raw_bytes)
                    if actual_digest != expected_digest.lower():
                        raise PackageIntegrityError(
                            f"Asset digest mismatch for '{clean_asset_name}': "
                            f"expected {expected_digest}, got {actual_digest}."
                        )
                    digests_verified += 1

                # Ensure every referenced tutorial file has a verified digest
                for t_rel in manifest.tutorial_files:
                    clean_t_rel = t_rel.strip().replace("\\", "/")
                    if clean_t_rel not in manifest.asset_digests:
                        raise PackageIntegrityError(
                            f"Referenced tutorial file '{clean_t_rel}' is missing "
                            "from asset_digests."
                        )

            # ──────────────────────────────────────────
            # Cryptographic Signature Verification (Phase 9.2)
            # ──────────────────────────────────────────
            trust_level, signer_id, signer_name = verify_manifest_signature(
                manifest_data,
                trust_store=active_trust_store,
            )

            return PackageVerificationResult(
                is_valid=True,
                package_path=pkg_file,
                manifest=manifest,
                file_count=len(file_entries),
                total_compressed_bytes=total_compressed,
                total_uncompressed_bytes=total_uncompressed,
                file_entries=tuple(file_entries),
                sha256_digest=sha256_digest,
                trust_level=trust_level,
                signer_id=signer_id,
                signer_name=signer_name,
                asset_digests_verified=digests_verified,
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
            trust_level=PackageTrustLevel.INVALID_TAMPERED,
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
            trust_level=PackageTrustLevel.INVALID_TAMPERED,
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
    signing_key: ed25519.Ed25519PrivateKey | None = None,
    key_id: str = "octa-official",
) -> Path:
    """Pack an external tutorial plugin directory into a validated .octapack archive.

    - Validates source directory structure and manifest before packing.
    - Computes deterministic SHA-256 asset digests for all tutorial definitions.
    - Optionally signs the canonical manifest with an Ed25519 private key.
    - Gathers manifest, referenced tutorial files, and supporting documentation.
    - Enforces quotas, path safety, and forbids executable scripts.
    - Builds the archive atomically using a temporary file.
    - Runs full verify_tutorial_package() on the temporary archive before moving it into place.

    Args:
        source_dir: Directory containing plugin.json and tutorial JSON files.
        output_path: Target path for .octapack file. Defaults to parent_dir/<id>.octapack.
        overwrite: Whether to overwrite target if it already exists.
        signing_key: Optional Ed25519 private key to cryptographically sign the package.
        key_id: Pinned key identifier associated with the signing key.

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

    # Collect files to pack: referenced tutorial files and safe docs
    collected_files: list[tuple[Path, str, bytes]] = []
    seen_rel_paths: set[str] = set()
    asset_digests: dict[str, str] = {}

    def collect_file_bytes(fpath: Path, rel_str: str) -> bytes:
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

        with open(fpath, "rb") as sf:
            raw_bytes = sf.read()

        collected_files.append((fpath, cleaned_rel, raw_bytes))
        return raw_bytes

    # Collect referenced tutorial files and compute their SHA-256 digests
    for t_rel in manifest.tutorial_files:
        t_path = (src / t_rel).resolve()
        try:
            t_path.relative_to(src)
        except ValueError as exc:
            raise PackageSecurityError(
                f"Tutorial file path escapes source root: {t_rel!r}"
            ) from exc

        t_bytes = collect_file_bytes(t_path, t_rel)
        clean_t_rel = t_rel.strip().replace("\\", "/")
        asset_digests[clean_t_rel] = compute_bytes_sha256(t_bytes)

    # Collect safe documentation files if present
    for entry in src.iterdir():
        if entry.is_file() and not entry.name.startswith("."):
            ext = entry.suffix.lower()
            if ext in {".md", ".txt"} and entry.name.lower() not in seen_rel_paths:
                doc_bytes = collect_file_bytes(entry, entry.name)
                asset_digests[entry.name] = compute_bytes_sha256(doc_bytes)

    # Prepare updated manifest dictionary with deterministic asset digests
    manifest_dict = manifest.to_dict(include_signature=False)
    manifest_dict["asset_digests"] = asset_digests

    # If signing requested, compute Ed25519 signature over canonical JSON manifest
    if signing_key is not None:
        sig_block = sign_manifest(manifest_dict, signing_key, key_id=key_id)
        manifest_dict["signature"] = sig_block
    elif manifest.signature is not None:
        # Preserve existing valid signature if already present in source
        manifest_dict["signature"] = manifest.signature

    manifest_bytes = json.dumps(manifest_dict, indent=2).encode("utf-8")
    collected_files.insert(0, (manifest_file, TUTORIAL_PLUGIN_MANIFEST_FILENAME, manifest_bytes))

    # Check aggregate count and uncompressed size
    if len(collected_files) > MAX_PACKAGE_FILE_COUNT:
        raise PackageQuotaError(
            f"Package contains {len(collected_files)} files, "
            f"exceeding limit ({MAX_PACKAGE_FILE_COUNT})."
        )

    total_uncompressed = sum(len(f[2]) for f in collected_files)
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
            for _source_file, archive_rel, file_data in collected_files:
                zinfo = zipfile.ZipInfo(filename=archive_rel)
                # Standard regular file mode 0o644 (no exec bits, no symlinks)
                zinfo.external_attr = 0o644 << 16
                zinfo.compress_type = zipfile.ZIP_DEFLATED
                zf.writestr(zinfo, file_data)

        # Verify temporary package before committing to destination
        verification = verify_tutorial_package(tmp_path, raise_on_error=True)
        if not verification.is_valid:
            err_msg = "; ".join(verification.errors)
            raise PackageFormatError(f"Newly built package failed verification: {err_msg}")

        # Atomic replacement
        tmp_path.replace(target_file)
        logger.info(
            "Successfully packed tutorial package '%s' (%s) -> %s [trust: %s]",
            manifest.plugin_id,
            manifest.version,
            target_file,
            verification.trust_level.value,
        )
        return target_file

    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass


# ──────────────────────────────────────────
# Key Loading Helper for CLI / Tests
# ──────────────────────────────────────────


def load_private_key_from_hex_or_file(key_source: Path | str) -> ed25519.Ed25519PrivateKey:
    """Load an Ed25519 private key from a 64-char hex string or a file containing the key."""
    raw_str = ""
    p = Path(key_source)
    if p.is_file():
        raw_str = p.read_text(encoding="utf-8").strip()
    else:
        raw_str = str(key_source).strip()

    if len(raw_str) == 64:
        try:
            return ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(raw_str))
        except ValueError as exc:
            raise ValueError(f"Invalid hexadecimal string for Ed25519 private key: {exc}") from exc

    raise ValueError(
        "Ed25519 private key must be a 64-character (32-byte) hexadecimal string or file."
    )


# ──────────────────────────────────────────
# CLI Command Handlers
# ──────────────────────────────────────────


def run_pack_cli(args: argparse.Namespace) -> int:
    """Execute the 'octa pack' CLI command."""
    source_dir: Path = args.source_dir
    output_path: Path | None = args.output
    overwrite: bool = bool(args.overwrite)
    key_file: Path | None = getattr(args, "signing_key", None)
    key_id: str = getattr(args, "key_id", "community") or "community"

    signing_key: ed25519.Ed25519PrivateKey | None = None
    if key_file:
        try:
            signing_key = load_private_key_from_hex_or_file(key_file)
        except Exception as exc:
            print(f"  [ERROR] Failed to load signing key: {exc}", file=sys.stderr)
            return 1

    print(f"OCTA Tutorial Packager — Packing '{source_dir}'...")
    try:
        out_file = pack_tutorial_package(
            source_dir,
            output_path=output_path,
            overwrite=overwrite,
            signing_key=signing_key,
            key_id=key_id,
        )
        result = verify_tutorial_package(out_file, raise_on_error=True)
        print("  [SUCCESS] Package created successfully.")
        print(f"  Package Path:  {out_file}")
        print(f"  Plugin ID:     {result.plugin_id}")
        print(f"  Name:          {result.plugin_name}")
        print(f"  Version:       {result.plugin_version}")
        print(f"  Trust Status:  {result.trust_level.name}")
        if result.signer_name:
            print(f"  Signer:        {result.signer_name} [{result.signer_id}]")
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
    trust_store_path: Path | None = getattr(args, "trust_store", None)

    trust_store: TrustStore | None = None
    if trust_store_path:
        try:
            trust_store = TrustStore.from_json_file(trust_store_path)
        except Exception as exc:
            print(
                f"  [ERROR] Failed to load trust store from {trust_store_path}: {exc}",
                file=sys.stderr,
            )
            return 1

    print(f"OCTA Tutorial Package Verifier — Inspecting '{package_path}'...")
    result = verify_tutorial_package(package_path, trust_store=trust_store, raise_on_error=False)

    if not result.is_valid:
        print("  [FAIL] Package validation failed:", file=sys.stderr)
        print(f"  Trust Status:  {result.trust_level.name}", file=sys.stderr)
        for err in result.errors:
            print(f"    - {err}", file=sys.stderr)
        return 1

    print("  [PASS] Valid OCTA tutorial package.")
    print(f"  Trust Status:  {result.trust_level.name}")
    if result.signer_name:
        print(f"  Signer:        {result.signer_name} [{result.signer_id}]")
    print(f"  Plugin ID:     {result.plugin_id}")
    print(f"  Name:          {result.plugin_name}")
    print(f"  Version:       {result.plugin_version}")
    if result.manifest and result.manifest.author:
        print(f"  Author:        {result.manifest.author}")
    if result.manifest and result.manifest.description:
        print(f"  Description:   {result.manifest.description}")
    print(f"  Files:         {result.file_count} entry(ies)")
    if result.asset_digests_verified:
        print(f"  Digests:       {result.asset_digests_verified} verified")
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
