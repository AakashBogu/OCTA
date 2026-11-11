"""Unit tests for Phase 9.1 & 9.2 — Secure Tutorial Package Archive, Integrity & Trust Model."""

from __future__ import annotations

import json
import stat
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519

from octa.__main__ import main
from octa.tutorials.packaging import (
    MAX_PACKAGE_FILE_COUNT,
    PACKAGE_EXTENSION,
    PackageFormatError,
    PackageIntegrityError,
    PackageQuotaError,
    PackageSecurityError,
    PackageTrustLevel,
    PackageVerificationResult,
    TrustedKey,
    TrustStore,
    canonical_json_bytes,
    canonical_manifest_bytes,
    compute_bytes_sha256,
    compute_file_sha256,
    pack_tutorial_package,
    verify_tutorial_package,
)

SAMPLE_PLUGIN_ID = "paint-pack"
SAMPLE_MANIFEST = {
    "schema_version": "1.0",
    "id": SAMPLE_PLUGIN_ID,
    "name": "MS Paint Tutorial Pack",
    "version": "1.0.0",
    "author": "OCTA Community",
    "description": "Tutorials for creating artwork in MS Paint.",
    "tutorial_files": ["tutorials/draw_shape.json", "tutorials/fill_bucket.json"],
}

SAMPLE_TUTORIAL_1 = {
    "task_id": "plugin.paint-pack.draw_shape",
    "version": "1.0",
    "title": "Draw a Basic Shape",
    "application": "MS Paint",
    "goal": "Draw a rectangle shape in MS Paint.",
    "steps": [
        {
            "step_id": "select_rectangle",
            "order": 1,
            "title": "Select Rectangle",
            "instruction": "Select the rectangle tool from the toolbar.",
            "expected_outcome": {
                "description": "Rectangle tool is active",
                "required_elements": ["Rectangle tool"],
            },
        }
    ],
}

SAMPLE_TUTORIAL_2 = {
    "task_id": "plugin.paint-pack.fill_bucket",
    "version": "1.0",
    "title": "Fill with Bucket",
    "application": "MS Paint",
    "goal": "Fill the canvas with paint bucket.",
    "steps": [
        {
            "step_id": "select_bucket",
            "order": 1,
            "title": "Select Bucket",
            "instruction": "Click on the paint bucket icon.",
            "expected_outcome": {
                "description": "Paint bucket tool active",
                "required_elements": ["Paint bucket tool"],
            },
        }
    ],
}


def _create_sample_plugin_dir(tmp_path: Path) -> Path:
    """Helper to populate a valid external tutorial plugin directory."""
    plugin_dir = tmp_path / "valid_plugin"
    plugin_dir.mkdir(parents=True, exist_ok=True)

    # Manifest
    manifest_file = plugin_dir / "plugin.json"
    manifest_file.write_text(json.dumps(SAMPLE_MANIFEST, indent=2), encoding="utf-8")

    # Tutorials
    tut_dir = plugin_dir / "tutorials"
    tut_dir.mkdir(parents=True, exist_ok=True)
    (tut_dir / "draw_shape.json").write_text(
        json.dumps(SAMPLE_TUTORIAL_1, indent=2), encoding="utf-8"
    )
    (tut_dir / "fill_bucket.json").write_text(
        json.dumps(SAMPLE_TUTORIAL_2, indent=2), encoding="utf-8"
    )

    # Safe documentation file
    (plugin_dir / "README.md").write_text("# MS Paint Pack Documentation", encoding="utf-8")

    return plugin_dir


# ──────────────────────────────────────────
# Valid Packaging & Verification Round-Trip
# ──────────────────────────────────────────


