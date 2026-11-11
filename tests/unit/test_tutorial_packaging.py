"""Unit tests for Phase 9.1 — Secure Tutorial Package Archive and CLI."""

from __future__ import annotations

import json
import stat
import zipfile
from pathlib import Path

import pytest

from octa.__main__ import main
from octa.tutorials.packaging import (
    MAX_PACKAGE_FILE_COUNT,
    PACKAGE_EXTENSION,
    PackageFormatError,
    PackageQuotaError,
    PackageSecurityError,
    PackageVerificationResult,
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
