"""Shared, real-time-safe numpy DSP core for the live audio engine.

Every class here follows the Real-Time Safety Contract in
docs/DAW_VISION_SPEC.md: no locking, no heap allocation once constructed
(scratch buffers are pre-sized at __init__ time and reused via in-place numpy
ops), and no branching on anything that isn't a plain attribute read. This
module is pure numpy (no scipy) so it stays inside the dependency set
reviewed for this feature -- see FEATURE_SECURITY_REVIEW.md.

Construction (buffer allocation, WAV loading, resampling) happens off the
audio callback thread, e.g. when a track/voice is created. Only the
process()/process_control_rate() methods run inside the callback.
"""
from __future__ import annotations

import math
import wave
from enum import Enum, auto

import numpy as np
from scipy.signal import lfilter

SAMPLE_RATE = 48000
MAX_SAMPLE_DURATION_SECONDS = 30.0
MIN_RELEASE_MS = 5.0

FILTER_CUTOFF_MIN_HZ = 20.0
FILTER_CUTOFF_MAX_HZ = 20_000.0
FILTER_RESONANCE_MIN = 0.5
FILTER_RESONANCE_MAX = 8.0

MASTER_LIMITER_CEILING_DB = -0.3


def _time_to_coeff(time_ms: float, sample_rate: int) -> float:
    """One-pole smoothing coefficient for a given time constant in ms."""
    time_ms = max(time_ms, 0.01)
    return 1.0 - math.exp(-1.0 / ((time_ms / 1000.0) * sample_rate))


def db_to_linear(db: float) -> float:
    return 10.0 ** (db / 20.0)


def linear_to_db(value: float) -> float:
    return 20.0 * math.log10(max(value, 1e-9))


# ---------------------------------------------------------------------------
# Sample loading
# ---------------------------------------------------------------------------

def read_wav_stereo(path: str, max_duration_seconds: float | None = MAX_SAMPLE_DURATION_SECONDS) -> tuple[np.ndarray, int]:
    """Reads a WAV file to a (n_frames, 2) float32 array in [-1, 1] and its
    source sample rate. Mono sources are duplicated L=R rather than handled
    as a separate code path. Resampling to SAMPLE_RATE is the caller's job
    (via resample_linear) so this function stays a pure file-read step.
    """
    with wave.open(path, "rb") as wav_file:
        n_channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        sample_rate = wav_file.getframerate()
        n_frames = wav_file.getnframes()
        if max_duration_seconds is not None:
            n_frames = min(n_frames, int(sample_rate * max_duration_seconds))
        raw = wav_file.readframes(n_frames)

    if sample_width == 1:
        data = np.frombuffer(raw, dtype=np.uint8).astype(np.float32)
        data = (data - 128.0) / 128.0
    elif sample_width == 2:
        data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    else:
        # 24/32-bit: fall back to a generic int32 read via numpy's stride
        # trick is more trouble than this feature is worth here -- treat as
        # int32 samples, which covers the common 32-bit PCM case.
        data = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0

    n = len(data) // max(1, n_channels)
    data = data[: n * n_channels].reshape(n, n_channels)

    if n_channels == 1:
        stereo = np.repeat(data, 2, axis=1)
    elif n_channels == 2:
        stereo = data
    else:
        stereo = data[:, :2]

    return stereo.astype(np.float32, copy=False), sample_rate