def test_pack_and_verify_valid_round_trip(tmp_path: Path) -> None:
    """Pack a valid plugin directory and verify the resulting .octapack archive."""
    src_dir = _create_sample_plugin_dir(tmp_path)
    output_pkg = tmp_path / "custom_pack.octapack"

    created = pack_tutorial_package(src_dir, output_path=output_pkg)
    assert created == output_pkg
    assert created.is_file()
    assert created.stat().st_size > 0

    # Verify without extracting
    result: PackageVerificationResult = verify_tutorial_package(created, raise_on_error=True)
    assert result.is_valid is True
    assert result.plugin_id == SAMPLE_PLUGIN_ID
    assert result.plugin_name == "MS Paint Tutorial Pack"
    assert result.plugin_version == "1.0.0"
    assert result.file_count == 4  # plugin.json, 2 tutorials, README.md
    assert result.total_uncompressed_bytes > 0
    assert result.total_compressed_bytes > 0
    assert len(result.sha256_digest) == 64
    assert result.sha256_digest == compute_file_sha256(created)
    assert len(result.errors) == 0

    # Check entries contain expected relative paths with forward slashes
    expected_entries = {
        "plugin.json",
        "tutorials/draw_shape.json",
        "tutorials/fill_bucket.json",
        "README.md",
    }
    assert set(result.file_entries) == expected_entries


def test_pack_default_output_path_and_overwrite(tmp_path: Path) -> None:
    """pack_tutorial_package defaults output to parent/<id>.octapack and enforces overwrite."""
    src_dir = _create_sample_plugin_dir(tmp_path)

    # Default output path
    default_pkg = pack_tutorial_package(src_dir)
    expected_default = src_dir.parent / f"{SAMPLE_PLUGIN_ID}{PACKAGE_EXTENSION}"
    assert default_pkg == expected_default
    assert default_pkg.is_file()

    # Attempting to re-pack without overwrite fails
    with pytest.raises(FileExistsError, match="Use overwrite=True"):
        pack_tutorial_package(src_dir, default_pkg, overwrite=False)

    # Re-pack with overwrite succeeds
    overwritten = pack_tutorial_package(src_dir, default_pkg, overwrite=True)
    assert overwritten == default_pkg


# ──────────────────────────────────────────
# Security & Malicious Archive Rejection
# ──────────────────────────────────────────


def test_verify_rejects_zip_slip_traversal(tmp_path: Path) -> None:
    """Archives with directory traversal ('..') entries are rejected immediately."""
    bad_pkg = tmp_path / "zip_slip.octapack"
    with zipfile.ZipFile(bad_pkg, "w") as zf:
        zf.writestr("plugin.json", json.dumps(SAMPLE_MANIFEST))
        zf.writestr("../evil_escape.json", "{}")

    res = verify_tutorial_package(bad_pkg)
    assert res.is_valid is False
    assert any("Path traversal" in err or "Relative directory escape" in err for err in res.errors)

    with pytest.raises(PackageSecurityError):
        verify_tutorial_package(bad_pkg, raise_on_error=True)


def test_verify_rejects_absolute_and_drive_paths(tmp_path: Path) -> None:
    """Archives with absolute paths or drive letters are rejected."""
    # Absolute POSIX path
    bad_posix = tmp_path / "bad_posix.octapack"
    with zipfile.ZipFile(bad_posix, "w") as zf:
        zf.writestr("/root/plugin.json", "{}")

    res = verify_tutorial_package(bad_posix)
    assert res.is_valid is False
    assert any("Absolute path" in err for err in res.errors)

    # Windows drive path
    bad_drive = tmp_path / "bad_drive.octapack"
    with zipfile.ZipFile(bad_drive, "w") as zf:
        zf.writestr("C:/escape/plugin.json", "{}")

    res2 = verify_tutorial_package(bad_drive)
    assert res2.is_valid is False
    assert any("Drive path" in err for err in res2.errors)


def test_verify_rejects_backslash_and_consecutive_separators(tmp_path: Path) -> None:
    """Archives using backslashes or consecutive slashes are rejected."""
    bad_slash = tmp_path / "backslash.octapack"
    with zipfile.ZipFile(bad_slash, "w") as zf:
        zf.writestr("plugin.json", json.dumps(SAMPLE_MANIFEST))
        zf.writestr("tutorials//escape.json", "{}")

    res = verify_tutorial_package(bad_slash)
    assert res.is_valid is False
    assert any("slash" in err.lower() for err in res.errors)


