"""
SyncEngine -- Multi-stage audio cross-correlation engine.

Synchronises camera audio with external recorder (e.g. Zoom) audio
using a 4-stage algorithm inspired by PluralEyes:

  Stage 1: Raw FFT cross-correlation (multi-slice or first N seconds, windowed)
  Stage 2: Envelope cross-correlation (full camera, windowed)
  Stage 3: Envelope cross-correlation (full camera, full recorder)
  Stage 4: Refinement -- raw xcorr in a tight window around the
           envelope peak for sub-sample precision.

v1.1 additions:
  - Multi-slice Stage 1: 3 slices (beginning, middle, end) with consensus
  - Peak-to-second-peak ratio: measures match distinctness
  - VAD (Voice Activity Detection): skips xcorr if camera audio is too weak
  - Spectral whitening: reduces microphone signature differences
  - Diagnostics: per-stage records for debugging sync failures

v1.3.0 refactoring:
  - DSP helpers extracted to dsp.py (parabolic_interpolation,
    compute_peak_ratio, compute_envelope, extract_slices,
    multi_slice_consensus).
  - Cross-correlation functions extracted to xcorr.py (xcorr,
    xcorr_windowed, xcorr_envelope, xcorr_envelope_windowed).
  - SyncEngine remains the orchestrator with thin wrappers for
    backward compatibility.

Dependencies: numpy, scipy (only).
"""

from __future__ import annotations

import gc
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import numpy as np

from synclab.core.audio import (
    extract_wav,
    load_wav,
    bandpass_filter,
    safe_remove,
    compute_speech_ratio,
    spectral_whiten,
)
from synclab.core.dsp import (
    compute_envelope,
    extract_slices,
    multi_slice_consensus,
)
from synclab.core.xcorr import (
    xcorr,
    xcorr_windowed,
    xcorr_envelope,
    xcorr_envelope_windowed,
)

# Type alias for the progress callback: (stage_name, detail_text) -> None
ProgressCallback = Optional[Callable[[str, str], None]]


