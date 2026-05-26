# BrowserSpeaker

Turn any phone, tablet, or laptop into a wireless speaker for your Windows PC. Anything that has a browser becomes an extra speaker on the same network — no app to install on the client.

## How it works

Requires Windows 10+ and Python 3.10+ to build. End users do not need Python — only the built .exe file inside the dist folder.

The PC captures its own audio output via WASAPI loopback and streams raw PCM over a local WebSocket. Clients open `http://<your-pc-ip>:8765` in any browser and play it back through Web Audio API.

<div style="display: flex; align-items: center; justify-content: center; gap: 10px; width: 100%;">
  <img src="docs/screenshot1.png" alt="BrowserSpeaker PC UI" style="height: 400px; width: auto; object-fit: contain;">
  <img src="docs/screenshot2.png" alt="BrowserSpeaker Mobile UI" style="height: 400px; width: auto; object-fit: contain;">
</div>
## Features

- 48 kHz stereo Float32 PCM streaming, ~80 ms end-to-end buffer
- Adaptive playback-rate drift correction (±2 %) — no clicks, no buffer resets
- Per-device source selection (any WASAPI loopback target on the PC)
- Per-interface network selection (Ethernet, Wi-Fi, virtual adapters)
- Live RMS / dBFS meter and a built-in test-tone generator
- QR code for one-tap mobile join
- Media Session integration — lock-screen title, artwork, background playback
- Silence keep-alive so the mobile media session never decays when the PC is quiet
- Optional auto-start on Windows boot (HKCU Run key)
- Auto-minimize when a client connects, auto-restore on disconnect

## Build it yourself

This project ships only as source. Compile your own binary.

```
git clone https://github.com/dogukansahil/browserspeaker
cd browserspeaker
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
python build.py
```

The result is `dist\BrowserSpeaker.exe` (~40 MB), fully self-contained. Move it wherever you want; no installer, no registry footprint until you tick "Start on Windows boot" inside the app.

## Run from source

```
.venv\Scripts\python server.py
```

Or launch silently via `BrowserSpeaker.vbs` (no console window).

## On the client

Open the URL the PC window shows, or scan the QR. Tap **Start**. Make sure both devices are on the same network.

## Notes

- Windows Defender occasionally flags PyInstaller one-file executables as a false positive. If it eats the exe, add the folder to Defender exclusions or build it yourself and trust your own binary.
- For lowest latency, prefer 5 GHz Wi-Fi or Ethernet over 2.4 GHz.

## Disclaimer

This software is provided **as is**, without warranty of any kind, express or implied. It is open source — you are expected to read it, build it, and run it on your own machine, under your own responsibility. The author is not liable for any data loss, audio routing mishaps, network exposure, antivirus false positives, hearing damage from accidentally maxed-out volume, or any other unintended consequence. By running this code you accept full responsibility for it.

## License

Licensed under the **Apache License, Version 2.0**. See [LICENSE](LICENSE) for the full text and [NOTICE](NOTICE.md) for third-party attributions.

---

[github.com/dogukansahil](https://github.com/dogukansahil/)