def test_verify_rejects_forbidden_extensions(tmp_path: Path) -> None:
    """Archives containing executable binaries or script files are strictly rejected."""
    for bad_ext in [".exe", ".dll", ".bat", ".ps1", ".py", ".sh", ".vbs"]:
        pkg = tmp_path / f"malicious_{bad_ext[1:]}.octapack"
        with zipfile.ZipFile(pkg, "w") as zf:
            zf.writestr("plugin.json", json.dumps(SAMPLE_MANIFEST))
            zf.writestr(f"payload{bad_ext}", b"bad_code")

        res = verify_tutorial_package(pkg)
        assert res.is_valid is False
        assert any("Forbidden file extension" in err for err in res.errors)

        with pytest.raises(PackageSecurityError):
            verify_tutorial_package(pkg, raise_on_error=True)


def test_verify_rejects_symlinks_and_executables(tmp_path: Path) -> None:
    """Archives containing symlink metadata or executable bits are rejected."""
    # Symlink entry
    symlink_pkg = tmp_path / "symlink.octapack"
    with zipfile.ZipFile(symlink_pkg, "w") as zf:
        zinfo = zipfile.ZipInfo("symlink_target.json")
        # S_IFLNK = 0o120000
        zinfo.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(zinfo, "target.json")

    res = verify_tutorial_package(symlink_pkg)
    assert res.is_valid is False
    assert any("Symlinks are forbidden" in err for err in res.errors)

    # Executable permissions (0o755)
    exec_pkg = tmp_path / "exec_bit.octapack"
    with zipfile.ZipFile(exec_pkg, "w") as zf:
        zinfo = zipfile.ZipInfo("run.json")
        zinfo.external_attr = 0o755 << 16
        zf.writestr(zinfo, "{}")

    res2 = verify_tutorial_package(exec_pkg)
    assert res2.is_valid is False
    assert any("Executable permissions are forbidden" in err for err in res2.errors)


def test_verify_rejects_case_colliding_entries(tmp_path: Path) -> None:
    """Duplicate or case-insensitive colliding entries are rejected."""
    collide_pkg = tmp_path / "collision.octapack"
    with zipfile.ZipFile(collide_pkg, "w") as zf:
        zf.writestr("plugin.json", json.dumps(SAMPLE_MANIFEST))
        zf.writestr("PLUGIN.JSON", json.dumps(SAMPLE_MANIFEST))

    res = verify_tutorial_package(collide_pkg)
    assert res.is_valid is False
    assert any("Duplicate or case-colliding" in err for err in res.errors)


# ──────────────────────────────────────────
# Quotas, Sizes, and Anti-Zip-Bomb Limits
# ──────────────────────────────────────────


def test_verify_rejects_entry_count_exceeded(tmp_path: Path) -> None:
    """Archives containing more than MAX_PACKAGE_FILE_COUNT entries are rejected."""
    huge_count_pkg = tmp_path / "many_files.octapack"
    with zipfile.ZipFile(huge_count_pkg, "w") as zf:
        zf.writestr("plugin.json", json.dumps(SAMPLE_MANIFEST))
        for i in range(MAX_PACKAGE_FILE_COUNT + 10):
            zf.writestr(f"tut_{i}.json", "{}")

    res = verify_tutorial_package(huge_count_pkg)
    assert res.is_valid is False
    assert any("exceeding maximum allowed limit" in err for err in res.errors)

    with pytest.raises(PackageQuotaError):
        verify_tutorial_package(huge_count_pkg, raise_on_error=True)


