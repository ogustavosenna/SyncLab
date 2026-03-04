# SyncLab v1.2.2 — Progress Report
**Date:** 2026-03-04
**Author:** Gustavo (filmmaker/creator) + Claude (AI development)
**Purpose:** External review by ChatGPT and Gemini for architecture evaluation, improvement suggestions, and distribution readiness assessment.

---

## 1. What is SyncLab?

SyncLab is a **desktop application for automatic audio-video synchronization**, inspired by PluralEyes (discontinued by Red Giant). It solves a common problem in documentary and multi-camera production: matching external audio recorder files (e.g., ZOOM recorders) to video files from cameras that have lower-quality built-in microphones.

**Core workflow:**
1. User selects video folder(s) and audio folder(s)
2. SyncLab scans for video files and ZOOM recorder WAV files
3. A 4-phase matching pipeline runs: metadata → timestamp calibration → audio cross-correlation → brute-force fallback
4. User exports an Adobe Premiere Pro XML (FCP XML v5) with the synced timeline
5. User imports XML into Premiere — all clips are aligned with their corresponding external audio

**Tech stack:**
- Python 3.14 (core logic, DSP, Flask server)
- Flask + Flask-SocketIO (REST API + real-time WebSocket progress)
- PyWebView (native desktop window wrapping the web UI)
- PyInstaller (distribution as standalone .exe)
- NumPy + SciPy (FFT cross-correlation, signal processing)
- ffmpeg/ffprobe (media extraction and metadata)
- HTML/CSS/JS single-page app (dark theme, Socket.IO real-time updates)

---

## 2. Current State (v1.2.2)

### Sync Accuracy
Tested on a real documentary production (62 video clips across 2 shooting days, 20+ ZOOM recorder files):

| Version | Correct | Accuracy | Notes |
|---------|---------|----------|-------|
| v1.0 (early) | ~40/62 | ~65% | Many cross-day errors, missing clips |
| v1.2.0 | 54/62 | 87% | Major improvements, but critical bugs |
| v1.2.1 | 57/62 | 92% | Fixed filter bugs, source-folder preference |
| v1.2.1 (build 3) | 59/62 | 95.2% | Sanity checks, offset validation |
| v1.2.2 | 59/62 | 95.2% | Quality filter (peak_ratio), no false syncs |

**Key quality improvement in v1.2.2:** Matches with `peak_ratio < 2.0` are now rejected (left as unmatched). This eliminated ALL incorrect syncs — the remaining 3 "errors" are now correctly shown as UNMATCHED rather than wrongly synced. Additionally, `timestamp_only` matches no longer place external audio in the XML export, preventing wrong-offset audio placement.

### Features
- Multi-folder input (multiple video and audio source folders)
- Automatic ZOOM recorder detection (supports nested folder structures)
- 3-pass clock offset calibration (coarse 15s → fine 1s → sub-second 0.1s)
- Multi-stage audio cross-correlation (raw xcorr → envelope windowed → envelope full → refinement)
- Spectral whitening to reduce microphone signature bias
- Voice Activity Detection (VAD) to skip silent clips
- Brute-force fallback for clips without timestamp assignment
- Source-folder preference to avoid cross-day false positives
- Adaptive timestamp tolerance (auto-expands 15s→30s if <50% assigned)
- Peak ratio quality filter (rejects ambiguous matches)
- Real-time progress with phase-weighted progress bar
- Notification sound on completion
- Confidence badges (high/medium/low) with colored dots
- **Label colors in Premiere XML** (Forest=high confidence, Mango=medium, Iris=timestamp, Rose=unmatched)
- Export diagnostics JSON alongside XML for debugging
- Export support package (ZIP with config, system info, diagnostics, XML)
- Responsive results list with sticky action buttons
- Native Windows folder picker (COM IFileDialog)
- Drag-and-drop folder input (PyWebView native + browser fallback)
- Settings panel (FPS, resolution, threshold, extensions)
- Persistent folder memory across sessions

---

## 3. Architecture

