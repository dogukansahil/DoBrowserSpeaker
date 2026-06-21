# Copyright 2026 Dogukan Sahil
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Build DoBrowserSpeaker as a single Windows .exe.

Run:  python build.py
Output:  dist/DoBrowserSpeaker.exe
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = Path(__file__).parent


def ensure_pyinstaller() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("Installing PyInstaller...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])


def ensure_runtime_deps() -> None:
    req = HERE / "requirements.txt"
    if not req.exists():
        return
    missing = []
    for mod in ("aiohttp", "pyaudiowpatch", "qrcode", "PIL"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        print(f"Installing runtime dependencies ({', '.join(missing)})...")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-r", str(req)]
        )


def run_pyinstaller() -> None:
    print("Running PyInstaller (this takes 1-3 minutes)...")
    # Remove only the Windows exe so Linux .deb in dist/ is preserved.
    old_exe = HERE / "dist" / "DoBrowserSpeaker.exe"
    if old_exe.exists():
        old_exe.unlink()
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name", "DoBrowserSpeaker",
        "--icon", str(HERE / "static" / "icon.ico"),
        "--add-data", f"{HERE / 'static'}{';'}static",
        "--collect-all", "pyaudiowpatch",
        "--hidden-import", "PIL._tkinter_finder",
        str(HERE / "server.py"),
    ]
    subprocess.check_call(args, cwd=HERE)


def cleanup() -> None:
    for d in ("build", "DoBrowserSpeaker.spec"):
        p = HERE / d
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif p.exists():
            p.unlink(missing_ok=True)


def main() -> None:
    ensure_pyinstaller()
    ensure_runtime_deps()
    run_pyinstaller()
    cleanup()
    exe = HERE / "dist" / "DoBrowserSpeaker.exe"
    if exe.exists():
        size_mb = exe.stat().st_size / (1024 * 1024)
        print(f"\nDone: {exe}  ({size_mb:.1f} MB)")
    else:
        print("\nBuild finished but exe not found — check PyInstaller output above.")


if __name__ == "__main__":
    main()
