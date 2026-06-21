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

import asyncio
import json
import math
import re
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

from aiohttp import web, WSMsgType
import qrcode
from PIL import ImageTk
import tkinter as tk
from tkinter import ttk

IS_WINDOWS = sys.platform.startswith("win")
IS_LINUX = sys.platform.startswith("linux")

if IS_WINDOWS:
    import winreg
    import pyaudiowpatch as pyaudio
else:
    import sounddevice as sd
    try:
        import pulsectl
    except ImportError:
        pulsectl = None

def _resource_root() -> Path:
    """Folder containing bundled resources (static/, etc.)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).parent


HERE = Path(__file__).parent if not getattr(sys, "frozen", False) else Path(sys.executable).parent
STATIC = _resource_root() / "static"
PORT = 8765
CHUNK_MS = 20


# ---------- Network helpers ----------

def get_default_route_ip() -> str | None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return None
    finally:
        s.close()


def list_interfaces() -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    if IS_WINDOWS:
        try:
            raw = subprocess.run(
                ["ipconfig"], capture_output=True,
                creationflags=0x08000000, timeout=5,
            ).stdout
            text = raw.decode("utf-8", errors="replace")
            if "adapter" not in text.lower():
                text = raw.decode("cp857", errors="replace")

            current = None
            for line in text.splitlines():
                stripped = line.strip()
                m = re.match(r"^(.+?adapter\s+.+?):\s*$", line, re.IGNORECASE)
                if m:
                    name = m.group(1).strip()
                    name = re.sub(
                        r"^(Ethernet|Wireless LAN|Unknown|PPP|Tunnel)\s+adapter\s+",
                        lambda x: {
                            "ethernet": "Ethernet: ",
                            "wireless lan": "Wi-Fi: ",
                            "unknown": "Other: ",
                            "ppp": "PPP: ",
                            "tunnel": "Tunnel: ",
                        }.get(x.group(1).lower(), x.group(0)),
                        name, flags=re.IGNORECASE,
                    )
                    current = name
                    continue
                m = re.search(r"IPv4[^:]*:\s*([\d\.]+)", stripped)
                if m and current:
                    ip = m.group(1)
                    if ip.startswith("169.254") or ip == "0.0.0.0":
                        continue
                    key = (current, ip)
                    if key not in seen:
                        seen.add(key)
                        results.append(key)
        except Exception:
            pass
    else:
        try:
            raw = subprocess.run(
                ["ip", "-4", "addr", "show"], capture_output=True,
                text=True, timeout=5,
            ).stdout
            current = None
            for line in raw.splitlines():
                m = re.match(r"^\d+:\s+([^:]+):", line)
                if m:
                    current = m.group(1)
                    continue
                m = re.search(r"inet\s+([\d\.]+)/", line)
                if m and current:
                    ip = m.group(1)
                    if ip.startswith("127.") or ip.startswith("169.254"):
                        continue
                    name = current
                    key = (name, ip)
                    if key not in seen:
                        seen.add(key)
                        results.append(key)
        except Exception:
            pass

    if not results:
        try:
            for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
                if ip.startswith("127.") or ip.startswith("169.254"):
                    continue
                key = (f"Interface {ip}", ip)
                if key not in seen:
                    seen.add(key)
                    results.append(key)
        except Exception:
            pass

    default = get_default_route_ip()
    if default:
        for i, (n, ip) in enumerate(results):
            if ip == default:
                results.insert(0, results.pop(i))
                break
        else:
            results.insert(0, (f"Default route ({default})", default))

    if not results:
        results.append(("Loopback", "127.0.0.1"))
    return results


# ---------- Audio broadcaster ----------

class AudioBroadcaster:
    def __init__(self):
        self.clients: set[web.WebSocketResponse] = set()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.pa = None
        self.stream = None
        self._pw_proc = None
        self.device_info: dict | None = None
        self.sample_rate = 48000
        self.channels = 2
        self.error: str | None = None

        self.last_rms = 0.0
        self.last_callback_ts = 0.0
        self.last_broadcast_ts = 0.0
        self.total_chunks = 0
        self.test_tone_active = False

    # ---- PipeWire helpers ----
    def _has_pipewire(self) -> bool:
        return shutil.which("pw-record") is not None

    def _list_pipewire_sinks(self) -> list[dict]:
        devices = []
        try:
            raw = subprocess.run(
                ["pw-dump"], capture_output=True, text=True, timeout=5
            ).stdout
            data = json.loads(raw)
            for node in data:
                if "Node" not in node.get("type", ""):
                    continue
                props = node.get("info", {}).get("props", {})
                if props.get("media.class") != "Audio/Sink":
                    continue
                name = props.get("node.name", "")
                desc = props.get("node.description", name)
                if not name:
                    continue
                devices.append({
                    "index": name,
                    "name": desc,
                    "channels": 2,
                    "rate": 48000,
                    "backend": "pipewire",
                })
        except Exception:
            pass
        return devices

    def _open_pipewire_stream(self, target: str):
        self.sample_rate = 48000
        self.channels = 2
        self.device_info = {"name": target}
        chunk_frames = int(self.sample_rate * CHUNK_MS / 1000)
        chunk_bytes = chunk_frames * self.channels * 4

        try:
            proc = subprocess.Popen(
                [
                    "pw-record",
                    "--target", target,
                    "--format", "f32",
                    "--rate", str(self.sample_rate),
                    "--channels", str(self.channels),
                    "--latency", f"{CHUNK_MS}ms",
                    "-",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            self.error = f"Failed to start pw-record: {e}"
            return

        self._pw_proc = proc
        self.error = None

        def _read_loop():
            while True:
                data = proc.stdout.read(chunk_bytes)
                if not data:
                    break
                self._callback(data, len(data) // (self.channels * 4), None, None)

        threading.Thread(target=_read_loop, daemon=True).start()

    # ---- Device enumeration ----
    def list_loopback_devices(self) -> list[dict]:
        devices = []
        if IS_WINDOWS:
            pa = self.pa or pyaudio.PyAudio()
            try:
                for d in pa.get_loopback_device_info_generator():
                    devices.append({
                        "index": int(d["index"]),
                        "name": str(d["name"]),
                        "channels": int(d["maxInputChannels"]) or 2,
                        "rate": int(d["defaultSampleRate"]),
                    })
            except Exception:
                pass
            if self.pa is None:
                pa.terminate()

            try:
                default = pyaudio.PyAudio().get_default_wasapi_loopback()
                default_idx = int(default["index"])
                for i, dev in enumerate(devices):
                    if dev["index"] == default_idx:
                        dev["default"] = True
                        devices.insert(0, devices.pop(i))
                        break
            except Exception:
                pass
            return devices

        if self._has_pipewire():
            pw_devices = self._list_pipewire_sinks()
            if pw_devices:
                pw_devices[0]["default"] = True
                return pw_devices

        try:
            query = sd.query_devices()
            for idx, d in enumerate(query):
                if d["max_input_channels"] <= 0:
                    continue
                name = str(d["name"])
                if "monitor" not in name.lower() and "loopback" not in name.lower():
                    continue
                devices.append({
                    "index": idx,
                    "name": name,
                    "channels": int(d["max_input_channels"]) or 2,
                    "rate": int(d["default_samplerate"] or 48000),
                    "backend": "sounddevice",
                })
        except Exception:
            pass

        if not devices:
            try:
                query = sd.query_devices()
                for idx, d in enumerate(query):
                    if d["max_input_channels"] <= 0:
                        continue
                    devices.append({
                        "index": idx,
                        "name": str(d["name"]),
                        "channels": int(d["max_input_channels"]) or 2,
                        "rate": int(d["default_samplerate"] or 48000),
                        "backend": "sounddevice",
                    })
            except Exception:
                pass

        if devices and pulsectl is not None:
            try:
                with pulsectl.Pulse("DoBrowserSpeaker") as pulse:
                    default_sink = pulse.server_info().default_sink_name
                    if default_sink:
                        monitor_name = f"{default_sink}.monitor"
                        for i, dev in enumerate(devices):
                            if monitor_name.lower() in dev["name"].lower():
                                dev["default"] = True
                                devices.insert(0, devices.pop(i))
                                break
            except Exception:
                pass

        return devices

    def default_device_index(self):
        if IS_WINDOWS:
            try:
                pa = pyaudio.PyAudio()
                d = pa.get_default_wasapi_loopback()
                pa.terminate()
                return int(d["index"])
            except Exception:
                pass

        for d in self.list_loopback_devices():
            return d["index"]
        return None

    # ---- Stream lifecycle ----
    def _open_stream(self, device_index):
        if self._pw_proc is not None:
            try:
                self._pw_proc.terminate()
            except Exception:
                pass
            self._pw_proc = None

        if self.stream is not None:
            try:
                if IS_WINDOWS:
                    self.stream.stop_stream()
                self.stream.close()
            except Exception:
                pass
            self.stream = None

        if IS_LINUX and isinstance(device_index, str):
            self._open_pipewire_stream(device_index)
            return

        if IS_WINDOWS:
            dev = self.pa.get_device_info_by_index(device_index)
            self.device_info = dev
            self.sample_rate = int(dev["defaultSampleRate"])
            self.channels = int(dev["maxInputChannels"]) or 2
            chunk_frames = int(self.sample_rate * (CHUNK_MS / 1000))

            try:
                self.stream = self.pa.open(
                    format=pyaudio.paFloat32,
                    channels=self.channels,
                    rate=self.sample_rate,
                    input=True,
                    input_device_index=device_index,
                    frames_per_buffer=chunk_frames,
                    stream_callback=self._callback_pyaudio,
                )
                self.stream.start_stream()
                self.error = None
            except Exception as e:
                self.error = f"Failed to open audio stream: {e}"
            return

        try:
            dev = sd.query_devices(device_index, kind="input")
            self.device_info = dev
            self.sample_rate = int(dev["default_samplerate"] or 48000)
            self.channels = int(dev["max_input_channels"] or 2)
            self.stream = sd.InputStream(
                device=device_index,
                channels=self.channels,
                samplerate=self.sample_rate,
                dtype="float32",
                callback=self._callback_linux,
                latency="low",
            )
            self.stream.start()
            self.error = None
        except Exception as e:
            self.error = f"Failed to open audio stream: {e}"

    def _callback_linux(self, indata, frames, time_info, status):
        if status:
            pass
        self._callback(indata.tobytes(), frames, time_info, status)
        return None

    def start(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop
        if IS_WINDOWS:
            self.pa = pyaudio.PyAudio()
        idx = self.default_device_index()
        if idx is None:
            self.error = "No loopback audio device found."
            return
        self._open_stream(idx)

    def switch_device(self, device_index):
        if self.loop is None:
            return
        # Kick existing clients so they reconnect with new sample rate / channels.
        for ws in list(self.clients):
            try:
                asyncio.run_coroutine_threadsafe(ws.close(), self.loop)
            except Exception:
                pass
        self.clients.clear()
        self._open_stream(device_index)

    def stop(self):
        if self._pw_proc is not None:
            try:
                self._pw_proc.terminate()
            except Exception:
                pass
            self._pw_proc = None
        if self.stream is not None:
            try:
                if IS_WINDOWS:
                    self.stream.stop_stream()
                self.stream.close()
            except Exception:
                pass
            self.stream = None
        if IS_WINDOWS and self.pa is not None:
            try:
                self.pa.terminate()
            except Exception:
                pass
            self.pa = None

    # ---- Audio callback ----
    def _callback(self, in_data, frame_count, time_info, status):
        self.last_callback_ts = time.time()
        self.total_chunks += 1
        if not self.test_tone_active:
            self.last_rms = _pcm_rms_float32(in_data)
            if self.loop and self.clients:
                try:
                    asyncio.run_coroutine_threadsafe(
                        self._broadcast(in_data), self.loop
                    )
                except Exception:
                    pass
        return None

    def _callback_pyaudio(self, in_data, frame_count, time_info, status):
        self._callback(in_data, frame_count, time_info, status)
        return (None, pyaudio.paContinue)

    async def _broadcast(self, data: bytes):
        self.last_broadcast_ts = time.time()
        dead = []
        for ws in list(self.clients):
            try:
                if ws.closed:
                    dead.append(ws)
                    continue
                await ws.send_bytes(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    async def keepalive_loop(self):
        """Send silence if no real audio chunk has been broadcast in 100ms.
        Keeps mobile MediaStream alive so the lock-screen session does not die."""
        while True:
            await asyncio.sleep(0.05)
            if not self.clients or self.test_tone_active:
                continue
            if time.time() - self.last_broadcast_ts < 0.1:
                continue
            frames = max(1, int(self.sample_rate * (CHUNK_MS / 1000)))
            silence = b"\x00" * (frames * self.channels * 4)
            await self._broadcast(silence)

    # ---- Test tone ----
    async def play_test_tone(self, seconds: float = 1.2, freq: float = 440.0):
        """Generate a sine tone and broadcast it as if it came from the device."""
        if not self.clients:
            return
        self.test_tone_active = True
        try:
            sr = self.sample_rate
            ch = self.channels
            chunk_frames = max(1, int(sr * (CHUNK_MS / 1000)))
            total_frames = int(sr * seconds)
            written = 0
            phase = 0.0
            two_pi_f_over_sr = 2.0 * math.pi * freq / sr
            fade = max(1, int(sr * 0.02))
            while written < total_frames:
                n = min(chunk_frames, total_frames - written)
                samples = []
                for i in range(n):
                    idx = written + i
                    env = 1.0
                    if idx < fade:
                        env = idx / fade
                    elif idx > total_frames - fade:
                        env = max(0.0, (total_frames - idx) / fade)
                    v = math.sin(phase) * 0.25 * env
                    phase += two_pi_f_over_sr
                    for _ in range(ch):
                        samples.append(v)
                data = struct.pack(f"<{len(samples)}f", *samples)
                self.last_rms = _pcm_rms_float32(data)
                await self._broadcast(data)
                written += n
                await asyncio.sleep(CHUNK_MS / 1000.0)
        finally:
            self.test_tone_active = False


def _pcm_rms_float32(data: bytes) -> float:
    """Return RMS amplitude (0..1) of interleaved float32 PCM."""
    if not data:
        return 0.0
    n = len(data) // 4
    if n == 0:
        return 0.0
    fmt = f"<{n}f"
    try:
        vals = struct.unpack(fmt, data)
    except struct.error:
        return 0.0
    s = 0.0
    for v in vals:
        s += v * v
    return math.sqrt(s / n)


# ---------- HTTP/WS ----------

async def index_handler(request: web.Request):
    return web.FileResponse(STATIC / "index.html")


async def ws_handler(request: web.Request):
    bc: AudioBroadcaster = request.app["bc"]
    ws = web.WebSocketResponse(max_msg_size=0, heartbeat=20)
    await ws.prepare(request)
    await ws.send_json({
        "sampleRate": bc.sample_rate,
        "channels": bc.channels,
        "format": "float32",
    })
    bc.clients.add(ws)
    try:
        async for msg in ws:
            if msg.type in (WSMsgType.CLOSED, WSMsgType.ERROR):
                break
    finally:
        bc.clients.discard(ws)
    return ws


async def run_server_async(bc: AudioBroadcaster, ready_evt: threading.Event):
    app = web.Application()
    app["bc"] = bc
    app.router.add_get("/", index_handler)
    app.router.add_get("/ws", ws_handler)
    app.router.add_static("/static/", STATIC)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    bc.start(asyncio.get_running_loop())
    ready_evt.set()
    asyncio.create_task(bc.keepalive_loop())
    while True:
        await asyncio.sleep(3600)


def run_server_thread(bc: AudioBroadcaster, ready_evt: threading.Event):
    try:
        asyncio.run(run_server_async(bc, ready_evt))
    except Exception as e:
        bc.error = f"Server error: {e}"
        ready_evt.set()


# ---------- GUI ----------

# ---------- Autostart helpers ----------

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_VALUE = "DoBrowserSpeaker"
_LINUX_AUTOSTART_DIR = Path.home() / ".config" / "autostart"
_LINUX_AUTOSTART_FILE = _LINUX_AUTOSTART_DIR / "dobrowserspeaker.desktop"


def _autostart_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    return f'"{Path(sys.executable)}" "{HERE / "server.py"}"'


def autostart_enabled() -> bool:
    if IS_WINDOWS:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
                winreg.QueryValueEx(k, _RUN_VALUE)
            return True
        except FileNotFoundError:
            return False
        except OSError:
            return False

    if IS_LINUX:
        return _LINUX_AUTOSTART_FILE.exists()

    return False


def set_autostart(enable: bool) -> None:
    if IS_WINDOWS:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                                winreg.KEY_SET_VALUE) as k:
                if enable:
                    winreg.SetValueEx(k, _RUN_VALUE, 0, winreg.REG_SZ,
                                      _autostart_command())
                else:
                    try:
                        winreg.DeleteValue(k, _RUN_VALUE)
                    except FileNotFoundError:
                        pass
        except OSError:
            pass
        return

    if IS_LINUX:
        try:
            _LINUX_AUTOSTART_DIR.mkdir(parents=True, exist_ok=True)
            if enable:
                _LINUX_AUTOSTART_FILE.write_text(
                    "[Desktop Entry]\n"
                    "Type=Application\n"
                    "Name=DoBrowserSpeaker\n"
                    "Exec=" + _autostart_command() + "\n"
                    "Terminal=false\n"
                    "X-GNOME-Autostart-enabled=true\n"
                    "StartupWMClass=DoBrowserSpeaker\n"
                    "NoDisplay=true\n"
                )
            else:
                if _LINUX_AUTOSTART_FILE.exists():
                    _LINUX_AUTOSTART_FILE.unlink()
        except Exception:
            pass
        return


def _apply_app_id(root: tk.Tk) -> None:
    if not IS_WINDOWS:
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "DoBrowserSpeaker.dogukansahil.v01"
        )
    except Exception:
        pass


def _disable_maximize(root: tk.Tk) -> None:
    """Windows: title bar'dan maximize butonunu kaldir, minimize + kapat kalsin."""
    try:
        import ctypes
        GWL_STYLE = -16
        WS_MAXIMIZEBOX = 0x00010000
        user32 = ctypes.windll.user32
        hwnd = user32.GetParent(root.winfo_id())
        style = user32.GetWindowLongW(hwnd, GWL_STYLE)
        user32.SetWindowLongW(hwnd, GWL_STYLE, style & ~WS_MAXIMIZEBOX)
        SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_FRAMECHANGED = 0x1, 0x2, 0x4, 0x20
        user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0,
                            SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_FRAMECHANGED)
    except Exception:
        pass