```
J:\SyncLab\synclab\
├── __init__.py              (7 lines)   — Version: 1.2.2
├── config.py                (50 lines)  — Default configuration
├── settings.py              (96 lines)  — JSON settings persistence
├── dependencies.py          (105 lines) — ffmpeg dependency check
├── subprocess_utils.py      (29 lines)  — Windows subprocess helpers
│
├── core/
│   ├── audio.py             (216 lines) — Audio extraction, filtering, VAD
│   ├── media.py             (78 lines)  — Media info via ffprobe
│   ├── engine.py            (966 lines) — 4-stage cross-correlation engine
│   └── matcher.py           (1335 lines)— Timestamp-first smart matching
│
├── scanner/
│   └── scanner.py           (259 lines) — Video/audio file discovery
│
├── export/
│   └── premiere_xml.py      (759 lines) — FCP XML v5 generator
│
└── app/
    ├── main.py              (356 lines) — PyWebView desktop launcher
    ├── server.py            (805 lines) — Flask REST API + WebSocket
    ├── pywebview_patch.py   (140 lines) — PyWebView compatibility patch
    └── static/
        ├── index.html       (196 lines) — Single-page HTML
        ├── css/style.css    (994 lines) — Dark theme CSS
        └── js/app.js        (1001 lines)— Frontend application logic
```

**Total: ~7,400 lines** (5,200 Python + 2,200 JS/CSS/HTML)

### Data Flow

```
[Video Folders] → Scanner → video_list[]
[Audio Folders] → Scanner → audio_groups[]
                      ↓
            Phase 1: Metadata (ffprobe)
                      ↓
            Phase 2: Timestamp Calibration
            (3-pass clock offset search)
                      ↓
            Phase 3: Audio Cross-Correlation
            (4-stage: raw xcorr → envelope windowed
             → envelope full → parabolic refinement)
                      ↓
            Phase 3b: Brute-Force Fallback
            (try all audio groups for unmatched videos)
                      ↓
            Quality Filter (peak_ratio >= 2.0)
                      ↓
            Timeline Assembly
                      ↓
            [Premiere XML Export] + [Diagnostics JSON]
```

### Audio Sync Algorithm (4 stages)

1. **Stage 1 — Raw Cross-Correlation:** FFT-based normalized cross-correlation between camera audio slices and recorder audio within a search window
2. **Stage 2 — Envelope Windowed:** RMS amplitude envelope cross-correlation around the timestamp-predicted offset (+/- 30s window)
3. **Stage 3 — Envelope Full:** Full-duration envelope correlation when windowed search fails
4. **Stage 4 — Parabolic Refinement:** Sub-sample accuracy refinement around the best peak found in stages 1-3

Each stage produces a `confidence` (correlation peak value) and `peak_ratio` (best peak / second-best peak). A high peak_ratio (>2.0) means there's one clear dominant match point.

---

## 4. Key Challenges and How We Solved Them

### Challenge 1: ZOOM Recorder Name Collisions
**Problem:** Multiple shooting days reset ZOOM numbering (ZOOM0001 exists in Day 2 AND Day 3). Single-source calibration cross-matched between days.

**Solution:** Source-folder detection (`multi_source` flag). When multiple source folders are detected, calibrate clock offset per source-folder pair independently. Brute-force also tracks `best_same_ri` to prefer same-source matches over cross-source.

### Challenge 2: Long Interview Clip Lost Sync (P1099108)
**Problem:** A 27-minute interview clip got zero brute-force candidates because a buggy pre-filter (`if ri in matched_recorders and rdur < vdur * 2: continue`) eliminated its correct recorder.

**Solution:** Removed the broken filter entirely. The brute-force now tests all audio groups without arbitrary exclusions.

### Challenge 3: Filesystem Time Ranges Wider Than Audio Duration
**Problem:** `_get_recorder_time_range()` uses `st_ctime`/`st_mtime` which can create much wider time ranges than actual audio content. This caused videos to be assigned to recorders with predicted offsets exceeding the actual audio duration (e.g., 611s offset into a 495s file).

**Solution:** Sanity check in `_timestamp_assign()`: rejects assignment if `offset_in_recorder > recorder_duration + tolerance`.

### Challenge 4: False Positives with Ambiguous Peaks
**Problem:** Some clips matched with decent confidence (0.3-0.5) but very low peak_ratio (1.0-1.5), meaning the "best" match was barely distinguishable from noise. These were ALL wrong.

