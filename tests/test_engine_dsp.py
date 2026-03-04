"""
Tests for SyncEngine DSP functions (_xcorr, _xcorr_envelope, etc.)
Priority 2: Core math of sync — if this breaks, everything breaks.

Tests access private methods via instance for characterization before refactoring.
After refactoring (Step 2), imports will change to module-level functions.
"""

import numpy as np
import pytest

from synclab.config import get_config
from synclab.core.engine import SyncEngine


@pytest.fixture
def engine():
    """Create a SyncEngine with default config."""
    config = get_config()
    return SyncEngine(config)


# ---------------------------------------------------------------------------
# _xcorr — Raw FFT cross-correlation
# ---------------------------------------------------------------------------

class TestXcorr:
    """Test raw FFT cross-correlation."""

    def test_exact_offset_recovery(self, engine, make_offset_pair):
        """Given cam embedded in zoom at offset 10s, _xcorr should recover it."""
        short, long, true_offset = make_offset_pair(
            signal_duration=3.0, total_duration=30.0, offset_sec=10.0, sr=8000
        )
        offset, conf, peak_ratio = engine._xcorr(short, long)

        assert abs(offset - true_offset) < 0.05, (
            f"Offset error too large: {offset:.3f} vs true {true_offset:.3f}"
        )
        assert conf > 0.3, f"Confidence too low: {conf:.3f}"
        assert peak_ratio > 3.0, f"Peak ratio too low: {peak_ratio:.3f}"

    def test_offset_at_beginning(self, engine):
        """Signal at the beginning (offset=0) should return ~0."""
        rng = np.random.RandomState(123)
        short = rng.randn(8000 * 2).astype(np.float32)  # 2s
        long = np.zeros(8000 * 20, dtype=np.float32)
        long[:len(short)] = short  # embed at offset 0

        offset, conf, peak_ratio = engine._xcorr(short, long)
        assert abs(offset) < 0.1, f"Expected offset ~0, got {offset:.3f}"

    def test_offset_near_end(self, engine):
        """Signal near the end of zoom should still be found."""
        rng = np.random.RandomState(456)
        sr = 8000
        short = rng.randn(sr * 2).astype(np.float32)
        long = np.zeros(sr * 30, dtype=np.float32) * 0.001
        offset_sec = 25.0
        start = int(offset_sec * sr)
        long[start:start + len(short)] += short

        offset, conf, peak_ratio = engine._xcorr(short, long)
        assert abs(offset - offset_sec) < 0.1, (
            f"Expected offset ~{offset_sec}, got {offset:.3f}"
        )

    def test_silence_returns_zero_confidence(self, engine):
        """If cam or zoom is silence, confidence should be 0."""
        silence = np.zeros(8000, dtype=np.float32)
        noise = np.random.randn(80000).astype(np.float32)

        _, conf, _ = engine._xcorr(silence, noise)
        assert conf == 0.0

    def test_peak_ratio_clear_match(self, engine, make_offset_pair):
        """A single clear match should have high peak_ratio (> 5.0)."""
        short, long, _ = make_offset_pair(
            signal_duration=3.0, total_duration=30.0, offset_sec=10.0,
            sr=8000, noise_level=0.01
        )
        _, _, peak_ratio = engine._xcorr(short, long)
        assert peak_ratio > 5.0, f"Expected clear match peak_ratio > 5, got {peak_ratio:.2f}"

    def test_peak_ratio_ambiguous_match(self, engine):
        """Two copies of the signal should give low peak_ratio (~1.0)."""
        rng = np.random.RandomState(789)
        sr = 8000
        short = rng.randn(sr * 2).astype(np.float32)
        long = np.zeros(sr * 30, dtype=np.float32) * 0.001

        # Place at two positions
        long[sr * 5:sr * 5 + len(short)] += short
        long[sr * 20:sr * 20 + len(short)] += short

        _, _, peak_ratio = engine._xcorr(short, long)
        assert peak_ratio < 2.0, (
            f"Expected ambiguous peak_ratio < 2.0, got {peak_ratio:.2f}"
        )

    def test_returns_tuple_of_three(self, engine, make_offset_pair):
        """Result should be (offset, confidence, peak_ratio) tuple."""
        short, long, _ = make_offset_pair()
        result = engine._xcorr(short, long)
        assert len(result) == 3
        offset, conf, pr = result
        assert isinstance(offset, float)
        assert isinstance(conf, float)
        assert isinstance(pr, float)


