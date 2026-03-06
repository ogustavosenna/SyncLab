# SyncLab

Audio-video synchronization tool for documentary filmmakers.

SyncLab matches camera audio with external recorder audio using FFT cross-correlation, then generates a Premiere Pro XML timeline with all clips perfectly aligned.

## Features

- **Automatic sync** — drag two folders (camera + recorder) and click Sync
- **FFT cross-correlation** — robust audio fingerprinting, works even with noisy on-camera audio
- **Parallel processing** — ThreadPoolExecutor brute-force matching for fast results
- **Premiere Pro XML** — ready-to-import timeline with all clips synced
- **Zero dependencies** — standalone Windows installer, ffmpeg included

## Quick Start

1. Download the latest installer from [Releases](https://github.com/ogustavosenna/SyncLab/releases)
2. Install and open SyncLab
3. Drag your **camera folder** and **recorder folder** into the app
4. Click **Sync**
5. Import the generated `.xml` file into Premiere Pro

## Building from Source

```bash
pip install -r requirements.txt
python -m pytest tests/
python -m PyInstaller synclab.spec
```

## License

[MIT](LICENSE) — Copyright 2026 Gustavo Senna

## Code Signing Policy

Release binaries are built from this public repository and signed via
[SignPath Foundation](https://signpath.org), a free code signing service
for open-source projects. Each release artifact can be traced back to the
corresponding source code commit.