**Solution:** Added `min_peak_ratio: 2.0` config. Matches below this threshold are rejected. Analysis showed ALL correct matches had peak_ratio > 6.0, and ALL incorrect matches had peak_ratio < 1.5. The threshold of 2.0 provides a safe margin.

### Challenge 5: Timestamp-Only Matches Placing Wrong Audio
**Problem:** When audio cross-correlation fails (camera filming B-roll while ZOOM records interview), the system fell back to timestamp_only — placing ZOOM audio at the timestamp-predicted offset. But since the audio content doesn't match at all, the waveforms were clearly misaligned in Premiere.

**Solution:** In the XML export, `timestamp_only` and `timestamp_fallback` methods no longer place external audio. The video clip and camera audio are still placed normally, but the ZOOM audio is omitted. This prevents visible misalignment in the editor.

### Challenge 6: Progress Bar UX
**Problem:** Progress showed 92% at "video 3 of 62" during brute-force because the phase ranges mapped brute-force to 92-99% but used global video index.

**Solution:** Pass `bf_index`/`bf_total` from matcher for accurate brute-force progress. Each phase now calculates progress proportionally within its own range.

---

## 5. Code Quality Assessment

### Files Exceeding 400 Lines

| File | Lines | Concern |
|------|------:|---------|
| `matcher.py` | 1,335 | Largest — contains full matching pipeline |
| `engine.py` | 966 | Audio DSP — 4-stage cross-correlation |
| `server.py` | 805 | Flask app + all API routes |
| `premiere_xml.py` | 759 | XML generation for Premiere |
| `app.js` | 1,001 | Full frontend SPA |
| `style.css` | 994 | Complete dark theme |

**Regarding the "400-line rule":** This is a common industry guideline (not a strict rule) that suggests individual files should be under ~400 lines for maintainability. It's part of the Single Responsibility Principle — each file/class should do ONE thing. In our case, `matcher.py` at 1,335 lines is the main candidate for refactoring. It could be split into:

- `matcher/calibration.py` — Clock offset calibration (~200 lines)
- `matcher/timestamp.py` — Timestamp assignment logic (~200 lines)
- `matcher/brute_force.py` — Brute-force matching (~200 lines)
- `matcher/timeline.py` — Timeline assembly (~150 lines)
- `matcher/pipeline.py` — Main `match()` orchestrator (~300 lines)

Similarly, `engine.py` could be split into stage-specific modules and `server.py` could separate routes from sync logic. However, **this refactoring is cosmetic — it doesn't change functionality**. It should be done before v2.0 or before adding multi-camera support.

### What's Good
- Clear 4-layer architecture (core → scanner → export → app)
- Comprehensive diagnostics JSON for debugging every match decision
- Confidence badge system with visual feedback (colors in both UI and Premiere)
- Progressive enhancement: timestamp first, then audio confirmation
- Real-time WebSocket progress with phase-weighted percentages
- Clean separation between backend (Flask) and frontend (SPA)

### What Could Improve
- Large files need refactoring (matcher.py, engine.py)
- No automated tests (no unit tests, no integration tests)
- No type annotations in some modules
- No logging framework (uses print statements)
- No CI/CD pipeline
- Config could use a proper schema/validation

---

## 6. Distribution Readiness Assessment

### Ready for Distribution
- Standalone .exe via PyInstaller (no Python installation needed)
- Only external dependency: ffmpeg (checked on startup with user-friendly error)
- Settings persistence across sessions
- Support package export for debugging user issues
- Professional dark theme UI
- Version badge displayed in UI

### Not Yet Ready
- No installer (just a folder with .exe + dependencies)
- No auto-update mechanism
- No license/activation system
- No crash reporting
- No telemetry/analytics
- No user documentation/help
- No multi-language support (UI is English, user is Brazilian Portuguese)

### Recommended Pre-Distribution Tasks
1. **Create an installer** (NSIS or Inno Setup) for clean Windows installation
2. **Add basic error recovery** — if sync crashes, don't lose state
3. **User guide / tooltips** — explain what each phase does
4. **License system** (if selling) — could use a simple license key validation
5. **Landing page** with download link and pricing

---

## 7. Future Roadmap