# ---------------------------------------------------------------------------
# _compute_envelope — RMS amplitude envelope
# ---------------------------------------------------------------------------

class TestComputeEnvelope:
    """Test RMS envelope computation."""

    def test_reduces_length(self, engine, make_sine):
        """Envelope length should be input_length / hop."""
        signal = make_sine(freq=440, duration=1.0, sr=8000)
        hop = 200
        env = engine._compute_envelope(signal, hop=hop)
        expected_len = len(signal) // hop
        assert len(env) == expected_len

    def test_captures_energy(self, engine):
        """Loud sections should have higher envelope values than silent sections."""
        sr = 8000
        # First half loud, second half silent
        loud = np.random.randn(sr * 2).astype(np.float32) * 0.5
        silent = np.zeros(sr * 2, dtype=np.float32)
        signal = np.concatenate([loud, silent])

        env = engine._compute_envelope(signal, hop=200)
        mid = len(env) // 2
        loud_avg = np.mean(env[:mid])
        silent_avg = np.mean(env[mid:])

        assert loud_avg > silent_avg * 5, (
            f"Loud section envelope ({loud_avg:.4f}) should be >> silent ({silent_avg:.4f})"
        )

    def test_very_short_signal(self, engine):
        """Signal shorter than 2 hops should return single-element array."""
        short = np.array([0.5, 0.3, 0.1], dtype=np.float32)
        env = engine._compute_envelope(short, hop=200)
        assert len(env) == 1


# ---------------------------------------------------------------------------
# _xcorr_envelope — Envelope cross-correlation
# ---------------------------------------------------------------------------

class TestXcorrEnvelope:
    """Test envelope-based cross-correlation."""

    def test_offset_recovery(self, engine, make_offset_pair):
        """Should recover offset from amplitude envelope correlation."""
        short, long, true_offset = make_offset_pair(
            signal_duration=5.0, total_duration=60.0, offset_sec=20.0,
            sr=8000, noise_level=0.01
        )
        offset, conf, peak_ratio = engine._xcorr_envelope(short, long)

        # Envelope resolution is coarser — allow larger tolerance
        assert abs(offset - true_offset) < 1.0, (
            f"Envelope offset error: {offset:.2f} vs true {true_offset:.2f}"
        )
        assert conf > 0.1, f"Envelope confidence too low: {conf:.3f}"

    def test_returns_seconds(self, engine, make_offset_pair):
        """Offset should be in seconds (not samples or frames)."""
        short, long, true_offset = make_offset_pair(
            signal_duration=3.0, total_duration=30.0, offset_sec=10.0, sr=8000
        )
        offset, _, _ = engine._xcorr_envelope(short, long)
        # Should be a reasonable number of seconds, not thousands of frames
        assert 0 <= offset < 40, f"Offset {offset} doesn't look like seconds"


# ---------------------------------------------------------------------------
# _xcorr_windowed — Windowed cross-correlation
# ---------------------------------------------------------------------------

