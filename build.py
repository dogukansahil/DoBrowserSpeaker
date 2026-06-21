# Copyright 2026 Dogukan Sahil
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

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