### v1.3 — Quality & Polish (recommended before distribution)
- [ ] Refactor large files (matcher.py, engine.py, server.py)
- [ ] Add unit tests for core DSP functions
- [ ] Add proper logging framework (Python logging module)
- [ ] Improve brute-force accuracy with GCC-PHAT (for reverberant environments)
- [ ] Add MFCC-based coarse alignment (BBC audio-offset-finder approach)
- [ ] Parallel brute-force using multiprocessing
- [ ] Create Windows installer (Inno Setup)

### v2.0 — Multi-Camera Support
The user wants to support multiple cameras syncing to the same audio:
- **UI change:** Dropdown to select number of cameras (1-4+). Each camera gets its own drag-and-drop input area (Camera A, Camera B, Camera C).
- **Algorithm change:** Cross-correlate each camera's audio against the reference audio, then place all cameras on separate video tracks in the Premiere XML, aligned to the same audio timeline.
- **XML change:** Multiple `<track>` elements in the video section, one per camera.
- **Complexity:** Medium — the core sync engine already works per-video, the main change is in the UI and XML export.

### v2.0 — Timeline Preview
Instead of (or in addition to) the results list, show a simplified timeline visualization:
- Horizontal bars representing video clips and audio recordings
- Color-coded by confidence (green/orange/red)
- Zoomable/scrollable
- Click to inspect individual sync points
- Reference: PluralEyes-style timeline view

### v2.0 — Distribution Platform
- "Pay what you want" model with email capture
- Landing page with demo video
- Download hosted on GitHub Releases or Gumroad
- Optional license key for premium features

---

## 8. Questions for Reviewers (ChatGPT / Gemini)

1. **Architecture:** Is the current 4-layer architecture (core/scanner/export/app) appropriate for this type of application? Would you recommend a different structure?

2. **Code Quality:** Given the file sizes (matcher.py at 1,335 lines, engine.py at 966 lines), what's the best refactoring strategy that won't introduce regressions? Should we add tests first before refactoring?

3. **Algorithm:** Our 4-stage audio cross-correlation (raw xcorr → envelope windowed → envelope full → refinement) achieves 95%+ accuracy on real documentary footage. Are there known techniques we're missing? Specifically:
   - Would GCC-PHAT improve results for reverberant environments?
   - Would MFCC-based matching help for the remaining edge cases?
   - Is there a better approach for clips where the camera captures ambient sound while the recorder captures direct speech?

4. **Multi-Camera:** What's the best UX pattern for multi-camera sync? Should it be:
   - (A) Separate camera inputs on the main screen
   - (B) A "project mode" where you configure cameras first, then add folders
   - (C) Automatic detection (analyze video metadata to group by camera model/serial)

5. **Distribution:** For a niche professional tool targeting video editors:
   - Is "pay what you want" the right pricing model?
   - Should we offer a free tier with limitations (e.g., max 10 clips)?
   - What's the minimum viable feature set for a v1.0 commercial release?

6. **Performance:** The brute-force phase tests every unmatched video against every audio group sequentially. For a 100-video project with 20 audio groups, that's 2000 cross-correlations. Should we:
   - Parallelize with multiprocessing?
   - Use a coarse MFCC fingerprint to pre-filter candidates?
   - Both?

7. **Testing:** We currently have zero automated tests. What's the most impactful testing strategy for an audio DSP application? Unit tests for the math functions? Integration tests with known audio files? Regression tests comparing XML output?

---

## 9. File Listing for Code Review

If you'd like to review specific files, here are the most important ones by priority:

1. **`synclab/core/matcher.py`** (1,335 lines) — The brain: matching pipeline
2. **`synclab/core/engine.py`** (966 lines) — The ears: audio cross-correlation DSP
3. **`synclab/export/premiere_xml.py`** (759 lines) — The output: Premiere XML generation
4. **`synclab/app/server.py`** (805 lines) — The spine: Flask API + progress handling
5. **`synclab/config.py`** (50 lines) — Configuration defaults
6. **`synclab/scanner/scanner.py`** (259 lines) — File discovery logic
7. **`synclab/app/static/js/app.js`** (1,001 lines) — Frontend application

The full codebase is at `J:\SyncLab\`. Total: ~7,400 lines across 18 files.

---

*Report generated by Claude (Anthropic) as part of SyncLab development. March 4, 2026.*
