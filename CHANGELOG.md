# SyncLab - Changelog

## v1.3.1 (2026-03-04)

### Performance
- Brute-force audio matching now runs in parallel using ThreadPoolExecutor
- ~4-6x speedup on projects with multiple unmatched videos
- Pre-warming zoom cache eliminates lock contention

### Distribution
- ffmpeg and ffprobe are now bundled inside the installer (zero external dependencies)
- Windows installer via Inno Setup (87 MB, per-user install, no admin required)
- Bilingual installer (English + Brazilian Portuguese)

### Testing
- 14 new tests for parallel infrastructure (thread safety, worker function)
- Total: 133 tests passing

---

## v1.3.0 (2026-03-04)

### Architecture
- Refactored monolithic engine into modular architecture
- Extracted: `dsp.py`, `xcorr.py`, `calibration.py`, `timeline.py`, `metadata.py`, `media.py`
- SmartMatcher remains the orchestrator with thin wrappers

### Code Quality
- Replaced all 58 `print()` calls with formal `logging` module
- Centralized logging configuration (`logging_config.py`)
- Added `pyproject.toml` with ruff + pytest configuration

### Testing
- Added comprehensive test infrastructure: 119 tests covering all core modules
- Test coverage: 95% on DSP module, 41% overall

---

## v1.2.2 (2026-02-28)

### Matching
- Added `min_peak_ratio` quality filter to reject ambiguous matches
- Improved source-folder preference for multi-card shoots

---

## v1.2.1 (2026-02-28)

### Matching
- Added spectral whitening to reduce microphone signature differences
- Expanded timestamp tolerance when initial assignment is below 50%

---

## v1.1.0 (2026-02-27)

### Core Algorithm
- Multi-slice Stage 1: 3 slices (beginning, middle, end) with consensus
- Voice Activity Detection (VAD): skips xcorr if camera audio is too weak
- Peak-to-second-peak ratio for match quality assessment
- Per-stage diagnostics for debugging sync failures

---

## v1.0.0 (2026-02-26)

### Initial Release
- 4-stage audio cross-correlation engine (raw FFT, envelope windowed, envelope full, refinement)
- Timestamp-first matching with automatic clock offset calibration
- Premiere Pro XML export
- Desktop app with pywebview + Flask-SocketIO