def test_verify_rejects_compression_bomb_ratio(tmp_path: Path) -> None:
    """Archives with suspicious compression ratios exceeding MAX_COMPRESSION_RATIO are rejected."""
    bomb_pkg = tmp_path / "bomb.octapack"
    with zipfile.ZipFile(bomb_pkg, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("plugin.json", json.dumps(SAMPLE_MANIFEST))
        # Large repeating text compresses to tiny byte size, exceeding 50:1 ratio
        huge_text = "0" * (1024 * 1024)  # 1 MB of zeroes compresses to ~1 KB (>1000:1 ratio)
        zf.writestr("large.txt", huge_text)

    res = verify_tutorial_package(bomb_pkg)
    assert res.is_valid is False
    assert any("Compression ratio" in err for err in res.errors)

    with pytest.raises(PackageQuotaError):
        verify_tutorial_package(bomb_pkg, raise_on_error=True)


# ──────────────────────────────────────────
# Manifest & Content Validation
# ──────────────────────────────────────────


def test_verify_rejects_missing_manifest(tmp_path: Path) -> None:
    """Archives without root plugin.json are rejected."""
    no_manifest = tmp_path / "no_manifest.octapack"
    with zipfile.ZipFile(no_manifest, "w") as zf:
        zf.writestr("other.json", "{}")

    res = verify_tutorial_package(no_manifest)
    assert res.is_valid is False
    assert any("missing mandatory root 'plugin.json'" in err for err in res.errors)


def test_verify_rejects_malformed_manifest_json(tmp_path: Path) -> None:
    """Archives with invalid JSON in plugin.json are rejected."""
    bad_manifest = tmp_path / "bad_manifest.octapack"
    with zipfile.ZipFile(bad_manifest, "w") as zf:
        zf.writestr("plugin.json", "{invalid json here")

    res = verify_tutorial_package(bad_manifest)
    assert res.is_valid is False
    assert any("contains invalid JSON" in err for err in res.errors)


def test_verify_rejects_missing_referenced_tutorial(tmp_path: Path) -> None:
    """Archives missing files listed in plugin.json tutorial_files are rejected."""
    missing_tut = tmp_path / "missing_tut.octapack"
    with zipfile.ZipFile(missing_tut, "w") as zf:
        zf.writestr("plugin.json", json.dumps(SAMPLE_MANIFEST))
        zf.writestr("tutorials/draw_shape.json", json.dumps(SAMPLE_TUTORIAL_1))
        # "tutorials/fill_bucket.json" is omitted!

    res = verify_tutorial_package(missing_tut)
    assert res.is_valid is False
    assert any("missing from the package archive" in err for err in res.errors)


def test_verify_rejects_malformed_tutorial_schema(tmp_path: Path) -> None:
    """Archives with invalid tutorial step schemas are rejected."""
    invalid_schema = tmp_path / "invalid_schema.octapack"
    with zipfile.ZipFile(invalid_schema, "w") as zf:
        zf.writestr("plugin.json", json.dumps(SAMPLE_MANIFEST))
        zf.writestr("tutorials/draw_shape.json", json.dumps(SAMPLE_TUTORIAL_1))
        # fill_bucket has missing required fields
        zf.writestr("tutorials/fill_bucket.json", json.dumps({"broken": True}))

    res = verify_tutorial_package(invalid_schema)
    assert res.is_valid is False
    assert any("schema validation failed" in err for err in res.errors)


# ──────────────────────────────────────────
# Pack Validation from Source Directory
# ──────────────────────────────────────────


def test_pack_rejects_missing_source_manifest(tmp_path: Path) -> None:
    """pack_tutorial_package rejects source directories without plugin.json."""
    empty_dir = tmp_path / "empty_plugin"
    empty_dir.mkdir()

    with pytest.raises(PackageFormatError, match="missing mandatory 'plugin.json'"):
        pack_tutorial_package(empty_dir)


def test_pack_rejects_forbidden_extension_in_source(tmp_path: Path) -> None:
    """pack_tutorial_package rejects source directories containing forbidden scripts."""
    plugin_dir = _create_sample_plugin_dir(tmp_path)
    (plugin_dir / "hook.py").write_text("print('exploit')", encoding="utf-8")

    with pytest.raises(PackageSecurityError, match="Forbidden file extension"):
        pack_tutorial_package(plugin_dir)


# ──────────────────────────────────────────
# CLI Command Execution Tests
# ──────────────────────────────────────────


def test_cli_pack_and_verify_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Test 'octa pack' and 'octa verify' CLI commands via main()."""
    src_dir = _create_sample_plugin_dir(tmp_path)
    target_octapack = tmp_path / "cli_test.octapack"

    # Run octa pack
    pack_exit = main(["pack", str(src_dir), "-o", str(target_octapack)])
    assert pack_exit == 0
    captured_pack = capsys.readouterr()
    assert "[SUCCESS] Package created successfully." in captured_pack.out
    assert target_octapack.is_file()

    # Run octa verify (standard)
    verify_exit = main(["verify", str(target_octapack)])
    assert verify_exit == 0
    captured_verify = capsys.readouterr()
    assert "[PASS] Valid OCTA tutorial package." in captured_verify.out
    assert "Plugin ID:     paint-pack" in captured_verify.out

    # Run octa verify --verbose
    verify_v_exit = main(["verify", "-v", str(target_octapack)])
    assert verify_v_exit == 0
    captured_v = capsys.readouterr()
    assert "Entries:" in captured_v.out
    assert "tutorials/draw_shape.json" in captured_v.out


def test_cli_pack_and_verify_failure_exit_codes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CLI commands return exit code 1 and descriptive stderr on invalid input."""
    # Packing non-existent directory
    pack_fail = main(["pack", str(tmp_path / "non_existent")])
    assert pack_fail == 1
    captured_fail = capsys.readouterr()
    assert "[ERROR] Packaging failed" in captured_fail.err

    # Verifying invalid package
    bad_file = tmp_path / "corrupt.octapack"
    bad_file.write_bytes(b"not a zip archive")
    verify_fail = main(["verify", str(bad_file)])
    assert verify_fail == 1
    captured_v_fail = capsys.readouterr()
    assert "[FAIL] Package validation failed" in captured_v_fail.err


# ──────────────────────────────────────────
# Phase 9.2 Cryptographic Integrity & Trust Tests
# ──────────────────────────────────────────


def test_deterministic_canonical_manifest_bytes() -> None:
    """Canonical manifest serialization is deterministic and stable for supported values."""
    # Key order independence at top and nested levels
    dict_a = {
        "version": "1.0.0",
        "name": "Alpha Pack",
        "id": "pack-a",
        "asset_digests": {"b.json": "hash-b", "a.json": "hash-a"},
    }
    dict_b = {
        "id": "pack-a",
        "asset_digests": {"a.json": "hash-a", "b.json": "hash-b"},
        "version": "1.0.0",
        "name": "Alpha Pack",
    }
    assert canonical_manifest_bytes(dict_a) == canonical_manifest_bytes(dict_b)

    # Unicode preservation: characters are encoded directly in UTF-8 without \uXXXX escaping
    unicode_manifest = {
        "id": "intl-pack",
        "name": "Café München",
        "author": "山田太郎",
    }
    unicode_bytes = canonical_manifest_bytes(unicode_manifest)
    assert b'"author":"\xe5\xb1\xb1\xe7\x94\xb0\xe5\xa4\xaa\xe9\x83\x8e"' in unicode_bytes
    assert b'"name":"Caf\xc3\xa9 M\xc3\xbcnchen"' in unicode_bytes
    assert b"\\u" not in unicode_bytes

    # Signature exclusion: presence of signature field does not affect canonical bytes
    dict_with_sig = dict(dict_a)
    dict_with_sig["signature"] = {"algorithm": "ed25519", "key_id": "test", "public_key": "abc"}
    assert canonical_manifest_bytes(dict_with_sig) == canonical_manifest_bytes(dict_a)

    # Known fixed test vector verification
    known_manifest = {
        "schema_version": "1.0",
        "id": "known-vector",
        "name": "Known Vector Tutorial",
        "version": "1.0.0",
        "author": "OCTA Team",
        "description": "Deterministic test vector",
        "tutorial_files": ["tutorials/one.json"],
        "asset_digests": {"tutorials/one.json": "0" * 64},
    }
    expected_bytes = (
        b'{"asset_digests":{"tutorials/one.json":'
        b'"0000000000000000000000000000000000000000000000000000000000000000"},'
        b'"author":"OCTA Team","description":"Deterministic test vector","id":"known-vector",'
        b'"name":"Known Vector Tutorial","schema_version":"1.0",'
        b'"tutorial_files":["tutorials/one.json"],"version":"1.0.0"}'
    )
    actual_bytes = canonical_manifest_bytes(known_manifest)
    assert actual_bytes == expected_bytes
    assert canonical_json_bytes(known_manifest) == actual_bytes
    assert compute_bytes_sha256(actual_bytes) == compute_bytes_sha256(expected_bytes)


def test_signed_package_with_pinned_key_verifies_as_trusted_signed(tmp_path: Path) -> None:
    """A package signed by a pinned key in the TrustStore classifies as TRUSTED_SIGNED."""
    src_dir = _create_sample_plugin_dir(tmp_path)
    output_pkg = tmp_path / "signed_trusted.octapack"

    private_key = ed25519.Ed25519PrivateKey.generate()
    pub_hex = private_key.public_key().public_bytes_raw().hex()

    trusted_key = TrustedKey(
        key_id="trusted-edu",
        name="Trusted Educator Keys",
        algorithm="ed25519",
        public_key_hex=pub_hex,
    )
    trust_store = TrustStore([trusted_key])

    created = pack_tutorial_package(
        src_dir,
        output_path=output_pkg,
        signing_key=private_key,
        key_id="trusted-edu",
    )
    assert created.is_file()

    result = verify_tutorial_package(created, trust_store=trust_store, raise_on_error=True)
    assert result.is_valid is True
    assert result.trust_level == PackageTrustLevel.TRUSTED_SIGNED
    assert result.is_trusted_signed is True
    assert result.is_signed_unknown_key is False
    assert result.is_unsigned_community is False
    assert result.signer_id == "trusted-edu"
    assert result.signer_name == "Trusted Educator Keys"
    assert result.asset_digests_verified >= 3


def test_signed_package_with_unknown_key_verifies_as_signed_unknown_key(tmp_path: Path) -> None:
    """A package signed by an unpinned key classifies as SIGNED_UNKNOWN_KEY."""
    src_dir = _create_sample_plugin_dir(tmp_path)
    output_pkg = tmp_path / "signed_unknown.octapack"

    private_key = ed25519.Ed25519PrivateKey.generate()
    empty_trust_store = TrustStore()

    created = pack_tutorial_package(
        src_dir,
        output_path=output_pkg,
        signing_key=private_key,
        key_id="community-author",
    )

    result = verify_tutorial_package(created, trust_store=empty_trust_store, raise_on_error=True)
    assert result.is_valid is True
    assert result.trust_level == PackageTrustLevel.SIGNED_UNKNOWN_KEY
    assert result.is_trusted_signed is False
    assert result.is_signed_unknown_key is True
    assert result.is_unsigned_community is False
    assert "Unknown Signer" in result.signer_name


def test_unsigned_package_verifies_as_unsigned_community(tmp_path: Path) -> None:
    """An unsigned package with valid asset digests classifies as UNSIGNED_COMMUNITY."""
    src_dir = _create_sample_plugin_dir(tmp_path)
    output_pkg = tmp_path / "unsigned.octapack"

    created = pack_tutorial_package(src_dir, output_path=output_pkg)
    result = verify_tutorial_package(created, raise_on_error=True)

    assert result.is_valid is True
    assert result.trust_level == PackageTrustLevel.UNSIGNED_COMMUNITY
    assert result.is_trusted_signed is False
    assert result.is_signed_unknown_key is False
    assert result.is_unsigned_community is True
    assert result.signer_id == ""
    assert result.asset_digests_verified >= 3


def test_tampered_asset_triggers_digest_mismatch(tmp_path: Path) -> None:
    """Modifying even one byte of a tutorial asset triggers digest mismatch and INVALID_TAMPERED."""
    src_dir = _create_sample_plugin_dir(tmp_path)
    pkg = pack_tutorial_package(src_dir, tmp_path / "tampered.octapack")

    # Read all files from package and tamper with one tutorial file
    tampered_pkg = tmp_path / "corrupted_asset.octapack"
    with zipfile.ZipFile(pkg, "r") as z_in, zipfile.ZipFile(tampered_pkg, "w") as z_out:
        for item in z_in.infolist():
            data = z_in.read(item.filename)
            if item.filename == "tutorials/draw_shape.json":
                # Inject a modification into the JSON payload
                tampered_data = data.replace(b"Select the rectangle tool", b"TAMPERED PAYLOAD")
                z_out.writestr(item, tampered_data)
            else:
                z_out.writestr(item, data)

    res = verify_tutorial_package(tampered_pkg)
    assert res.is_valid is False
    assert res.trust_level == PackageTrustLevel.INVALID_TAMPERED
    assert any("Asset digest mismatch" in err for err in res.errors)

    with pytest.raises(PackageIntegrityError, match="Asset digest mismatch"):
        verify_tutorial_package(tampered_pkg, raise_on_error=True)


def test_tampered_signature_bytes_rejected(tmp_path: Path) -> None:
    """Tampering with signature bytes in plugin.json triggers cryptographic failure."""
    src_dir = _create_sample_plugin_dir(tmp_path)
    private_key = ed25519.Ed25519PrivateKey.generate()
    pkg = pack_tutorial_package(
        src_dir,
        tmp_path / "signed_orig.octapack",
        signing_key=private_key,
    )

    bad_sig_pkg = tmp_path / "bad_sig.octapack"
    with zipfile.ZipFile(pkg, "r") as z_in, zipfile.ZipFile(bad_sig_pkg, "w") as z_out:
        for item in z_in.infolist():
            data = z_in.read(item.filename)
            if item.filename == "plugin.json":
                manifest_obj = json.loads(data.decode("utf-8"))
                # Flip characters in the signature hex string
                sig_hex = manifest_obj["signature"]["signature"]
                flipped = ("0" if sig_hex[0] != "0" else "1") + sig_hex[1:]
                manifest_obj["signature"]["signature"] = flipped
                z_out.writestr(item, json.dumps(manifest_obj).encode("utf-8"))
            else:
                z_out.writestr(item, data)

    res = verify_tutorial_package(bad_sig_pkg)
    assert res.is_valid is False
    assert res.trust_level == PackageTrustLevel.INVALID_TAMPERED
    assert any("signature verification failed" in err for err in res.errors)

    with pytest.raises(PackageIntegrityError, match="signature verification failed"):
        verify_tutorial_package(bad_sig_pkg, raise_on_error=True)


def test_tampered_manifest_payload_with_valid_signature_rejected(tmp_path: Path) -> None:
    """Altering manifest metadata (e.g. version) without re-signing causes signature failure."""
    src_dir = _create_sample_plugin_dir(tmp_path)
    private_key = ed25519.Ed25519PrivateKey.generate()
    pkg = pack_tutorial_package(
        src_dir,
        tmp_path / "signed_orig2.octapack",
        signing_key=private_key,
    )

    tampered_manifest_pkg = tmp_path / "tampered_meta.octapack"
    with zipfile.ZipFile(pkg, "r") as z_in, zipfile.ZipFile(tampered_manifest_pkg, "w") as z_out:
        for item in z_in.infolist():
            data = z_in.read(item.filename)
            if item.filename == "plugin.json":
                manifest_obj = json.loads(data.decode("utf-8"))
                manifest_obj["version"] = "9.9.9"  # unauthorized version bump
                z_out.writestr(item, json.dumps(manifest_obj).encode("utf-8"))
            else:
                z_out.writestr(item, data)

    res = verify_tutorial_package(tampered_manifest_pkg)
    assert res.is_valid is False
    assert res.trust_level == PackageTrustLevel.INVALID_TAMPERED
    assert any("signature verification failed" in err for err in res.errors)


def test_missing_asset_digest_rejected(tmp_path: Path) -> None:
    """Manifest with missing asset digest for a referenced tutorial is rejected."""
    bad_manifest_pkg = tmp_path / "missing_digest.octapack"
    manifest_data = dict(SAMPLE_MANIFEST)
    draw_shape_bytes = json.dumps(SAMPLE_TUTORIAL_1).encode("utf-8")
    draw_digest = compute_bytes_sha256(draw_shape_bytes)
    manifest_data["asset_digests"] = {"tutorials/draw_shape.json": draw_digest}

    with zipfile.ZipFile(bad_manifest_pkg, "w") as zf:
        zf.writestr("plugin.json", json.dumps(manifest_data))
        zf.writestr("tutorials/draw_shape.json", draw_shape_bytes)
        zf.writestr("tutorials/fill_bucket.json", json.dumps(SAMPLE_TUTORIAL_2).encode("utf-8"))

    res = verify_tutorial_package(bad_manifest_pkg)
    assert res.is_valid is False
    assert res.trust_level == PackageTrustLevel.INVALID_TAMPERED
    assert any("missing from asset_digests" in err for err in res.errors)


def test_malformed_trust_store_entries_rejected() -> None:
    """TrustedKey and TrustStore strictly validate inputs and reject malformed data."""
    # Invalid key_id
    with pytest.raises(ValueError, match="key_id"):
        TrustedKey(key_id="", name="Valid", algorithm="ed25519", public_key_hex="a" * 64)

    # Invalid algorithm
    with pytest.raises(ValueError, match="Unsupported algorithm"):
        TrustedKey(key_id="k1", name="Valid", algorithm="rsa", public_key_hex="a" * 64)

    # Invalid public key hex length
    with pytest.raises(ValueError, match="must be exactly 64 characters"):
        TrustedKey(key_id="k1", name="Valid", algorithm="ed25519", public_key_hex="abcd")

    # Non-hex characters
    with pytest.raises(ValueError, match="Invalid hexadecimal"):
        TrustedKey(key_id="k1", name="Valid", algorithm="ed25519", public_key_hex="z" * 64)

    # TrustStore from invalid data
    with pytest.raises(ValueError, match="must be a JSON object"):
        TrustStore.from_dict(["not", "dict"])  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="must be a list"):
        TrustStore.from_dict({"trusted_keys": "not_list"})