class TestXcorrWindowed:
    """Test windowed cross-correlation around predicted offset."""

    def test_recovers_offset_in_window(self, engine, make_offset_pair):
        """With a good prediction, windowed xcorr should find the exact offset."""
        short, long, true_offset = make_offset_pair(
            signal_duration=3.0, total_duration=30.0, offset_sec=10.0, sr=8000
        )
        # Predicted offset is close to true
        offset, conf, _ = engine._xcorr_windowed(short, long, predicted_offset=10.5)

        assert abs(offset - true_offset) < 0.1, (
            f"Windowed offset error: {offset:.3f} vs true {true_offset:.3f}"
        )

    def test_bad_prediction_may_fail(self, engine, make_offset_pair):
        """If prediction is completely wrong, windowed xcorr gets low confidence."""
        short, long, true_offset = make_offset_pair(
            signal_duration=3.0, total_duration=60.0, offset_sec=50.0, sr=8000
        )
        # Prediction is 40 seconds off
        offset, conf, _ = engine._xcorr_windowed(
            short, long, predicted_offset=10.0, window_sec=5
        )
        # The signal is at 50s but we're searching around 10s with 5s window
        # So it should NOT find a good match
        assert conf < 0.3 or abs(offset - true_offset) > 5.0


# ---------------------------------------------------------------------------
# _extract_slices — Multi-slice extraction
# ---------------------------------------------------------------------------

class TestExtractSlices:
    """Test multi-slice segment extraction."""

    def test_three_slices_from_long_signal(self, engine, make_sine):
        """A signal longer than slice duration should yield 3 slices."""
        # 60s signal, default slice=20s
        signal = make_sine(freq=440, duration=60.0, sr=8000)
        slices = engine._extract_slices(signal)
        assert len(slices) == 3

    def test_short_signal_returns_single_slice(self, engine, make_sine):
        """A signal shorter than one slice should return just itself."""
        signal = make_sine(freq=440, duration=5.0, sr=8000)  # 5s < 20s slice
        slices = engine._extract_slices(signal)
        assert len(slices) == 1
        assert len(slices[0][0]) == len(signal)

    def test_slices_cover_beginning_middle_end(self, engine):
        """Slices should be from beginning, middle, and end of signal."""
        sr = 8000
        signal = np.arange(sr * 60, dtype=np.float32)  # 60s
        slices = engine._extract_slices(signal)

        starts = [s[1] for s in slices]
        assert starts[0] == 0, "First slice should start at 0"
        assert starts[2] > sr * 30, "Third slice should be near the end"


# ---------------------------------------------------------------------------
# _multi_slice_consensus — Consensus from multiple slices
# ---------------------------------------------------------------------------

class TestMultiSliceConsensus:
    """Test consensus algorithm for multi-slice results."""

    def test_agreeing_slices(self, engine):
        """Two slices agreeing should average their offsets."""
        results = [
            (10.0, 0.5, 5.0),   # offset=10.0, conf=0.5, pr=5.0
            (10.1, 0.6, 4.0),   # agrees with first (within 0.5s)
            (50.0, 0.4, 3.0),   # outlier
        ]
        offset, conf, pr = engine._multi_slice_consensus(results, tolerance=0.5)
        assert abs(offset - 10.05) < 0.1, f"Expected avg ~10.05, got {offset}"
        assert conf == 0.6, f"Expected max conf 0.6, got {conf}"
        assert pr == 5.0, f"Expected max pr 5.0, got {pr}"

    def test_no_consensus_returns_best(self, engine):
        """Without consensus, return the highest-confidence result."""
        results = [
            (10.0, 0.3, 2.0),
            (30.0, 0.7, 3.0),  # highest confidence
            (50.0, 0.1, 1.0),
        ]
        offset, conf, pr = engine._multi_slice_consensus(results, tolerance=0.5)
        assert offset == 30.0
        assert conf == 0.7

    def test_single_result(self, engine):
        """Single result should be returned as-is."""
        results = [(10.0, 0.5, 5.0)]
        offset, conf, pr = engine._multi_slice_consensus(results)
        assert offset == 10.0
        assert conf == 0.5
        assert pr == 5.0

    def test_empty_results(self, engine):
        """Empty results should return zeros."""
        offset, conf, pr = engine._multi_slice_consensus([])
        assert offset == 0.0