def run_gui(bc: AudioBroadcaster):
    root = tk.Tk()
    root.title("DoBrowserSpeaker")
    root.geometry("380x600")
    root.resizable(False, False)
    _apply_app_id(root)
    try:
        root.iconbitmap(default=str(STATIC / "icon.ico"))
    except Exception:
        pass
    root.after(0, lambda: _disable_maximize(root))

    # ---- Light palette ----
    BG = "#ffffff"
    SURF = "#f1f5f9"          # input/card bg
    BORDER = "#e2e8f0"
    FG = "#0f172a"
    MUTED = "#64748b"
    ACCENT = "#1050AD"        # brand
    ACCENT_HOVER = "#0c3f87"
    BTN_PRIMARY = "#1050AD"
    BTN_PRIMARY_HOVER = "#0c3f87"

    root.configure(bg=BG)

    def lbl(parent, txt, size=10, bold=False, color=FG):
        weight = "bold" if bold else "normal"
        return tk.Label(parent, text=txt, fg=color, bg=BG,
                        font=("Segoe UI", size, weight))

    style = ttk.Style()
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure("BS.TCombobox",
                    fieldbackground=SURF, background=SURF,
                    foreground=FG, arrowcolor=MUTED,
                    bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                    borderwidth=1, relief="flat", padding=4)
    style.map("BS.TCombobox",
              fieldbackground=[("readonly", SURF)],
              foreground=[("readonly", FG)],
              bordercolor=[("focus", ACCENT)])
    style.configure("BS.Horizontal.TProgressbar",
                    troughcolor=BORDER, bordercolor=BORDER,
                    background=ACCENT, lightcolor=ACCENT, darkcolor=ACCENT,
                    thickness=6)
    style.configure("BS.TCheckbutton",
                    background=BG, foreground=FG,
                    font=("Segoe UI", 9), focuscolor=BG)
    style.map("BS.TCheckbutton",
              background=[("active", BG)],
              foreground=[("active", FG)])
    root.option_add("*TCombobox*Listbox.background", "#ffffff")
    root.option_add("*TCombobox*Listbox.foreground", FG)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")

    # ---- Audio source ----
    lbl(root, "Audio source", 9, color=MUTED).pack(anchor="w", padx=14, pady=(10, 2))
    devices = bc.list_loopback_devices()
    dev_labels = []
    for d in devices:
        tag = "  •  default" if d.get("default") else ""
        dev_labels.append(f"{d['name'][:48]}{tag}")
    dev_var = tk.StringVar(value=dev_labels[0] if dev_labels else "")
    dev_combo = ttk.Combobox(root, textvariable=dev_var, values=dev_labels,
                             state="readonly", style="BS.TCombobox",
                             font=("Segoe UI", 9))
    dev_combo.pack(fill="x", padx=14)

    def on_device_change(*_):
        sel = dev_combo.current()
        if 0 <= sel < len(devices):
            bc.switch_device(devices[sel]["index"])
    dev_combo.bind("<<ComboboxSelected>>", on_device_change)

    # ---- Network interface ----
    lbl(root, "Network interface", 9, color=MUTED).pack(anchor="w", padx=14, pady=(10, 2))
    interfaces = list_interfaces()
    iface_options = [f"{name}  —  {ip}" for name, ip in interfaces]
    iface_var = tk.StringVar(value=iface_options[0])
    iface_combo = ttk.Combobox(root, textvariable=iface_var, values=iface_options,
                               state="readonly", style="BS.TCombobox",
                               font=("Segoe UI", 9))
    iface_combo.pack(fill="x", padx=14)

    tk.Label(root, text="Make sure the other device is connected to the same network.",
             fg=MUTED, bg=BG, font=("Segoe UI", 8, "italic"),
             wraplength=350, justify="center").pack(pady=(8, 0))

    # ---- URL ----
    url_var = tk.StringVar()
    url_entry = tk.Entry(root, textvariable=url_var, font=("Consolas", 12),
                         justify="center", bg=SURF, fg=FG,
                         insertbackground=FG, relief="flat",
                         highlightthickness=1, highlightbackground=BORDER,
                         highlightcolor=ACCENT)
    url_entry.config(state="readonly", readonlybackground=SURF)
    url_entry.pack(fill="x", padx=14, pady=(10, 4), ipady=4)

    # ---- Buttons row ----
    btn_row = tk.Frame(root, bg=BG)
    btn_row.pack(pady=2)

    def hover(btn, normal, over):
        btn.bind("<Enter>", lambda e: btn.configure(bg=over))
        btn.bind("<Leave>", lambda e: btn.configure(bg=normal))

    def copy_url():
        root.clipboard_clear()
        root.clipboard_append(url_var.get())

    btn_copy = tk.Button(btn_row, text="Copy URL", command=copy_url,
                         bg=SURF, fg=FG, activebackground=BORDER, activeforeground=FG,
                         relief="flat", font=("Segoe UI", 9),
                         padx=12, pady=5, cursor="hand2", bd=0)
    btn_copy.pack(side="left", padx=3)
    hover(btn_copy, SURF, BORDER)

    def play_test():
        if bc.loop is not None:
            asyncio.run_coroutine_threadsafe(bc.play_test_tone(), bc.loop)

    btn_test = tk.Button(btn_row, text="Send test tone", command=play_test,
                         bg=BTN_PRIMARY, fg="#ffffff",
                         activebackground=BTN_PRIMARY_HOVER, activeforeground="#ffffff",
                         relief="flat", font=("Segoe UI", 9, "bold"),
                         padx=12, pady=5, cursor="hand2", bd=0)
    btn_test.pack(side="left", padx=3)
    hover(btn_test, BTN_PRIMARY, BTN_PRIMARY_HOVER)

    # ---- QR ----
    qr_lbl = tk.Label(root, bg=BG, bd=0)
    qr_lbl.pack(pady=8)

    def render(*_):
        sel = iface_var.get()
        ip = sel.split("—")[-1].strip() if "—" in sel else "127.0.0.1"
        url = f"http://{ip}:{PORT}"
        url_var.set(url)
        qr = qrcode.QRCode(box_size=4, border=1)
        qr.add_data(url)
        qr.make()
        img = qr.make_image(fill_color="#0f172a", back_color="#ffffff").convert("RGB")
        photo = ImageTk.PhotoImage(img)
        qr_lbl.configure(image=photo)
        qr_lbl.image = photo

    iface_combo.bind("<<ComboboxSelected>>", render)
    render()

    # ---- Status & level meter ----
    clients_var = tk.StringVar(value="0 devices connected")
    tk.Label(root, textvariable=clients_var, fg=ACCENT, bg=BG,
             font=("Segoe UI", 11, "bold")).pack(pady=(2, 4))

    level_frame = tk.Frame(root, bg=BG)
    level_frame.pack(fill="x", padx=14)
    level = ttk.Progressbar(level_frame, orient="horizontal",
                            mode="determinate", maximum=100,
                            style="BS.Horizontal.TProgressbar")
    level.pack(fill="x")
    level_text = tk.StringVar(value="—")
    tk.Label(root, textvariable=level_text, fg=MUTED, bg=BG,
             font=("Consolas", 8)).pack(pady=(2, 0))

    status_var = tk.StringVar(value="Starting...")
    tk.Label(root, textvariable=status_var, fg=MUTED, bg=BG,
             font=("Segoe UI", 8), wraplength=350, justify="center").pack(pady=(4, 2))

    autostart_var = tk.BooleanVar(value=autostart_enabled())
    def on_autostart_toggle():
        set_autostart(autostart_var.get())
        autostart_var.set(autostart_enabled())
    autostart_label = "Start on Windows boot" if IS_WINDOWS else "Start on startup"
    ttk.Checkbutton(root, text=autostart_label,
                    variable=autostart_var, style="BS.TCheckbutton",
                    command=on_autostart_toggle, cursor="hand2").pack(pady=(4, 0))

    footer_row = tk.Frame(root, bg=BG)
    footer_row.pack(side="bottom", pady=8)
    footer = tk.Label(footer_row, text="github.com/dogukansahil",
                      fg=MUTED, bg=BG, cursor="hand2",
                      font=("Segoe UI", 8, "underline"))
    footer.pack(side="left")
    footer.bind("<Button-1>", lambda e: webbrowser.open("https://github.com/dogukansahil/"))
    footer.bind("<Enter>", lambda e: footer.configure(fg=ACCENT))
    footer.bind("<Leave>", lambda e: footer.configure(fg=MUTED))
    tk.Label(footer_row, text="v1.0", fg=MUTED, bg=BG,
             font=("Segoe UI", 8)).pack(side="left", padx=(8, 0))

    prev_clients = [0]

    def tick():
        try:
            n_now = len(bc.clients)
            if n_now > 0 and prev_clients[0] == 0:
                try:
                    root.iconify()
                except Exception:
                    pass
            elif n_now == 0 and prev_clients[0] > 0:
                try:
                    root.deiconify()
                    root.lift()
                except Exception:
                    pass
            prev_clients[0] = n_now

            if bc.error:
                status_var.set(f"ERROR: {bc.error}")
            elif bc.device_info is not None:
                name = str(bc.device_info.get("name", ""))[:54]
                stale = (time.time() - bc.last_callback_ts) if bc.last_callback_ts else 999
                if bc.total_chunks == 0:
                    sig = "waiting for source"
                elif stale > 1.0:
                    sig = "silent"
                else:
                    sig = "streaming"
                status_var.set(f"{name} · {bc.sample_rate} Hz / {bc.channels} ch · {sig}")
            n = len(bc.clients)
            clients_var.set("1 device connected" if n == 1 else f"{n} devices connected")

            rms = bc.last_rms
            db = 20 * math.log10(rms + 1e-9)
            pct = max(0.0, min(100.0, (db + 60) * (100 / 60)))
            level["value"] = pct
            level_text.set(f"{db:+.1f} dBFS")
        except Exception:
            pass
        root.after(150, tick)

    tick()

    def on_close():
        try:
            bc.stop()
        finally:
            root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


def main():
    if not STATIC.exists():
        STATIC.mkdir(parents=True)
    bc = AudioBroadcaster()
    ready = threading.Event()
    threading.Thread(target=run_server_thread, args=(bc, ready), daemon=True).start()
    ready.wait(timeout=5)
    run_gui(bc)


if __name__ == "__main__":
    main()
