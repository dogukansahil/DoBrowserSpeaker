#!/usr/bin/env python3
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

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DIST = HERE / "dist"
STATIC = HERE / "static"


def ensure_system_tkinter() -> None:
    try:
        import tkinter  # noqa: F401
        return
    except ImportError:
        pass

    print("tkinter not found — installing python3-tk...")
    for cmd in (
        ["pkexec", "apt-get", "install", "-y", "python3-tk"],
        ["sudo", "apt-get", "install", "-y", "python3-tk"],
    ):
        try:
            subprocess.check_call(cmd)
            print("python3-tk installed successfully.")
            return
        except FileNotFoundError:
            continue
        except subprocess.CalledProcessError:
            continue

    sys.exit(
        "\nERROR: Could not install python3-tk automatically.\n"
        "Run:  sudo apt-get install python3-tk\n"
        "Then re-run this script."
    )


def ensure_pyinstaller() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("Installing PyInstaller...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])


def ensure_runtime_deps() -> None:
    required = ["aiohttp", "sounddevice", "pulsectl", "qrcode[pil]", "Pillow"]
    missing: list[str] = []
    for pkg in ["aiohttp", "sounddevice", "pulsectl", "qrcode", "PIL"]:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"Installing runtime dependencies: {', '.join(missing)}")
        subprocess.check_call([sys.executable, "-m", "pip", "install"] + required)


def run_pyinstaller() -> Path:
    print("Running PyInstaller for Linux build...")
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True, exist_ok=True)
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name", "BrowserSpeaker",
        "--add-data", f"{STATIC}{os.pathsep}static",
        "--hidden-import", "PIL._tkinter_finder",
        "--hidden-import", "tkinter",
        "--hidden-import", "tkinter.ttk",
        "--collect-all", "tkinter",
        str(HERE / "server.py"),
    ]
    subprocess.check_call(args, cwd=HERE)
    exe = DIST / "BrowserSpeaker"
    if not exe.exists():
        raise FileNotFoundError("PyInstaller did not produce dist/BrowserSpeaker")
    return exe


def write_desktop_file(path: Path, icon_name: str = "browserspeaker", version: str = "1.0") -> None:
    path.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name=BrowserSpeaker {version}\n"
        "Comment=Turn any browser into a wireless speaker\n"
        f"Exec=/usr/bin/{icon_name}\n"
        f"Icon={icon_name}\n"
        "Terminal=false\n"
        "StartupNotify=true\n"
        "StartupWMClass=BrowserSpeaker\n"
        "Categories=Audio;Network;\n"
    )


def convert_icon(output_path: Path) -> None:
    try:
        from PIL import Image
    except ImportError:
        return
    ico = STATIC / "icon.ico"
    if not ico.exists():
        return
    try:
        with Image.open(ico) as im:
            png = im.convert("RGBA")
            png.save(output_path)
    except Exception:
        pass


def build_deb(binary_path: Path) -> Path:
    version = "1.0"
    arch = subprocess.check_output(["dpkg", "--print-architecture"], text=True).strip()
    pkgroot = HERE / "pkgroot"
    if pkgroot.exists():
        shutil.rmtree(pkgroot)
    (pkgroot / "DEBIAN").mkdir(parents=True)
    (pkgroot / "usr" / "bin").mkdir(parents=True)
    (pkgroot / "usr" / "share" / "applications").mkdir(parents=True)
    (pkgroot / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps").mkdir(parents=True)
    (pkgroot / "usr" / "share" / "doc" / "browserspeaker").mkdir(parents=True)

    shutil.copy2(binary_path, pkgroot / "usr" / "bin" / "browserspeaker")
    (pkgroot / "usr" / "bin" / "browserspeaker").chmod(0o755)

    for doc_file in ("LICENSE", "NOTICE.md"):
        src = HERE / doc_file
        if src.exists():
            shutil.copy2(src, pkgroot / "usr" / "share" / "doc" / "browserspeaker" / doc_file)

    write_desktop_file(pkgroot / "usr" / "share" / "applications" / "browserspeaker.desktop", version=version)
    convert_icon(pkgroot / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps" / "browserspeaker.png")

    control = (
        f"Package: browserspeaker\n"
        f"Version: {version}\n"
        f"Section: sound\n"
        f"Priority: optional\n"
        f"Architecture: {arch}\n"
        f"Maintainer: BrowserSpeaker <noreply@localhost>\n"
        f"Description: BrowserSpeaker lets browsers act as wireless speakers.\n"
    )
    (pkgroot / "DEBIAN" / "control").write_text(control)

    deb_path = DIST / f"browserspeaker_{version}_{arch}.deb"
    subprocess.check_call(["dpkg-deb", "--build", str(pkgroot), str(deb_path)])
    return deb_path


def build_appimage(binary_path: Path) -> Path:
    appimagetool = shutil.which("appimagetool")
    if appimagetool is None:
        raise RuntimeError("appimagetool not found on PATH")

    appdir = HERE / "AppDir"
    if appdir.exists():
        shutil.rmtree(appdir)
    (appdir / "usr" / "bin").mkdir(parents=True)
    (appdir / "usr" / "share" / "applications").mkdir(parents=True)
    (appdir / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps").mkdir(parents=True)

    shutil.copy2(binary_path, appdir / "usr" / "bin" / "BrowserSpeaker")
    (appdir / "usr" / "bin" / "BrowserSpeaker").chmod(0o755)
    write_desktop_file(appdir / "usr" / "share" / "applications" / "browserspeaker.desktop", icon_name="browserspeaker", version="1.0")
    convert_icon(appdir / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps" / "browserspeaker.png")

    appdir.joinpath("AppRun").write_text(
        "#!/bin/sh\n"
        "HERE=$(dirname \"$(readlink -f \"$0\")\")\n"
        "exec \"$HERE/usr/bin/BrowserSpeaker\" \"$@\"\n"
    )
    (appdir / "AppRun").chmod(0o755)

    output = DIST / "BrowserSpeaker.AppImage"
    subprocess.check_call([appimagetool, str(appdir), str(output)])
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Linux BrowserSpeaker packages.")
    parser.add_argument("--deb", action="store_true", help="Build a Debian package")
    parser.add_argument("--appimage", action="store_true", help="Build an AppImage")
    parser.add_argument("--no-deps", action="store_true", help="Do not auto-install Python dependencies")
    args = parser.parse_args()

    if not args.no_deps:
        ensure_system_tkinter()
        ensure_pyinstaller()
        ensure_runtime_deps()

    binary = run_pyinstaller()
    print(f"Built binary: {binary}")

    if args.deb:
        deb = build_deb(binary)
        print(f"Built Debian package: {deb}")

    if args.appimage:
        appimage = build_appimage(binary)
        print(f"Built AppImage: {appimage}")


if __name__ == "__main__":
    main()