def resample_linear(samples: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Linear-interpolation resample of a (n, 2) float32 array. Not
    real-time-safe (allocates) -- only called at load time, off the audio
    callback thread."""
    if src_rate == dst_rate or len(samples) == 0:
        return samples
    src_n = len(samples)
    dst_n = max(1, int(round(src_n * dst_rate / src_rate)))
    src_positions = np.linspace(0, src_n - 1, dst_n, dtype=np.float64)
    idx0 = np.floor(src_positions).astype(np.int64)
    idx1 = np.minimum(idx0 + 1, src_n - 1)
    frac = (src_positions - idx0).astype(np.float32).reshape(-1, 1)
    out = samples[idx0] * (1.0 - frac) + samples[idx1] * frac
    return out.astype(np.float32, copy=False)


def load_and_prepare_sample(path: str) -> tuple[np.ndarray, int]:
    """Load + resample-to-SAMPLE_RATE in one step, for use by track/voice
    setup code. Returns (stereo_samples, SAMPLE_RATE)."""
    samples, src_rate = read_wav_stereo(path)
    samples = resample_linear(samples, src_rate, SAMPLE_RATE)
    return samples, SAMPLE_RATE


# ---------------------------------------------------------------------------
# Biquad filter (RBJ Audio EQ Cookbook), per-voice
# ---------------------------------------------------------------------------

class FilterType(Enum):
    LOWPASS = "lp"
    HIGHPASS = "hp"
    BANDPASS = "bp"


def design_biquad(filter_type: FilterType, cutoff_hz: float, resonance_q: float, sample_rate: int) -> tuple[float, float, float, float, float]:
    cutoff_hz = min(max(cutoff_hz, FILTER_CUTOFF_MIN_HZ), min(FILTER_CUTOFF_MAX_HZ, sample_rate / 2.0 - 100.0))
    q = min(max(resonance_q, FILTER_RESONANCE_MIN), FILTER_RESONANCE_MAX)

    w0 = 2.0 * math.pi * cutoff_hz / sample_rate
    cos_w0 = math.cos(w0)
    sin_w0 = math.sin(w0)
    alpha = sin_w0 / (2.0 * q)

    if filter_type == FilterType.LOWPASS:
        b0 = (1.0 - cos_w0) / 2.0
        b1 = 1.0 - cos_w0
        b2 = (1.0 - cos_w0) / 2.0
    elif filter_type == FilterType.HIGHPASS:
        b0 = (1.0 + cos_w0) / 2.0
        b1 = -(1.0 + cos_w0)
        b2 = (1.0 + cos_w0) / 2.0
    else:  # BANDPASS, constant 0 dB peak gain
        b0 = alpha
        b1 = 0.0
        b2 = -alpha

    a0 = 1.0 + alpha
    a1 = -2.0 * cos_w0
    a2 = 1.0 - alpha

    return b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0


class BiquadFilter:
    """Biquad via scipy.signal.lfilter (vectorized C implementation),
    independent persistent state per L/R channel via lfilter's zi/zf.

    Profiling during Phase 1 showed a pure-Python per-sample direct-form-I
    loop (this class's original implementation) was fast enough on its own
    -- the real cost was DelayLine/ReverbComb -- but lfilter is still the
    right tool for a small fixed-order (2) LTI filter called once per voice
    per block, and it's essentially free to use here once scipy is already
    a dependency for the delay/reverb fix. One instance per voice (see
    docs/DAW_VISION_SPEC.md's per-voice-vs-per-track split)."""

    def __init__(self, sample_rate: int = SAMPLE_RATE) -> None:
        self._sample_rate = sample_rate
        self._b = np.array([1.0, 0.0, 0.0], dtype=np.float64)
        self._a = np.array([1.0, 0.0, 0.0], dtype=np.float64)
        # lfilter zi shape for axis=0, 2-channel input: (order, n_channels)
        self._zi = np.zeros((2, 2), dtype=np.float64)

    def set_params(self, filter_type: FilterType, cutoff_hz: float, resonance_q: float) -> None:
        b0, b1, b2, a1, a2 = design_biquad(filter_type, cutoff_hz, resonance_q, self._sample_rate)
        self._b[:] = (b0, b1, b2)
        self._a[:] = (1.0, a1, a2)

    def set_raw_coeffs(self, b0: float, b1: float, b2: float, a1: float, a2: float) -> None:
        """For EQ shelf/peak stages, which design coefficients directly
        rather than through the LP/HP/BP cutoff+resonance API above."""
        self._b[:] = (b0, b1, b2)
        self._a[:] = (1.0, a1, a2)

    def process(self, block: np.ndarray) -> None:
        """In-place, audio-rate processing of a (n, 2) block. lfilter
        allocates its output array internally (scipy's API doesn't offer an
        out= parameter) -- a small, fixed-size allocation every call, the
        one deliberate exception to the Real-Time Safety Contract's
        no-allocation rule, noted there explicitly rather than glossed
        over."""
        y, self._zi = lfilter(self._b, self._a, block, axis=0, zi=self._zi)
        block[:] = y


# ---------------------------------------------------------------------------
# 3-band EQ (RBJ cookbook shelf/peak), per-track mixer strip
# ---------------------------------------------------------------------------

EQ_LOW_SHELF_HZ = 200.0
EQ_MID_PEAK_HZ = 1000.0
EQ_MID_PEAK_Q = 1.0
EQ_HIGH_SHELF_HZ = 5000.0
EQ_BAND_GAIN_MIN_DB = -15.0
EQ_BAND_GAIN_MAX_DB = 15.0


def design_low_shelf(freq_hz: float, gain_db: float, sample_rate: int, q: float = 0.707) -> tuple[float, float, float, float, float]:
    a = 10.0 ** (gain_db / 40.0)
    w0 = 2.0 * math.pi * freq_hz / sample_rate
    cos_w0, sin_w0 = math.cos(w0), math.sin(w0)
    alpha = sin_w0 / 2.0 * math.sqrt((a + 1.0 / a) * (1.0 / q - 1.0) + 2.0)
    two_sqrt_a_alpha = 2.0 * math.sqrt(a) * alpha
    b0 = a * ((a + 1.0) - (a - 1.0) * cos_w0 + two_sqrt_a_alpha)
    b1 = 2.0 * a * ((a - 1.0) - (a + 1.0) * cos_w0)
    b2 = a * ((a + 1.0) - (a - 1.0) * cos_w0 - two_sqrt_a_alpha)
    a0 = (a + 1.0) + (a - 1.0) * cos_w0 + two_sqrt_a_alpha
    a1 = -2.0 * ((a - 1.0) + (a + 1.0) * cos_w0)
    a2 = (a + 1.0) + (a - 1.0) * cos_w0 - two_sqrt_a_alpha
    return b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0


def design_high_shelf(freq_hz: float, gain_db: float, sample_rate: int, q: float = 0.707) -> tuple[float, float, float, float, float]:
    a = 10.0 ** (gain_db / 40.0)
    w0 = 2.0 * math.pi * freq_hz / sample_rate
    cos_w0, sin_w0 = math.cos(w0), math.sin(w0)
    alpha = sin_w0 / 2.0 * math.sqrt((a + 1.0 / a) * (1.0 / q - 1.0) + 2.0)
    two_sqrt_a_alpha = 2.0 * math.sqrt(a) * alpha
    b0 = a * ((a + 1.0) + (a - 1.0) * cos_w0 + two_sqrt_a_alpha)
    b1 = -2.0 * a * ((a - 1.0) + (a + 1.0) * cos_w0)
    b2 = a * ((a + 1.0) + (a - 1.0) * cos_w0 - two_sqrt_a_alpha)
    a0 = (a + 1.0) - (a - 1.0) * cos_w0 + two_sqrt_a_alpha
    a1 = 2.0 * ((a - 1.0) - (a + 1.0) * cos_w0)
    a2 = (a + 1.0) - (a - 1.0) * cos_w0 - two_sqrt_a_alpha
    return b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0


def design_peaking(freq_hz: float, gain_db: float, q: float, sample_rate: int) -> tuple[float, float, float, float, float]:
    a = 10.0 ** (gain_db / 40.0)
    w0 = 2.0 * math.pi * freq_hz / sample_rate
    cos_w0, sin_w0 = math.cos(w0), math.sin(w0)
    alpha = sin_w0 / (2.0 * q)
    b0 = 1.0 + alpha * a
    b1 = -2.0 * cos_w0
    b2 = 1.0 - alpha * a
    a0 = 1.0 + alpha / a
    a1 = -2.0 * cos_w0
    a2 = 1.0 - alpha / a
    return b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0


class ThreeBandEQ:
    """Low shelf (200Hz) -> mid peak (1kHz, fixed Q=1.0) -> high shelf
    (5kHz), each +/-15dB. Per-track, applied post-compressor/pre-fader in
    the mixdown chain (see docs/DAW_VISION_SPEC.md's Mixer Deck section)."""

    def __init__(self, sample_rate: int = SAMPLE_RATE) -> None:
        self._sample_rate = sample_rate
        self._low = BiquadFilter(sample_rate)
        self._mid = BiquadFilter(sample_rate)
        self._high = BiquadFilter(sample_rate)

    def process(self, block: np.ndarray, low_db: float, mid_db: float, high_db: float) -> None:
        low_db = min(max(low_db, EQ_BAND_GAIN_MIN_DB), EQ_BAND_GAIN_MAX_DB)
        mid_db = min(max(mid_db, EQ_BAND_GAIN_MIN_DB), EQ_BAND_GAIN_MAX_DB)
        high_db = min(max(high_db, EQ_BAND_GAIN_MIN_DB), EQ_BAND_GAIN_MAX_DB)
        if low_db != 0.0:
            self._low.set_raw_coeffs(*design_low_shelf(EQ_LOW_SHELF_HZ, low_db, self._sample_rate))
            self._low.process(block)
        if mid_db != 0.0:
            self._mid.set_raw_coeffs(*design_peaking(EQ_MID_PEAK_HZ, mid_db, EQ_MID_PEAK_Q, self._sample_rate))
            self._mid.process(block)
        if high_db != 0.0:
            self._high.set_raw_coeffs(*design_high_shelf(EQ_HIGH_SHELF_HZ, high_db, self._sample_rate))
            self._high.process(block)


# ---------------------------------------------------------------------------
# Envelope generator (audio-rate for the always-on amp envelope, control-rate
# for the FX rack's mod envelope) -- one instance per voice either way.
# ---------------------------------------------------------------------------

class EnvelopeStage(Enum):
    IDLE = auto()
    ATTACK = auto()
    DECAY = auto()
    SUSTAIN = auto()
    RELEASE = auto()


class ADSREnvelope:
    def __init__(self, attack_ms: float = 0.0, decay_ms: float = 0.0,
                 sustain_pct: float = 100.0, release_ms: float = MIN_RELEASE_MS) -> None:
        self.attack_ms = attack_ms
        self.decay_ms = decay_ms
        self.sustain_pct = sustain_pct
        self.release_ms = release_ms
        self.stage = EnvelopeStage.IDLE
        self.level = 0.0
        self._stage_elapsed_s = 0.0
        self._release_start_level = 0.0
        self._release_override_ms: float | None = None

    @property
    def is_active(self) -> bool:
        return self.stage != EnvelopeStage.IDLE

    def note_on(self) -> None:
        self.stage = EnvelopeStage.ATTACK
        self._stage_elapsed_s = 0.0

    def note_off(self, fade_ms: float | None = None) -> None:
        """fade_ms overrides this release only -- used for voice stealing
        and choke groups, which need a fixed ~5ms anti-click fade regardless
        of how long the track's own release_ms is set to."""
        if self.stage != EnvelopeStage.IDLE:
            self._release_start_level = self.level
            self._release_override_ms = fade_ms
            self.stage = EnvelopeStage.RELEASE
            self._stage_elapsed_s = 0.0

    def _advance(self, dt_seconds: float) -> float:
        if self.stage == EnvelopeStage.IDLE:
            self.level = 0.0
        elif self.stage == EnvelopeStage.ATTACK:
            total = max(1e-6, self.attack_ms / 1000.0)
            self._stage_elapsed_s += dt_seconds
            self.level = min(1.0, self._stage_elapsed_s / total)
            if self._stage_elapsed_s >= total:
                self.stage = EnvelopeStage.DECAY
                self._stage_elapsed_s = 0.0
        elif self.stage == EnvelopeStage.DECAY:
            sustain_level = self.sustain_pct / 100.0
            total = max(1e-6, self.decay_ms / 1000.0)
            self._stage_elapsed_s += dt_seconds
            frac = min(1.0, self._stage_elapsed_s / total)
            self.level = 1.0 + (sustain_level - 1.0) * frac
            if frac >= 1.0:
                self.stage = EnvelopeStage.SUSTAIN
        elif self.stage == EnvelopeStage.SUSTAIN:
            self.level = self.sustain_pct / 100.0
        elif self.stage == EnvelopeStage.RELEASE:
            if self._release_override_ms is not None:
                effective_release_ms = self._release_override_ms
            else:
                effective_release_ms = max(self.release_ms, MIN_RELEASE_MS)
            total = max(1e-6, effective_release_ms / 1000.0)
            self._stage_elapsed_s += dt_seconds
            frac = min(1.0, self._stage_elapsed_s / total)
            self.level = self._release_start_level * (1.0 - frac)
            if frac >= 1.0:
                self.stage = EnvelopeStage.IDLE
                self.level = 0.0
        return self.level

    def process_audio_rate(self, n_frames: int, sample_rate: int, out: np.ndarray) -> None:
        """Per-sample resolution -- used for the always-on amp envelope so
        short attack/release times stay click-free."""
        dt = 1.0 / sample_rate
        for i in range(n_frames):
            out[i] = self._advance(dt)

    def process_control_rate(self, n_frames: int, sample_rate: int) -> float:
        """One value for the whole block -- used for the FX rack's mod
        envelope, per docs/DAW_VISION_SPEC.md's control-rate modulation
        note."""
        dt = n_frames / sample_rate
        return self._advance(dt)


# ---------------------------------------------------------------------------
# LFO -- control-rate, one instance per voice
# ---------------------------------------------------------------------------

class LFOShape(Enum):
    SINE = "sine"
    TRIANGLE = "tri"
    SQUARE = "square"
    SAW = "saw"


class LFO:
    def __init__(self, rate_hz: float = 2.0, depth_pct: float = 0.0, shape: LFOShape = LFOShape.SINE) -> None:
        self.rate_hz = rate_hz
        self.depth_pct = depth_pct
        self.shape = shape
        self.phase = 0.0

    def reset_phase(self) -> None:
        """Called on voice note-on so each voice's LFO starts fresh."""
        self.phase = 0.0

    def _shape_value(self, phase: float) -> float:
        if self.shape == LFOShape.SINE:
            return math.sin(2.0 * math.pi * phase)
        if self.shape == LFOShape.TRIANGLE:
            return 2.0 * abs(2.0 * (phase - math.floor(phase + 0.5))) - 1.0
        if self.shape == LFOShape.SQUARE:
            return 1.0 if phase < 0.5 else -1.0
        # SAW: ramps -1 -> 1 across one cycle
        return 2.0 * phase - 1.0

    def process_control_rate(self, n_frames: int, sample_rate: int) -> float:
        dt = n_frames / sample_rate
        self.phase = (self.phase + self.rate_hz * dt) % 1.0
        return self._shape_value(self.phase) * (self.depth_pct / 100.0)


# ---------------------------------------------------------------------------
# Distortion -- stateless, per-track, in-place
# ---------------------------------------------------------------------------

def apply_distortion(block: np.ndarray, drive: float) -> None:
    if drive <= 0.0:
        return
    block *= drive
    np.tanh(block, out=block)


# ---------------------------------------------------------------------------
# Delay -- per-track, streaming, persistent state across blocks
# ---------------------------------------------------------------------------

class DelayLine:
    MAX_DELAY_MS = 2000.0

    def __init__(self, sample_rate: int = SAMPLE_RATE, delay_ms: float = 220.0,
                 feedback: float = 0.35, mix: float = 0.35) -> None:
        self._sample_rate = sample_rate
        max_samples = int(sample_rate * self.MAX_DELAY_MS / 1000.0) + 1
        self._buffer = np.zeros((max_samples, 2), dtype=np.float32)
        self._write_idx = 0
        self.delay_ms = delay_ms
        self.feedback = min(max(feedback, 0.0), 0.95)
        self.mix = min(max(mix, 0.0), 1.0)

    def process(self, block: np.ndarray) -> None:
        """Profiling during Phase 1 showed the original per-sample Python
        loop here was the single biggest cost in the engine (delay/reverb
        combined ~68% of callback time at 16 tracks, pushing the whole
        engine 3.7x over its real-time budget). A comb/delay filter with
        feedback isn't a good fit for scipy.signal.lfilter either (its
        coefficient vector would be `delay_samples` long -- thousands of
        mostly-zero taps -- and lfilter doesn't exploit that sparsity, so
        it'd cost O(delay_samples) per output sample instead of O(1)).

        The actual fix: whenever the delay is at least one block long (true
        for every default in this spec -- 220ms delay, 30-44ms reverb combs,
        vs. a ~10-21ms block), every read in this block comes from buffer
        content written in a *previous* block, so the whole block can be
        read/written as vectorized numpy slices instead of a per-sample
        loop. Falls back to the old per-sample loop only for the rare case
        of a delay shorter than one block."""
        buf = self._buffer
        buf_len = buf.shape[0]
        delay_samples = min(max(1, int(self._sample_rate * self.delay_ms / 1000.0)), buf_len - 1)
        write_idx = self._write_idx
        feedback = self.feedback
        mix = self.mix
        n = block.shape[0]

        if delay_samples >= n:
            read_start = (write_idx - delay_samples) % buf_len
            if read_start + n <= buf_len:
                delayed = buf[read_start:read_start + n].copy()
            else:
                first_len = buf_len - read_start
                delayed = np.concatenate([buf[read_start:], buf[:n - first_len]])

            new_vals = block + delayed * feedback
            if write_idx + n <= buf_len:
                buf[write_idx:write_idx + n] = new_vals
            else:
                first_len = buf_len - write_idx
                buf[write_idx:] = new_vals[:first_len]
                buf[:n - first_len] = new_vals[first_len:]

            block *= (1.0 - mix)
            block += delayed * mix
            write_idx = (write_idx + n) % buf_len
        else:
            for i in range(n):
                read_idx = (write_idx - delay_samples) % buf_len
                delayed0 = buf[read_idx, 0]
                delayed1 = buf[read_idx, 1]
                x0 = block[i, 0]
                x1 = block[i, 1]
                buf[write_idx, 0] = x0 + delayed0 * feedback
                buf[write_idx, 1] = x1 + delayed1 * feedback
                block[i, 0] = x0 * (1.0 - mix) + delayed0 * mix
                block[i, 1] = x1 * (1.0 - mix) + delayed1 * mix
                write_idx = (write_idx + 1) % buf_len

        self._write_idx = write_idx


# ---------------------------------------------------------------------------
# Reverb -- per-track, streaming Schroeder-style parallel combs
# ---------------------------------------------------------------------------

class ReverbComb:
    COMB_DELAYS_MS = (29.7, 37.1, 41.3, 43.7)

    def __init__(self, sample_rate: int = SAMPLE_RATE, decay: float = 0.5,
                 mix: float = 0.3, max_block_size: int = 4096) -> None:
        self.decay = min(max(decay, 0.0), 0.98)
        self.mix = min(max(mix, 0.0), 1.0)
        self._combs = []
        for delay_ms in self.COMB_DELAYS_MS:
            n = max(1, int(sample_rate * delay_ms / 1000.0))
            self._combs.append([np.zeros((n, 2), dtype=np.float32), 0])
        self._wet_scratch = np.zeros((max_block_size, 2), dtype=np.float32)

    def process(self, block: np.ndarray) -> None:
        """Same fast-path/fallback split as DelayLine.process, applied to
        each of the 4 comb filters -- this was the single largest cost
        found in Phase 1 profiling (~50% of callback time at 16 tracks).
        All 4 default comb delays (29.7-43.7ms) exceed a typical block
        (~10-21ms), so the vectorized path is the common case in practice."""
        n = block.shape[0]
        wet = self._wet_scratch[:n]
        wet[:] = 0.0
        decay = self.decay
        for comb in self._combs:
            buf, idx = comb
            buf_len = buf.shape[0]
            if buf_len >= n:
                if idx + n <= buf_len:
                    delayed = buf[idx:idx + n].copy()
                else:
                    first_len = buf_len - idx
                    delayed = np.concatenate([buf[idx:], buf[:n - first_len]])

                new_vals = block + delayed * decay
                if idx + n <= buf_len:
                    buf[idx:idx + n] = new_vals
                else:
                    first_len = buf_len - idx
                    buf[idx:] = new_vals[:first_len]
                    buf[:n - first_len] = new_vals[first_len:]

                wet += delayed
                idx = (idx + n) % buf_len
            else:
                for i in range(n):
                    delayed0 = buf[idx, 0]
                    delayed1 = buf[idx, 1]
                    buf[idx, 0] = block[i, 0] + delayed0 * decay
                    buf[idx, 1] = block[i, 1] + delayed1 * decay
                    wet[i, 0] += delayed0
                    wet[i, 1] += delayed1
                    idx = (idx + 1) % buf_len
            comb[1] = idx
        n_combs = len(self._combs)
        wet *= (self.mix / n_combs)
        block *= (1.0 - self.mix)
        block += wet


# ---------------------------------------------------------------------------
# Compressor -- per-track, audio-rate envelope follower
# ---------------------------------------------------------------------------

class Compressor:
    def __init__(self, sample_rate: int = SAMPLE_RATE, threshold_db: float = -18.0,
                 ratio: float = 4.0, attack_ms: float = 10.0, release_ms: float = 100.0,
                 makeup_db: float = 0.0) -> None:
        self._sample_rate = sample_rate
        self.threshold_db = threshold_db
        self.ratio = max(ratio, 1.0)
        self.attack_ms = attack_ms
        self.release_ms = release_ms
        self.makeup_db = makeup_db
        self._envelope_db = -100.0

    def process(self, block: np.ndarray) -> float:
        """In-place gain-reduces block; returns peak gain reduction in dB
        this block, for the mixer's GR meter.

        Profiling during Phase 1 found this was the biggest remaining cost
        after fixing delay/reverb -- not the envelope-follower recursion
        itself (which is genuinely sequential: attack/release coefficient
        depends on whether level is rising or falling, so it can't be
        expressed as a fixed-coefficient lfilter call), but the per-sample
        abs()/max()/log10() *Python function call* overhead surrounding it.
        Precomputing level detection as two vectorized numpy calls outside
        the loop, and leaving only the true recursion inside it, keeps the
        same algorithm while cutting that call overhead."""
        attack_coeff = _time_to_coeff(self.attack_ms, self._sample_rate)
        release_coeff = _time_to_coeff(self.release_ms, self._sample_rate)
        threshold = self.threshold_db
        one_minus_inv_ratio = 1.0 - 1.0 / self.ratio
        makeup_lin = db_to_linear(self.makeup_db)
        n = block.shape[0]

        peak = np.maximum(np.abs(block[:, 0]), np.abs(block[:, 1]))
        np.maximum(peak, 1e-9, out=peak)
        level_db = 20.0 * np.log10(peak)

        envelope_db = self._envelope_db
        gr_db_array = np.empty(n, dtype=np.float64)
        max_gr_db = 0.0
        for i in range(n):
            ld = level_db[i]
            if ld > envelope_db:
                envelope_db += (ld - envelope_db) * attack_coeff
            else:
                envelope_db += (ld - envelope_db) * release_coeff
            if envelope_db > threshold:
                gr_db = (envelope_db - threshold) * one_minus_inv_ratio
                if gr_db > max_gr_db:
                    max_gr_db = gr_db
            else:
                gr_db = 0.0
            gr_db_array[i] = gr_db
        self._envelope_db = envelope_db

        gain = (10.0 ** (-gr_db_array / 20.0)) * makeup_lin
        block[:, 0] *= gain
        block[:, 1] *= gain
        return max_gr_db


# ---------------------------------------------------------------------------
# Master bus brickwall limiter
# ---------------------------------------------------------------------------

class Limiter:
    """Fast-attack/release peak limiter followed by an unconditional hard
    clamp at the ceiling. The envelope follower keeps normal program
    material musical; the final np.clip is what actually makes the -0.3dBFS
    guarantee in docs/DAW_VISION_SPEC.md unconditional rather than
    best-effort -- it holds even if a single sample's transient outruns the
    envelope follower."""

    def __init__(self, sample_rate: int = SAMPLE_RATE, ceiling_db: float = MASTER_LIMITER_CEILING_DB,
                 attack_ms: float = 0.5, release_ms: float = 50.0) -> None:
        self.ceiling_linear = db_to_linear(ceiling_db)
        self._attack_coeff = _time_to_coeff(attack_ms, sample_rate)
        self._release_coeff = _time_to_coeff(release_ms, sample_rate)
        self._envelope = 0.0

    def process(self, block: np.ndarray) -> None:
        envelope = self._envelope
        ceiling = self.ceiling_linear
        attack_coeff = self._attack_coeff
        release_coeff = self._release_coeff
        n = block.shape[0]

        peak_array = np.maximum(np.abs(block[:, 0]), np.abs(block[:, 1]))
        gain_array = np.empty(n, dtype=np.float64)
        for i in range(n):
            peak = peak_array[i]
            if peak > envelope:
                envelope += (peak - envelope) * attack_coeff
            else:
                envelope += (peak - envelope) * release_coeff
            gain_array[i] = ceiling / envelope if envelope > ceiling else 1.0
        self._envelope = envelope

        block[:, 0] *= gain_array
        block[:, 1] *= gain_array
        # Unconditional hard safety clamp -- see class docstring: this is
        # what makes the -0.3dBFS guarantee actually unconditional.
        np.clip(block, -ceiling, ceiling, out=block)
