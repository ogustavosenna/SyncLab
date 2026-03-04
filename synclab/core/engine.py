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

Dependencies: numpy, scipy (only).
"""

from __future__ import annotations

import gc
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import numpy as np
from scipy import signal

from synclab.core.audio import (
    extract_wav,
    load_wav,
    bandpass_filter,
    safe_remove,
    compute_speech_ratio,
    spectral_whiten,
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
    # Multi-slice extraction (v1.1)
    # ------------------------------------------------------------------

    def _extract_slices(
        self,
        cam_bp: np.ndarray,
    ) -> List[tuple[np.ndarray, int]]:
        """Extract multi-slice segments from camera audio.

        Returns list of (slice_data, start_sample) tuples for
        beginning, middle, and end of the recording.
        """
        count = int(self.config.get("multi_slice_count", 3))
        dur = int(self.config.get("multi_slice_duration", 20))
        slice_samples = dur * self.sr
        total = len(cam_bp)

        if total <= slice_samples:
            return [(cam_bp, 0)]

        slices = []
        # Beginning
        slices.append((cam_bp[:slice_samples], 0))
        # Middle
        mid_start = (total - slice_samples) // 2
        slices.append((cam_bp[mid_start:mid_start + slice_samples], mid_start))
        # End
        end_start = total - slice_samples
        slices.append((cam_bp[end_start:], end_start))

        return slices[:count]

    def _multi_slice_consensus(
        self,
        results: List[tuple[float, float, float]],
        tolerance: float = 0.5,
    ) -> tuple[float, float, float]:
        """Find consensus offset from multiple slices.

        Parameters
        ----------
        results : list of (offset, confidence, peak_ratio) tuples
        tolerance : float
            Maximum disagreement in seconds between two slices
            to consider them "agreeing".

        Returns
        -------
        (offset, confidence, peak_ratio) : the consensus result.
            If 2+ slices agree within tolerance, returns the average
            offset, max confidence, and max peak_ratio of the group.
            Otherwise, returns the single best result by confidence.
        """
        if len(results) <= 1:
            return results[0] if results else (0.0, 0.0, 0.0)

        best_group: List[tuple[float, float, float]] = []
        best_group_conf = 0.0

        for i in range(len(results)):
            group = [results[i]]
            for j in range(len(results)):
                if i != j and abs(results[i][0] - results[j][0]) <= tolerance:
                    group.append(results[j])
            if len(group) >= 2:
                max_conf = max(r[1] for r in group)
                if max_conf > best_group_conf:
                    best_group = group
                    best_group_conf = max_conf

        if best_group:
            avg_off = sum(r[0] for r in best_group) / len(best_group)
            max_conf = max(r[1] for r in best_group)
            max_pr = max(r[2] for r in best_group)
            return avg_off, max_conf, max_pr

        # No consensus: return the slice with highest confidence
        return max(results, key=lambda r: r[1])

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

    # ------------------------------------------------------------------
    # Raw FFT cross-correlation with parabolic interpolation
    # ------------------------------------------------------------------

    def _xcorr(
        self,
        cam: np.ndarray,
        zoom: np.ndarray,
    ) -> tuple[float, float, float]:
        """Normalised cross-correlation: find *cam* inside *zoom*.

        Uses ``scipy.signal.fftconvolve`` for O(n log n) performance.
        A parabolic (3-point) interpolation around the peak yields
        sub-sample precision.

        Parameters
        ----------
        cam : ndarray
            Camera audio signal.
        zoom : ndarray
            Zoom (external recorder) audio signal.

        Returns
        -------
        offset_seconds : float
            Positive means the camera starts at position *offset* inside
            the Zoom recording.
        confidence : float
            Peak of the normalised correlation (0--1).
        peak_ratio : float
            Ratio of highest peak to second-highest peak.  Higher values
            indicate a more distinct (less ambiguous) match.
        """
        cam = np.asarray(cam, dtype=np.float64)
        zoom = np.asarray(zoom, dtype=np.float64)

        # Zero-mean
        cam = cam - np.mean(cam)
        zoom = zoom - np.mean(zoom)

        # Check energy
        std_c = np.std(cam)
        std_z = np.std(zoom)
        if std_c < 1e-10 or std_z < 1e-10:
            return 0.0, 0.0, 0.0

        # Normalised cross-correlation via FFT
        corr = signal.fftconvolve(zoom, cam[::-1], mode="full")
        corr = corr / (len(cam) * std_c * std_z)

        # Find peak
        pk = int(np.argmax(corr))
        conf = float(corr[pk])
        offset_samp = float(pk - (len(cam) - 1))

        # Parabolic interpolation for sub-sample accuracy
        if 0 < pk < len(corr) - 1:
            y0 = float(corr[pk - 1])
            y1 = float(corr[pk])
            y2 = float(corr[pk + 1])
            denom = 2.0 * (2 * y1 - y0 - y2)
            if abs(denom) > 1e-10:
                delta = (y0 - y2) / denom
                offset_samp += delta

        # -- Peak-to-second-peak ratio (v1.1) --
        peak_ratio = float("inf")
        if len(corr) > 10:
            min_gap = max(int(0.5 * self.sr), 1)
            corr_copy = corr.copy()
            mask_start = max(0, pk - min_gap)
            mask_end = min(len(corr_copy), pk + min_gap)
            corr_copy[mask_start:mask_end] = 0.0
            pk2 = int(np.argmax(corr_copy))
            conf2 = float(corr_copy[pk2])
            if conf2 > 1e-10:
                peak_ratio = conf / conf2

        return offset_samp / self.sr, max(conf, 0.0), peak_ratio

    # ------------------------------------------------------------------
    # Windowed cross-correlation
    # ------------------------------------------------------------------

    def _xcorr_windowed(
        self,
        cam: np.ndarray,
        zoom: np.ndarray,
        predicted_offset: float,
        window_sec: Optional[float] = None,
    ) -> tuple[float, float, float]:
        """Cross-correlation in a window around *predicted_offset*.

        Extracts a segment of the Zoom audio centred on the predicted
        offset and runs :meth:`_xcorr` on that segment.  The returned
        offset is expressed in global Zoom time.

        Parameters
        ----------
        cam : ndarray
            Camera audio signal.
        zoom : ndarray
            Full Zoom audio signal.
        predicted_offset : float
            Expected offset in seconds.
        window_sec : float or None
            Half-width of the search window.  Defaults to
            ``config["sync_window_sec"]`` (typically 30 s).

        Returns
        -------
        offset_global : float
            Offset in seconds relative to the start of *zoom*.
        confidence : float
            Peak normalised correlation.
        peak_ratio : float
            Peak distinctness ratio.
        """
        if window_sec is None:
            window_sec = self.config.get("sync_window_sec", 30)

        sr = self.sr
        cam_dur_sec = len(cam) / sr

        # Window boundaries in the Zoom signal
        win_start = max(0, int((predicted_offset - window_sec) * sr))
        win_end = min(
            len(zoom),
            int((predicted_offset + cam_dur_sec + window_sec) * sr),
        )

        if win_end <= win_start:
            return 0.0, 0.0, 0.0

        zoom_win = zoom[win_start:win_end]
        if len(zoom_win) < len(cam) // 2:
            return 0.0, 0.0, 0.0

        # Cross-correlation inside the window
        offset_local, conf, peak_ratio = self._xcorr(cam, zoom_win)

        # Convert local offset to global (relative to Zoom start)
        offset_global = offset_local + (win_start / sr)
        return offset_global, conf, peak_ratio

    # ------------------------------------------------------------------
    # Envelope computation (RMS amplitude)
    # ------------------------------------------------------------------

    def _compute_envelope(
        self,
        data: np.ndarray,
        hop: int = 200,
    ) -> np.ndarray:
        """Compute the RMS amplitude envelope of *data*.

        At the default analysis rate of 8 kHz, a hop of 200 samples
        gives 25 ms frames -- capturing *when* there is energy (speech
        vs. silence) rather than frequency content.  This makes the
        envelope robust across different microphones recording the same
        acoustic event.

        Parameters
        ----------
        data : ndarray
            Audio signal.
        hop : int
            Frame length in samples.

        Returns
        -------
        ndarray
            RMS amplitude per frame.
        """
        n = len(data) // hop
        if n < 2:
            return np.array([np.sqrt(np.mean(data**2))])
        frames = data[: n * hop].reshape(n, hop)
        return np.sqrt(np.mean(frames**2, axis=1))

    # ------------------------------------------------------------------
    # Envelope cross-correlation
    # ------------------------------------------------------------------

    def _xcorr_envelope(
        self,
        cam: np.ndarray,
        zoom: np.ndarray,
        hop: int = 200,
    ) -> tuple[float, float, float]:
        """Cross-correlate amplitude envelopes.

        More robust than raw sample-level cross-correlation when the
        camera and external recorder use different microphones (different
        frequency responses, noise floors, etc.).

        Parameters
        ----------
        cam : ndarray
            Camera audio.
        zoom : ndarray
            Zoom audio.
        hop : int
            Envelope frame size in samples.

        Returns
        -------
        offset_seconds : float
            Time offset in seconds.
        confidence : float
            Peak normalised correlation.
        peak_ratio : float
            Peak distinctness ratio.
        """
        env_c = self._compute_envelope(cam, hop)
        env_z = self._compute_envelope(zoom, hop)

        if len(env_c) < 2 or len(env_z) < 2:
            return 0.0, 0.0, 0.0

        env_c = env_c.astype(np.float64)
        env_z = env_z.astype(np.float64)
        env_c = env_c - np.mean(env_c)
        env_z = env_z - np.mean(env_z)

        std_c = np.std(env_c)
        std_z = np.std(env_z)
        if std_c < 1e-10 or std_z < 1e-10:
            return 0.0, 0.0, 0.0

        corr = signal.fftconvolve(env_z, env_c[::-1], mode="full")
        corr = corr / (len(env_c) * std_c * std_z)

        pk = int(np.argmax(corr))
        conf = float(corr[pk])
        offset_frames = float(pk - (len(env_c) - 1))

        # Parabolic interpolation for sub-frame precision
        if 0 < pk < len(corr) - 1:
            y0 = float(corr[pk - 1])
            y1 = float(corr[pk])
            y2 = float(corr[pk + 1])
            denom = 2.0 * (2 * y1 - y0 - y2)
            if abs(denom) > 1e-10:
                delta = (y0 - y2) / denom
                offset_frames += delta

        # -- Peak-to-second-peak ratio (v1.1) --
        peak_ratio = float("inf")
        if len(corr) > 10:
            # Envelope operates at reduced sample rate (sr / hop)
            env_sr = self.sr / hop
            min_gap = max(int(0.5 * env_sr), 1)
            corr_copy = corr.copy()
            mask_start = max(0, pk - min_gap)
            mask_end = min(len(corr_copy), pk + min_gap)
            corr_copy[mask_start:mask_end] = 0.0
            pk2 = int(np.argmax(corr_copy))
            conf2 = float(corr_copy[pk2])
            if conf2 > 1e-10:
                peak_ratio = conf / conf2

        offset_sec = offset_frames * hop / self.sr
        return offset_sec, max(conf, 0.0), peak_ratio

    # ------------------------------------------------------------------
    # Windowed envelope cross-correlation
    # ------------------------------------------------------------------

    def _xcorr_envelope_windowed(
        self,
        cam: np.ndarray,
        zoom: np.ndarray,
        predicted_offset: float,
        window_sec: Optional[float] = None,
        hop: int = 200,
    ) -> tuple[float, float, float]:
        """Windowed envelope cross-correlation around *predicted_offset*.

        Combines the robustness of envelope correlation with the
        efficiency of a limited search window.

        Parameters
        ----------
        cam : ndarray
            Camera audio (full).
        zoom : ndarray
            Zoom audio (full).
        predicted_offset : float
            Expected offset in seconds.
        window_sec : float or None
            Half-width of the window (defaults to
            ``config["sync_window_sec"]``).
        hop : int
            Envelope frame size in samples.

        Returns
        -------
        offset_global : float
            Offset in seconds relative to the start of *zoom*.
        confidence : float
            Peak normalised correlation of the envelopes.
        peak_ratio : float
            Peak distinctness ratio.
        """
        if window_sec is None:
            window_sec = self.config.get("sync_window_sec", 30)

        sr = self.sr
        cam_dur_sec = len(cam) / sr

        win_start = max(0, int((predicted_offset - window_sec) * sr))
        win_end = min(
            len(zoom),
            int((predicted_offset + cam_dur_sec + window_sec) * sr),
        )

        if win_end <= win_start:
            return 0.0, 0.0, 0.0

        zoom_win = zoom[win_start:win_end]
        if len(zoom_win) < len(cam) // 2:
            return 0.0, 0.0, 0.0

        offset_local, conf, peak_ratio = self._xcorr_envelope(
            cam, zoom_win, hop,
        )
        offset_global = offset_local + (win_start / sr)
        return offset_global, conf, peak_ratio