class SyncEngine:
    """Multi-stage audio synchronisation engine.

    Uses FFT-based cross-correlation and RMS-envelope correlation to
    find the precise time offset between a camera recording and one or
    more external audio files (typically from a Zoom recorder).

    Parameters
    ----------
    config : dict
        Engine configuration.  Recognised keys:

        * ``sample_rate``     -- analysis sample rate in Hz (default 8000)
        * ``max_camera_sec``  -- max seconds of camera audio for raw
          cross-correlation (default 60)
        * ``max_zoom_sec``    -- max seconds to extract from Zoom audio;
          0 means no limit (default 0)
        * ``sync_window_sec`` -- half-width of the search window in
          seconds around a predicted offset (default 30)
        * ``bandpass_low``    -- low cut-off for the voice bandpass
          filter in Hz (default 200)
        * ``bandpass_high``   -- high cut-off for the voice bandpass
          filter in Hz (default 4000)
        * ``multi_slice_enabled`` -- use multi-slice Stage 1 (default True)
        * ``multi_slice_count``   -- number of slices (default 3)
        * ``multi_slice_duration`` -- seconds per slice (default 20)
        * ``vad_threshold``   -- speech_ratio below this skips xcorr (default 0.05)
        * ``spectral_whiten`` -- apply spectral whitening (default False)

    progress_callback : callable, optional
        Called as ``progress_callback(stage_name, detail_text)`` at the
        start of each processing stage so that a UI can report progress.
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(
        self,
        config: Dict[str, Any],
        progress_callback: ProgressCallback = None,
    ) -> None:
        self.config = config
        self.sr: int = int(config.get("sample_rate", 8000))
        self.max_sec_camera: int = int(config.get("max_camera_sec", 60))
        self.max_sec_zoom: int = int(config.get("max_zoom_sec", 0))
        self._bandpass_low: int = int(config.get("bandpass_low", 200))
        self._bandpass_high: int = int(config.get("bandpass_high", 4000))

        self._progress: ProgressCallback = progress_callback

        # Cache: key = str(wav_path) -> value = Path to temp file
        self._zoom_cache: Dict[str, Path] = {}

    # ------------------------------------------------------------------
    # Progress helper
    # ------------------------------------------------------------------

    def _report(self, stage: str, detail: str = "") -> None:
        """Fire the progress callback if one was provided."""
        if self._progress is not None:
            self._progress(stage, detail)

    # ------------------------------------------------------------------
    # Camera audio preparation
    # ------------------------------------------------------------------

    def prepare_camera_audio(
        self,
        video_path: Path,
        temp_dir: Path,
        video_duration: float = 60,
    ) -> Optional[Path]:
        """Extract camera audio as a mono WAV at the analysis sample rate.

        Extracts up to ``video_duration + 10`` seconds (minimum
        ``max_camera_sec``) so that envelope-based stages have enough
        context for longer recordings.

        Parameters
        ----------
        video_path : Path
            Path to the camera video file.
        temp_dir : Path
            Directory for temporary WAV files.
        video_duration : float
            Nominal duration of the video in seconds.

        Returns
        -------
        Path or None
            Path to the extracted WAV, or ``None`` on failure.
        """
        self._report("prepare_camera", str(video_path.name))

        uid = f"{video_path.stem[:30]}_{id(video_path) % 10000}"
        wav_out = temp_dir / f"_va_{uid}.wav"

        max_sec = max(self.max_sec_camera, int(video_duration) + 10)
        ok = extract_wav(video_path, wav_out, self.sr, max_sec)
        if not ok:
            safe_remove(wav_out)
            return None

        # Verify that the extracted audio has meaningful content
        data, _ = load_wav(wav_out)
        if data is None or len(data) < self.sr:
            safe_remove(wav_out)
            return None

        return wav_out

    # ------------------------------------------------------------------
    # Zoom audio extraction (cached)
    # ------------------------------------------------------------------

    def _get_zoom_audio(
        self,
        wav_path: Path,
        temp_dir: Path,
        suffix: str = "",
    ) -> Optional[Path]:
        """Extract Zoom audio with caching -- avoids re-extraction.

        Parameters
        ----------
        wav_path : Path
            Source Zoom WAV file.
        temp_dir : Path
            Directory for temporary files.
        suffix : str
            Optional suffix appended to the temp file name.

        Returns
        -------
        Path or None
            Path to the cached/extracted WAV, or ``None`` on failure.
        """
        key = str(wav_path)
        if key in self._zoom_cache:
            cached = self._zoom_cache[key]
            if cached.exists() and cached.stat().st_size > 1000:
                return cached

        uid = f"{Path(wav_path).stem[:30]}_{suffix}"
        ea_temp = temp_dir / f"_ea_{uid}.wav"

        if ea_temp.exists() and ea_temp.stat().st_size > 1000:
            self._zoom_cache[key] = ea_temp
            return ea_temp

        ok = extract_wav(wav_path, ea_temp, self.sr, self.max_sec_zoom)
        if ok:
            self._zoom_cache[key] = ea_temp
            return ea_temp

        return None

    def clear_zoom_cache(self) -> None:
        """Delete all cached Zoom temp files and reset the cache."""
        for _key, path in self._zoom_cache.items():
            safe_remove(path)
        self._zoom_cache.clear()

    # ------------------------------------------------------------------
    # Wrappers — delegate to module-level functions in dsp.py / xcorr.py
    #
    # These thin wrappers maintain backward compatibility so that
    # sync_with_zoom() and tests can continue calling self._xcorr()
    # etc. without changes.
    # ------------------------------------------------------------------

    def _extract_slices(
        self,
        cam_bp: np.ndarray,
    ) -> List[tuple[np.ndarray, int]]:
        """Extract multi-slice segments from camera audio."""
        count = int(self.config.get("multi_slice_count", 3))
        dur = int(self.config.get("multi_slice_duration", 20))
        return extract_slices(cam_bp, self.sr, count, dur)

    def _multi_slice_consensus(
        self,
        results: List[tuple[float, float, float]],
        tolerance: float = 0.5,
    ) -> tuple[float, float, float]:
        """Find consensus offset from multiple slices."""
        return multi_slice_consensus(results, tolerance)

    def _xcorr(
        self,
        cam: np.ndarray,
        zoom: np.ndarray,
    ) -> tuple[float, float, float]:
        """Normalised cross-correlation: find *cam* inside *zoom*."""
        return xcorr(cam, zoom, self.sr)

    def _xcorr_windowed(
        self,
        cam: np.ndarray,
        zoom: np.ndarray,
        predicted_offset: float,
        window_sec: Optional[float] = None,
    ) -> tuple[float, float, float]:
        """Cross-correlation in a window around *predicted_offset*."""
        if window_sec is None:
            window_sec = self.config.get("sync_window_sec", 30)
        return xcorr_windowed(cam, zoom, self.sr, predicted_offset, window_sec)

    def _compute_envelope(
        self,
        data: np.ndarray,
        hop: int = 200,
    ) -> np.ndarray:
        """Compute the RMS amplitude envelope of *data*."""
        return compute_envelope(data, hop)

    def _xcorr_envelope(
        self,
        cam: np.ndarray,
        zoom: np.ndarray,
        hop: int = 200,
    ) -> tuple[float, float, float]:
        """Cross-correlate amplitude envelopes."""
        return xcorr_envelope(cam, zoom, self.sr, hop)

    def _xcorr_envelope_windowed(
        self,
        cam: np.ndarray,
        zoom: np.ndarray,
        predicted_offset: float,
        window_sec: Optional[float] = None,
        hop: int = 200,
    ) -> tuple[float, float, float]:
        """Windowed envelope cross-correlation around *predicted_offset*."""
        if window_sec is None:
            window_sec = self.config.get("sync_window_sec", 30)
        return xcorr_envelope_windowed(
            cam, zoom, self.sr, predicted_offset, window_sec, hop,
        )

    # ------------------------------------------------------------------
    # Main sync algorithm (4 stages)
    # ------------------------------------------------------------------

    def sync_with_zoom(
        self,
        cam_wav: Path,
        zoom_wav_files: List[Path],
        temp_dir: Path,
        suffix: str = "",
        predicted_offset: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Synchronise camera audio with one or more Zoom recordings.

        Runs a multi-stage algorithm with progressive refinement:

        1. **Raw xcorr** -- multi-slice or first ``max_camera_sec`` of
           camera audio, windowed around ``predicted_offset`` if available.
        2. **Envelope xcorr** -- full camera audio, windowed.
        3. **Envelope xcorr (full search)** -- full camera + full Zoom
           when the first two stages have low confidence.
        4. **Refinement** -- tight raw xcorr (+/-3 s) around the best
           envelope peak for sub-sample precision.

        Parameters
        ----------
        cam_wav : Path
            Path to the camera WAV (mono, at ``self.sr`` Hz).
        zoom_wav_files : list of Path
            Candidate Zoom WAV files to try.
        temp_dir : Path
            Working directory for temp files.
        suffix : str
            Optional label used in temp file names.
        predicted_offset : float or None
            Predicted offset in seconds (e.g. from timestamps).

        Returns
        -------
        dict
            Keys: ``offset`` (seconds), ``confidence`` (0--1),
            ``method`` (string label), ``level`` (quality tier 0--10),
            ``peak_ratio`` (float), ``speech_ratio`` (float),
            ``diagnostics`` (dict with per-stage records).
        """
        self._report("sync_load", "Loading camera audio")

        # -- Diagnostics container (v1.1) --
        diagnostics: Dict[str, Any] = {
            "predicted_offset": predicted_offset,
            "stages": [],
            "speech_ratio": None,
            "spectral_whiten": self.config.get("spectral_whiten", False),
            "multi_slice": self.config.get("multi_slice_enabled", True),
            "final_method": "none",
            "final_offset": 0.0,
            "final_confidence": 0.0,
            "final_peak_ratio": 0.0,
            "why_fallback": None,
        }

        # Load full camera audio
        cam_data, _ = load_wav(cam_wav)
        if cam_data is None or len(cam_data) < self.sr:
            diagnostics["why_fallback"] = "cam_audio_too_short"
            return {"offset": 0.0, "confidence": 0.0,
                    "method": "error", "level": 0,
                    "peak_ratio": 0.0, "speech_ratio": 0.0,
                    "diagnostics": diagnostics}

        # Voice bandpass filter
        cam_bp = bandpass_filter(
            cam_data, self.sr, self._bandpass_low, self._bandpass_high,
        )

        # -- VAD: Voice Activity Detection (v1.1) --
        speech_ratio = compute_speech_ratio(cam_bp, self.sr)
        diagnostics["speech_ratio"] = speech_ratio

        vad_threshold = self.config.get("vad_threshold", 0.05)
        if speech_ratio < vad_threshold:
            self._report(
                "sync_vad",
                f"Camera audio too weak (speech_ratio={speech_ratio:.3f})",
            )
            diagnostics["why_fallback"] = (
                f"speech_ratio {speech_ratio:.3f} < threshold {vad_threshold}"
            )
            diagnostics["final_method"] = "vad_skip"
            del cam_data, cam_bp
            gc.collect()
            return {
                "offset": 0.0, "confidence": 0.0,
                "method": "vad_skip", "level": 0,
                "peak_ratio": 0.0, "speech_ratio": speech_ratio,
                "diagnostics": diagnostics,
            }

        # -- Optional spectral whitening (v1.1) --
        if self.config.get("spectral_whiten", False):
            cam_bp = spectral_whiten(cam_bp)

        # Short excerpt for raw xcorr (first max_camera_sec seconds)
        max_raw = self.max_sec_camera * self.sr
        cam_short = cam_bp[:max_raw] if len(cam_bp) > max_raw else cam_bp

        best: Dict[str, Any] = {
            "offset": 0.0, "confidence": 0.0,
            "method": "none", "level": 0,
            "peak_ratio": 0.0,
        }

        for wav_path in zoom_wav_files:
            self._report("sync_zoom", f"Trying {Path(wav_path).name}")

            ea_temp = self._get_zoom_audio(wav_path, temp_dir, suffix)
            if ea_temp is None:
                continue

            zoom_data, _ = load_wav(ea_temp)
            if zoom_data is None or len(zoom_data) < self.sr:
                continue

            # Voice bandpass filter on Zoom audio
            zoom_bp = bandpass_filter(
                zoom_data, self.sr,
                self._bandpass_low, self._bandpass_high,
            )

            # Optional spectral whitening on Zoom
            if self.config.get("spectral_whiten", False):
                zoom_bp = spectral_whiten(zoom_bp)

            try:
                zoom_name = Path(wav_path).name

                # === Stage 1: Raw xcorr (multi-slice or short camera) ===
                self._report(
                    "sync_stage1",
                    "Raw cross-correlation",
                )
                multi_slice = self.config.get("multi_slice_enabled", True)

                if multi_slice:
                    # Multi-slice: 3 slices with consensus
                    slices = self._extract_slices(cam_bp)
                    slice_results: List[tuple[float, float, float]] = []

                    for si, (slice_data, slice_start_sample) in enumerate(slices):
                        if predicted_offset is not None:
                            s_off, s_conf, s_pr = self._xcorr_windowed(
                                slice_data, zoom_bp, predicted_offset,
                            )
                        else:
                            s_off, s_conf, s_pr = self._xcorr(
                                slice_data, zoom_bp,
                            )

                        # Adjust offset: xcorr finds where slice appears
                        # in zoom. Video start = that position minus slice
                        # position within the video.
                        adjusted_off = s_off - (slice_start_sample / self.sr)

                        slice_results.append((adjusted_off, s_conf, s_pr))

                        diagnostics["stages"].append({
                            "stage": f"stage1_raw_slice{si}",
                            "zoom_file": zoom_name,
                            "offset": adjusted_off,
                            "confidence": s_conf,
                            "peak_ratio": s_pr,
                            "window_used": (
                                "predicted" if predicted_offset else "full"
                            ),
                        })

                    offset, conf, peak_ratio = self._multi_slice_consensus(
                        slice_results,
                    )
                else:
                    # Legacy: single first-60s excerpt
                    if predicted_offset is not None:
                        offset, conf, peak_ratio = self._xcorr_windowed(
                            cam_short, zoom_bp, predicted_offset,
                        )
                    else:
                        offset, conf, peak_ratio = self._xcorr(
                            cam_short, zoom_bp,
                        )

                    diagnostics["stages"].append({
                        "stage": "stage1_raw",
                        "zoom_file": zoom_name,
                        "offset": offset,
                        "confidence": conf,
                        "peak_ratio": peak_ratio,
                        "window_used": (
                            "predicted" if predicted_offset else "full"
                        ),
                    })

                if conf > best.get("confidence", 0.0):
                    best = {
                        "offset": offset, "confidence": conf,
                        "method": "xcorr", "level": 10,
                        "peak_ratio": peak_ratio,
                    }

                if conf >= 0.30:
                    break  # High confidence -- done

                # === Stage 2: Envelope xcorr (full camera, windowed) ===
                self._report(
                    "sync_stage2",
                    "Envelope cross-correlation (full camera, windowed)",
                )
                if predicted_offset is not None:
                    e_offset, e_conf, e_pr = self._xcorr_envelope_windowed(
                        cam_bp, zoom_bp, predicted_offset,
                    )
                else:
                    e_offset, e_conf, e_pr = self._xcorr_envelope(
                        cam_bp, zoom_bp,
                    )

                diagnostics["stages"].append({
                    "stage": "stage2_envelope",
                    "zoom_file": zoom_name,
                    "offset": e_offset,
                    "confidence": e_conf,
                    "peak_ratio": e_pr,
                    "window_used": (
                        "predicted" if predicted_offset else "full"
                    ),
                })

                if e_conf > best.get("confidence", 0.0):
                    # Stage 4: Refine with tight raw xcorr (+/-3 s)
                    self._report(
                        "sync_stage4",
                        "Refining with raw xcorr around envelope peak",
                    )
                    ref_off, ref_conf, ref_pr = self._xcorr_windowed(
                        cam_short, zoom_bp, e_offset, window_sec=3,
                    )

                    diagnostics["stages"].append({
                        "stage": "stage4_refine",
                        "zoom_file": zoom_name,
                        "offset": ref_off,
                        "confidence": ref_conf,
                        "peak_ratio": ref_pr,
                        "window_used": "envelope_peak",
                    })

                    if ref_conf >= 0.02:
                        best = {
                            "offset": ref_off, "confidence": e_conf,
                            "method": "envelope+refined", "level": 9,
                            "peak_ratio": max(e_pr, ref_pr),
                        }
                    else:
                        best = {
                            "offset": e_offset, "confidence": e_conf,
                            "method": "envelope", "level": 8,
                            "peak_ratio": e_pr,
                        }

                if best.get("confidence", 0.0) >= 0.30:
                    break

                # === Stage 3: Full envelope search (no window) ===
                # Runs verification for any result below 0.30 confidence
                # (eliminates "dead zone" where medium results were unverified)
                if (predicted_offset is not None
                        and best.get("confidence", 0.0) < 0.30):
                    self._report(
                        "sync_stage3",
                        "Envelope cross-correlation (full search)",
                    )
                    e_off_full, e_conf_full, e_pr_full = (
                        self._xcorr_envelope(cam_bp, zoom_bp)
                    )

                    diagnostics["stages"].append({
                        "stage": "stage3_envelope_full",
                        "zoom_file": zoom_name,
                        "offset": e_off_full,
                        "confidence": e_conf_full,
                        "peak_ratio": e_pr_full,
                        "window_used": "full",
                    })

                    if e_conf_full > best.get("confidence", 0.0):
                        ref_off, ref_conf, ref_pr = self._xcorr_windowed(
                            cam_short, zoom_bp, e_off_full, window_sec=3,
                        )

                        diagnostics["stages"].append({
                            "stage": "stage4_refine_full",
                            "zoom_file": zoom_name,
                            "offset": ref_off,
                            "confidence": ref_conf,
                            "peak_ratio": ref_pr,
                            "window_used": "envelope_full_peak",
                        })

                        if ref_conf >= 0.02:
                            best = {
                                "offset": ref_off,
                                "confidence": e_conf_full,
                                "method": "envelope_full+refined",
                                "level": 8,
                                "peak_ratio": max(e_pr_full, ref_pr),
                            }
                        else:
                            best = {
                                "offset": e_off_full,
                                "confidence": e_conf_full,
                                "method": "envelope_full",
                                "level": 7,
                                "peak_ratio": e_pr_full,
                            }

            except Exception:
                pass
            finally:
                del zoom_data, zoom_bp
                gc.collect()

        # -- Finalize diagnostics --
        diagnostics["final_method"] = best["method"]
        diagnostics["final_offset"] = best["offset"]
        diagnostics["final_confidence"] = best["confidence"]
        diagnostics["final_peak_ratio"] = best.get("peak_ratio", 0.0)
        if best["confidence"] < 0.05:
            diagnostics["why_fallback"] = "confidence_below_threshold"

        # Free camera arrays
        del cam_data, cam_bp, cam_short
        gc.collect()

        self._report("sync_done", f"Best method: {best['method']}")

        best["speech_ratio"] = speech_ratio
        best["diagnostics"] = diagnostics
        return best