def test_trust_store_from_json_file(tmp_path: Path) -> None:
    """TrustStore loads pinned keys from a validated JSON file on disk."""
    store_file = tmp_path / "trust_store.json"
    key_hex = "a" * 64
    store_data = {
        "trusted_keys": [
            {
                "key_id": "school-district",
                "name": "School District Verified",
                "algorithm": "ed25519",
                "public_key_hex": key_hex,
                "description": "District curriculum key",
            }
        ]
    }
    store_file.write_text(json.dumps(store_data), encoding="utf-8")

    loaded_store = TrustStore.from_json_file(store_file)
    assert len(loaded_store) == 1
    assert loaded_store.is_trusted(key_hex) is True
    entry = loaded_store.get_by_public_key(key_hex)
    assert entry is not None
    assert entry.key_id == "school-district"
    assert entry.name == "School District Verified"


def test_cli_signing_and_trust_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """CLI supports --signing-key during packaging and --trust-store during verification."""
    src_dir = _create_sample_plugin_dir(tmp_path)
    target_pkg = tmp_path / "cli_signed.octapack"

    private_key = ed25519.Ed25519PrivateKey.generate()
    key_file = tmp_path / "priv.key"
    key_file.write_text(private_key.private_bytes_raw().hex(), encoding="utf-8")

    pub_hex = private_key.public_key().public_bytes_raw().hex()
    store_file = tmp_path / "trust_store.json"
    store_file.write_text(
        json.dumps(
            {
                "trusted_keys": [
                    {
                        "key_id": "octa-cli-key",
                        "name": "CLI Test Authority",
                        "algorithm": "ed25519",
                        "public_key_hex": pub_hex,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    # Pack with signing key
    pack_exit = main(
        [
            "pack",
            str(src_dir),
            "-o",
            str(target_pkg),
            "--signing-key",
            str(key_file),
            "--key-id",
            "octa-cli-key",
        ]
    )
    assert pack_exit == 0
    captured_pack = capsys.readouterr()
    assert "[SUCCESS] Package created successfully." in captured_pack.out

    # Verify with trust store (pinned key -> TRUSTED_SIGNED)
    verify_exit = main(["verify", str(target_pkg), "--trust-store", str(store_file)])
    assert verify_exit == 0
    captured_verify = capsys.readouterr()
    assert "[PASS] Valid OCTA tutorial package." in captured_verify.out
    assert "Trust Status:  TRUSTED_SIGNED" in captured_verify.out
    assert "CLI Test Authority" in captured_verify.out

    # Verify without trust store (default empty trust store -> SIGNED_UNKNOWN_KEY)
    verify_default_exit = main(["verify", str(target_pkg)])
    assert verify_default_exit == 0
    captured_default = capsys.readouterr()
    assert "[PASS] Valid OCTA tutorial package." in captured_default.out
    assert "Trust Status:  SIGNED_UNKNOWN_KEY" in captured_default.out
