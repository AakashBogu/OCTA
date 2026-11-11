#!/usr/bin/env python3
"""Build and validation script for OCTA Windows distribution.

Automates:
1. Environment pre-flight verification.
2. Reproducible PyInstaller packaging using `octa.spec`.
3. Secret-leak audit (guarantees .env, secrets, logs, user data are excluded).
4. Asset integrity verification (Godot project, Qt platform plugins, DLLs).
5. Smoke testing of built standalone executables (`OCTA.exe` and `octa-cli.exe`).
6. Optional portable ZIP distribution archive creation.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path


def get_repo_root() -> Path:
    """Return repository root directory, validating critical anchor files."""
    root = Path.cwd().resolve()
    if not (root / "pyproject.toml").is_file() or not (root / "src" / "octa").is_dir():
        raise RuntimeError(
            f"Build script must be executed from OCTA repository root. Current directory: {root}"
        )
    return root


def clean_build_artifacts(root: Path) -> None:
    """Remove previous build and dist directories."""
    print("[-] Cleaning previous build artifacts...")
    for dir_name in ("build", "dist"):
        target = root / dir_name
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
            print(f"    Removed {target}")


def verify_secret_hygiene(dist_dir: Path) -> list[str]:
    """Audit the output distribution directory to ensure no secret or user data leaked.

    Returns a list of forbidden files found (empty if clean).
    """
    forbidden_patterns = (
        ".env",
        ".env.*",
        "*.key",
        "*.pem",
        "*.pfx",
        "tutorial_feedback.json",
        "tutorial_session_metrics.json",
        "*.log",
    )
    violations: list[str] = []
    for pattern in forbidden_patterns:
        for match in dist_dir.rglob(pattern):
            if match.is_file():
                violations.append(str(match.relative_to(dist_dir)))
    return violations


def audit_distribution_assets(dist_app_dir: Path) -> list[str]:
    """Verify that required executables, Qt plugins, and assets exist in the bundle."""
    errors: list[str] = []

    # 1. Executables
    exe_gui = dist_app_dir / "OCTA.exe"
    exe_cli = dist_app_dir / "octa-cli.exe"
    if not exe_gui.is_file() or exe_gui.stat().st_size == 0:
        errors.append(f"Missing or empty GUI executable: {exe_gui}")
    if not exe_cli.is_file() or exe_cli.stat().st_size == 0:
        errors.append(f"Missing or empty CLI executable: {exe_cli}")

    # 2. Qt Platform Plugin (required for GUI on Windows)
    # PyInstaller may place it under PySide6/plugins/platforms/ or platforms/
    candidates = [
        dist_app_dir / "PySide6" / "plugins" / "platforms" / "qwindows.dll",
        dist_app_dir / "platforms" / "qwindows.dll",
        dist_app_dir / "_internal" / "PySide6" / "plugins" / "platforms" / "qwindows.dll",
    ]
    if not any(c.is_file() for c in candidates):
        errors.append("Missing required Qt Windows platform plugin (qwindows.dll).")

    # 3. Godot Companion Assets
    godot_candidates = [
        dist_app_dir / "godot",
        dist_app_dir / "_internal" / "godot",
    ]
    godot_dir = next((d for d in godot_candidates if (d / "project.godot").is_file()), None)
    if godot_dir is None:
        errors.append("Missing bundled Godot project file (project.godot).")
    else:
        if not (godot_dir / "scenes").is_dir():
            errors.append(f"Missing bundled Godot scenes directory in {godot_dir}")
        if not (godot_dir / "scripts").is_dir():
            errors.append(f"Missing bundled Godot scripts directory in {godot_dir}")

    return errors


def run_smoke_tests(dist_app_dir: Path) -> bool:
    """Execute smoke tests against the packaged executables to verify runtime functionality."""
    print("[-] Running smoke tests on packaged executables...")

    exe_cli = dist_app_dir / "octa-cli.exe"
    exe_gui = dist_app_dir / "OCTA.exe"

    # 1. Test CLI --version
    print(f"    Testing {exe_cli.name} --version ...")
    proc_version = subprocess.run(
        [str(exe_cli), "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc_version.returncode != 0:
        print(f"    [FAIL] --version returned non-zero code {proc_version.returncode}")
        print(f"    Stdout: {proc_version.stdout}")
        print(f"    Stderr: {proc_version.stderr}")
        return False
    version_text = (proc_version.stdout + " " + proc_version.stderr).lower()
    if "octa 0.1.0" not in version_text:
        print(f"    [FAIL] Unexpected version output: {proc_version.stdout or proc_version.stderr}")
        return False
    print(f"    [OK] Version check passed: {(proc_version.stdout or proc_version.stderr).strip()}")

    # 2. Test CLI --help
    print(f"    Testing {exe_cli.name} --help ...")
    proc_help = subprocess.run(
        [str(exe_cli), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc_help.returncode != 0:
        print(f"    [FAIL] --help returned non-zero code {proc_help.returncode}")
        return False
    if "OCTA" not in proc_help.stdout:
        print(f"    [FAIL] Unexpected help output: {proc_help.stdout}")
        return False
    print("    [OK] CLI help check passed.")

    # 3. Test GUI Startup & Process Termination
    print(f"    Testing {exe_gui.name} launch & clean shutdown (--no-auto-start) ...")
    proc_gui = subprocess.Popen(
        [str(exe_gui), "--no-auto-start"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # Allow process to initialize Qt application, tray, and event loop
    time.sleep(2.0)
    poll_code = proc_gui.poll()
    if poll_code is not None and poll_code != 0:
        _, stderr = proc_gui.communicate()
        print(f"    [FAIL] GUI exited prematurely with code {poll_code}")
        print(f"    Stderr: {stderr.decode('utf-8', errors='replace')}")
        return False

    # Terminate the running GUI test instance cleanly
    proc_gui.terminate()
    try:
        proc_gui.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc_gui.kill()
        proc_gui.wait()
    print("    [OK] GUI startup and termination smoke test passed cleanly.")
    return True


def create_portable_zip(dist_app_dir: Path, output_zip: Path) -> None:
    """Pack the distribution directory into a standalone portable ZIP archive."""
    print(f"[-] Packaging portable ZIP archive: {output_zip} ...")
    with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in dist_app_dir.rglob("*"):
            if file_path.is_file():
                arcname = file_path.relative_to(dist_app_dir.parent)
                zf.write(file_path, arcname)
    size_mb = output_zip.stat().st_size / (1024 * 1024)
    print(f"    [OK] Portable ZIP created ({size_mb:.1f} MB): {output_zip}")


def main() -> int:
    parser = argparse.ArgumentParser(description="OCTA Windows Distribution Packager")
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Remove build/ and dist/ directories before packaging.",
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="Skip PyInstaller build and run validation/packaging on existing dist/.",
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        default=True,
        help="Run runtime smoke tests on built executables (default: True).",
    )
    parser.add_argument(
        "--no-smoke-test",
        action="store_false",
        dest="smoke_test",
        help="Disable runtime smoke tests.",
    )
    parser.add_argument(
        "--zip",
        action="store_true",
        help="Create standalone portable ZIP archive in dist/.",
    )
    args = parser.parse_args()

    root = get_repo_root()
    spec_file = root / "octa.spec"
    dist_app_dir = root / "dist" / "OCTA"

    if not spec_file.is_file():
        print(f"[ERROR] Specification file not found: {spec_file}", file=sys.stderr)
        return 1

    if args.clean:
        clean_build_artifacts(root)

    if not args.skip_build:
        print("[-] Running PyInstaller build...")
        pyinstaller_cmd = [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--log-level=WARN",
            str(spec_file),
        ]
        proc = subprocess.run(pyinstaller_cmd, cwd=root, check=False)
        if proc.returncode != 0:
            print(f"[ERROR] PyInstaller failed with exit code {proc.returncode}", file=sys.stderr)
            return proc.returncode

    if not dist_app_dir.is_dir():
        print(
            f"[ERROR] Output distribution directory does not exist: {dist_app_dir}",
            file=sys.stderr,
        )
        return 1

    # Ensure top-level godot directory exists for direct accessibility
    top_godot = dist_app_dir / "godot"
    internal_godot = dist_app_dir / "_internal" / "godot"
    if not top_godot.exists() and internal_godot.exists():
        shutil.copytree(internal_godot, top_godot)

    # Audit assets
    print("[-] Auditing packaged distribution assets...")
    asset_errors = audit_distribution_assets(dist_app_dir)
    if asset_errors:
        print("[ERROR] Asset audit failed:", file=sys.stderr)
        for err in asset_errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    print("    [OK] All required binaries, Qt plugins, and Godot assets are present.")

    # Audit secret hygiene
    print("[-] Checking secret and credential hygiene...")
    leaked_secrets = verify_secret_hygiene(dist_app_dir)
    if leaked_secrets:
        print("[ERROR] Sensitive files detected in package:", file=sys.stderr)
        for leak in leaked_secrets:
            print(f"  - {leak}", file=sys.stderr)
        return 1
    print("    [OK] Secret hygiene audit passed (zero credentials, logs, or .env files found).")

    # Smoke testing
    if args.smoke_test:
        if not run_smoke_tests(dist_app_dir):
            print("[ERROR] Packaging smoke tests failed.", file=sys.stderr)
            return 1

    # Optional ZIP creation
    if args.zip:
        output_zip = root / "dist" / "OCTA-v0.1.0-windows-x64.zip"
        create_portable_zip(dist_app_dir, output_zip)

    print("\n[SUCCESS] OCTA Windows distribution packaging completed successfully.")
    print(f"Standalone application folder: {dist_app_dir}")
    print(f"Primary GUI executable:       {dist_app_dir / 'OCTA.exe'}")
    print(f"CLI / automation executable:   {dist_app_dir / 'octa-cli.exe'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
