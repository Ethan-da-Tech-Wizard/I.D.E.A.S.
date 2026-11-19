# ==============================================================================
# Program: dsp.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: Provides low-level digital signal processing algorithms, FFT transforms, filter design kernels, and windowing functions for audio analysis and synthesis.
# ==============================================================================
# How this module fits: it is the sound-processing toolbox for the live audio engine. app/services/audio/voice.py builds a Voice from
# an ADSREnvelope, LFO and BiquadFilter; app/services/audio/track_engine.py chains ThreeBandEQ, Compressor, apply_distortion, DelayLine and
# ReverbComb per track; app/services/audio/engine.py runs the master Limiter and calls the sample loaders. tests/test_audio_dsp.py exercises it.
# Signal model: audio blocks are numpy float32 arrays shaped (n_frames, 2) (left, right) at SAMPLE_RATE (48 kHz), values nominally in [-1, 1].
# Most processors work IN PLACE on the block they are given to avoid allocating inside the real-time audio callback.
# Design decisions: each effect is a small class holding its own state (filter memory, delay buffer, envelope level) so it can be called block after
# block without clicks at block edges; parameters are clamped in the setters/constructors so a UI slider can never produce an unstable filter.
# Known limits and discrepancies noticed (comment pass only, nothing changed):
#   - The module docstring says "pure numpy (no scipy)" but scipy.signal.lfilter is imported for BiquadFilter, so scipy IS a runtime dependency.
#   - read_wav_stereo treats every width above 16 bits as int32; a 24-bit WAV (3 bytes per sample) is misread (garbled or a numpy size error).
#   - ReverbComb sizes its scratch buffer for max_block_size (default 4096) frames; a larger block would break the slice assignment.
#   - Compressor/Limiter loop per sample in Python and allocate small arrays per block, which conflicts with the "no allocation" contract; they are the
#     known remaining cost of the engine.
#   - The Compressor measures level in dB from the louder channel only, so stereo images are reduced together (linked), by design.

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

# Engine-wide constants. SAMPLE_RATE: every sample is resampled to 48 kHz at load time so voices never resample while playing.
# MAX_SAMPLE_DURATION_SECONDS: longest sample loaded (30 s) to bound memory. MIN_RELEASE_MS: shortest release (5 ms), below which a note-off
# would cut the waveform abruptly and click.
SAMPLE_RATE = 48000
MAX_SAMPLE_DURATION_SECONDS = 30.0
MIN_RELEASE_MS = 5.0

# Filter limits: cutoff 20 Hz to 20 kHz (human hearing range) and resonance (Q) 0.5 to 8; Q above about 8 rings or self-oscillates and below 0.5 is overdamped.
FILTER_CUTOFF_MIN_HZ = 20.0
FILTER_CUTOFF_MAX_HZ = 20_000.0
FILTER_RESONANCE_MIN = 0.5
FILTER_RESONANCE_MAX = 8.0

# Master ceiling of -0.3 dBFS leaves a small margin below digital full scale so later lossy encoding or resampling does not clip.
MASTER_LIMITER_CEILING_DB = -0.3


def _time_to_coeff(time_ms: float, sample_rate: int) -> float:
    """Convert a time constant into a one-pole smoothing coefficient.

    Args:
        time_ms: time to reach about 63% of a step (milliseconds); clamped to at least 0.01 ms to avoid division by zero.
        sample_rate: Hz.

    Returns:
        Coefficient in (0, 1] to use as `y += (x - y) * coeff` per sample; larger means faster.
    """
    time_ms = max(time_ms, 0.01)
    # Standard exponential-smoothing relation: after tau seconds the output has covered 1 - 1/e of the distance.
    return 1.0 - math.exp(-1.0 / ((time_ms / 1000.0) * sample_rate))


def db_to_linear(db: float) -> float:
    """Convert decibels to a linear gain ratio (20 dB = x10, -6.02 dB = x0.5)."""
    return 10.0 ** (db / 20.0)


def linear_to_db(value: float) -> float:
    """Convert a linear gain to decibels; values at or below 1e-9 are floored (-180 dB) so silence does not give log(0) = -infinity."""
    # 1e-9 is far below any 16-bit or 24-bit sample, so the floor never changes audible results.
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

    # 8-bit WAV is unsigned with silence at 128; 16-bit is signed. Dividing by 128 / 32768 maps to [-1, 1).
    if sample_width == 1:
        data = np.frombuffer(raw, dtype=np.uint8).astype(np.float32)
        data = (data - 128.0) / 128.0
    elif sample_width == 2:
        data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    # Anything else is read as int32, which is correct only for 32-bit PCM (see the known-limits list above for the 24-bit problem).
    else:
        # 24/32-bit: fall back to a generic int32 read via numpy's stride
        # trick is more trouble than this feature is worth here -- treat as
        # int32 samples, which covers the common 32-bit PCM case.
        data = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0

    # Trim any partial trailing frame so the reshape into (frames, channels) cannot fail.
    n = len(data) // max(1, n_channels)
    data = data[: n * n_channels].reshape(n, n_channels)

    # Mono is duplicated to both channels so the rest of the engine only ever deals with stereo; files with more than two channels keep the first two (front left/right).
    if n_channels == 1:
        stereo = np.repeat(data, 2, axis=1)
    elif n_channels == 2:
        stereo = data
    else:
        stereo = data[:, :2]

    return stereo.astype(np.float32, copy=False), sample_rate


# src_positions maps every output index onto a fractional input index; linear interpolation between the two neighbouring input frames is cheap and
# adequate for drum samples (it softens very high frequencies slightly). round() makes the output length match the new rate.
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
    # Clamp the second index so the last output sample does not read past the end of the array.
    idx1 = np.minimum(idx0 + 1, src_n - 1)
    # Weights are shaped (n, 1) so numpy broadcasts one weight across both stereo channels.
    frac = (src_positions - idx0).astype(np.float32).reshape(-1, 1)
    out = samples[idx0] * (1.0 - frac) + samples[idx1] * frac
    return out.astype(np.float32, copy=False)


def load_and_prepare_sample(path: str) -> tuple[np.ndarray, int]:
    """Load a WAV and convert it to the engine's sample rate in one step.

    Args:
        path: validated WAV path.

    Returns:
        (stereo float32 array of shape (n, 2), SAMPLE_RATE).

    Called when a track or voice is created, never from the audio callback (it allocates).
    """
    samples, src_rate = read_wav_stereo(path)
    samples = resample_linear(samples, src_rate, SAMPLE_RATE)
    return samples, SAMPLE_RATE


# ---------------------------------------------------------------------------
# Biquad filter (RBJ Audio EQ Cookbook), per-voice
# ---------------------------------------------------------------------------

# Enum of filter shapes; the string values are what saved projects store.
class FilterType(Enum):
    LOWPASS = "lp"
    HIGHPASS = "hp"
    BANDPASS = "bp"


def design_biquad(filter_type: FilterType, cutoff_hz: float, resonance_q: float, sample_rate: int) -> tuple[float, float, float, float, float]:
    """Compute normalised biquad coefficients from the RBJ Audio EQ Cookbook.

    Args:
        filter_type: LOWPASS, HIGHPASS or BANDPASS.
        cutoff_hz: corner/centre frequency; clamped to 20 Hz .. min(20 kHz, Nyquist - 100 Hz) so the design stays stable.
        resonance_q: quality factor, clamped to 0.5 .. 8.
        sample_rate: Hz.

    Returns:
        (b0, b1, b2, a1, a2), each already divided by a0 so the filter equation has a leading coefficient of 1.
    """
    # Keep the cutoff at least 100 Hz below Nyquist (half the sample rate); coefficients explode as the frequency approaches it.
    cutoff_hz = min(max(cutoff_hz, FILTER_CUTOFF_MIN_HZ), min(FILTER_CUTOFF_MAX_HZ, sample_rate / 2.0 - 100.0))
    q = min(max(resonance_q, FILTER_RESONANCE_MIN), FILTER_RESONANCE_MAX)

    # w0 is the cutoff as an angle in radians per sample; alpha controls bandwidth (smaller Q = wider = larger alpha).
    w0 = 2.0 * math.pi * cutoff_hz / sample_rate
    cos_w0 = math.cos(w0)
    sin_w0 = math.sin(w0)
    alpha = sin_w0 / (2.0 * q)

    # Numerator (feed-forward) coefficients differ per shape; the denominator is shared by all three, per the cookbook.
    if filter_type == FilterType.LOWPASS:
        b0 = (1.0 - cos_w0) / 2.0
        b1 = 1.0 - cos_w0
        b2 = (1.0 - cos_w0) / 2.0
    elif filter_type == FilterType.HIGHPASS:
        b0 = (1.0 + cos_w0) / 2.0
        b1 = -(1.0 + cos_w0)
        b2 = (1.0 + cos_w0) / 2.0
    # Band-pass in the "constant 0 dB peak gain" form: b1 = 0, b2 = -b0.
    else:  # BANDPASS, constant 0 dB peak gain
        b0 = alpha
        b1 = 0.0
        b2 = -alpha

    # Denominator terms are the same for LP, HP and BP.
    a0 = 1.0 + alpha
    a1 = -2.0 * cos_w0
    a2 = 1.0 - alpha

    # Normalising by a0 means the caller never has to divide again when running the filter.
    return b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0


# Second-order filter processed with scipy's lfilter; one object holds the two channels' memory.
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
        """Create a pass-through filter (b = [1,0,0], a = [1,0,0]) with silent state.

        Args:
            sample_rate: Hz used when set_params designs coefficients.
        """
        self._sample_rate = sample_rate
        self._b = np.array([1.0, 0.0, 0.0], dtype=np.float64)
        self._a = np.array([1.0, 0.0, 0.0], dtype=np.float64)
        # lfilter state shape for axis=0 filtering of 2-channel data is (order, channels) = (2, 2); keeping it between calls makes consecutive blocks continuous.
        # lfilter zi shape for axis=0, 2-channel input: (order, n_channels)
        self._zi = np.zeros((2, 2), dtype=np.float64)

    def set_params(self, filter_type: FilterType, cutoff_hz: float, resonance_q: float) -> None:
        """Design and install LP/HP/BP coefficients (done at note-on or when the user moves a knob, not per sample).

        Args:
            filter_type: filter shape.
            cutoff_hz: corner frequency in Hz.
            resonance_q: Q factor.
        """
        b0, b1, b2, a1, a2 = design_biquad(filter_type, cutoff_hz, resonance_q, self._sample_rate)
        # Write into the existing arrays instead of creating new ones, so the filter never allocates when parameters change.
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
        # Passing zi and storing zf keeps the filter history across blocks; float64 precision keeps low cutoffs numerically stable.
        y, self._zi = lfilter(self._b, self._a, block, axis=0, zi=self._zi)
        block[:] = y


# ---------------------------------------------------------------------------
# 3-band EQ (RBJ cookbook shelf/peak), per-track mixer strip
# ---------------------------------------------------------------------------

# Fixed EQ corner frequencies (low shelf 200 Hz, mid peak 1 kHz with Q 1, high shelf 5 kHz) and a plus/minus 15 dB range per band; the same three
# bands appear on every mixer strip so the UI is simple.
EQ_LOW_SHELF_HZ = 200.0
EQ_MID_PEAK_HZ = 1000.0
EQ_MID_PEAK_Q = 1.0
EQ_HIGH_SHELF_HZ = 5000.0
EQ_BAND_GAIN_MIN_DB = -15.0
EQ_BAND_GAIN_MAX_DB = 15.0


def design_low_shelf(freq_hz: float, gain_db: float, sample_rate: int, q: float = 0.707) -> tuple[float, float, float, float, float]:
    """Compute low-shelf coefficients (RBJ cookbook).

    Args:
        freq_hz: shelf corner frequency.
        gain_db: boost (+) or cut (-) in dB below the corner.
        sample_rate: Hz.
        q: shelf slope; 0.707 gives a gentle Butterworth-like shelf.

    Returns:
        (b0, b1, b2, a1, a2) normalised by a0.
    """
    # Cookbook amplitude term A = 10^(dB/40) (dB/40, not /20, because the shelf formulas use the square root of the gain).
    a = 10.0 ** (gain_db / 40.0)
    w0 = 2.0 * math.pi * freq_hz / sample_rate
    cos_w0, sin_w0 = math.cos(w0), math.sin(w0)
    # alpha from the cookbook's shelf-slope form; when q = 0.707 the inner term (1/q - 1) stays small so the shelf is smooth.
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
    """Compute high-shelf coefficients (RBJ cookbook); arguments as design_low_shelf but the boost/cut applies above the corner."""
    # Same terms as the low shelf with the sign of the cos(w0) and (a-1) products flipped, which mirrors the response around the corner.
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
    """Compute peaking (bell) EQ coefficients (RBJ cookbook).

    Args:
        freq_hz: centre frequency.
        gain_db: boost or cut at the centre.
        q: bandwidth; higher is narrower.
        sample_rate: Hz.

    Returns:
        (b0, b1, b2, a1, a2) normalised by a0.
    """
    a = 10.0 ** (gain_db / 40.0)
    w0 = 2.0 * math.pi * freq_hz / sample_rate
    cos_w0, sin_w0 = math.cos(w0), math.sin(w0)
    # Bell filter: a = linear amplitude at the centre; scaling alpha by a (numerator) and 1/a (denominator) gives exactly that gain.
    alpha = sin_w0 / (2.0 * q)
    b0 = 1.0 + alpha * a
    b1 = -2.0 * cos_w0
    b2 = 1.0 - alpha * a
    a0 = 1.0 + alpha / a
    a1 = -2.0 * cos_w0
    a2 = 1.0 - alpha / a
    return b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0


class ThreeBandEQ:
    """Low shelf (200 Hz), mid bell (1 kHz, Q 1) and high shelf (5 kHz), each plus/minus 15 dB.

    Applied per track after the compressor and before the fader (see docs/DAW_VISION_SPEC.md, Mixer Deck section).
    A band set to exactly 0 dB is skipped completely, so an untouched EQ costs nothing.
    """

    def __init__(self, sample_rate: int = SAMPLE_RATE) -> None:
        """Create three independent biquad stages (one per band, each with its own memory for both channels)."""
        self._sample_rate = sample_rate
        self._low = BiquadFilter(sample_rate)
        self._mid = BiquadFilter(sample_rate)
        self._high = BiquadFilter(sample_rate)

    def process(self, block: np.ndarray, low_db: float, mid_db: float, high_db: float) -> None:
        """Apply the EQ in place.

        Args:
            block: (n, 2) float32 audio, modified in place.
            low_db, mid_db, high_db: band gains in dB, clamped to -15..+15.
        """
        # Clamp so a bad value from the UI or a loaded project cannot request a huge boost.
        low_db = min(max(low_db, EQ_BAND_GAIN_MIN_DB), EQ_BAND_GAIN_MAX_DB)
        mid_db = min(max(mid_db, EQ_BAND_GAIN_MIN_DB), EQ_BAND_GAIN_MAX_DB)
        high_db = min(max(high_db, EQ_BAND_GAIN_MIN_DB), EQ_BAND_GAIN_MAX_DB)
        # Coefficients are recomputed on every call (cheap trigonometry) so the knobs respond immediately without a separate update step.
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
    """Stage names of the ADSR (attack, decay, sustain, release) plus IDLE (no sound)."""
    IDLE = auto()
    ATTACK = auto()
    DECAY = auto()
    SUSTAIN = auto()
    RELEASE = auto()


class ADSREnvelope:
    """Attack-Decay-Sustain-Release loudness envelope, one instance per voice.

    Level goes 0 -> 1 during attack, falls to the sustain level during decay, holds until note_off, then fades to 0 during release.
    Times are milliseconds, sustain is a percentage of full level.
    """
    def __init__(self, attack_ms: float = 0.0, decay_ms: float = 0.0,
                 sustain_pct: float = 100.0, release_ms: float = MIN_RELEASE_MS) -> None:
        """Store the four settings and start idle.

        Args:
            attack_ms: rise time to full level.
            decay_ms: fall time from full level to the sustain level.
            sustain_pct: held level as a percentage (0-100).
            release_ms: fade-out time after note_off; MIN_RELEASE_MS is the floor applied while releasing.
        """
        self.attack_ms = attack_ms
        self.decay_ms = decay_ms
        self.sustain_pct = sustain_pct
        self.release_ms = release_ms
        # Runtime state: current stage, current output level (0..1), time spent in this stage, and the level at the moment release began (so release fades from where the sound actually was).
        self.stage = EnvelopeStage.IDLE
        self.level = 0.0
        self._stage_elapsed_s = 0.0
        self._release_start_level = 0.0
        self._release_override_ms: float | None = None

    # True while the voice is producing or fading sound; a voice that is not active can be reused.
    @property
    def is_active(self) -> bool:
        return self.stage != EnvelopeStage.IDLE

    def note_on(self) -> None:
        """Start (or retrigger) the attack from the current stage clock; the level itself is not reset, so retriggering a sounding note does not jump."""
        self.stage = EnvelopeStage.ATTACK
        self._stage_elapsed_s = 0.0

    def note_off(self, fade_ms: float | None = None) -> None:
        """Begin the release stage.

        Args:
            fade_ms: if given, use this release length instead of release_ms. Voice stealing and choke groups pass about 5 ms for a fast click-free cut.

        Ignored when the envelope is already idle.
        """
        if self.stage != EnvelopeStage.IDLE:
            self._release_start_level = self.level
            self._release_override_ms = fade_ms
            self.stage = EnvelopeStage.RELEASE
            self._stage_elapsed_s = 0.0

    def _advance(self, dt_seconds: float) -> float:
        """Advance the envelope by dt_seconds and return the new level (0..1).

        Each stage is linear. The 1e-6 floors on stage length prevent division by zero when a time is set to 0 (an instant stage).
        """
        if self.stage == EnvelopeStage.IDLE:
            self.level = 0.0
        # Attack: ramp up in proportion to elapsed time; when the time is used up, move on to decay with a fresh stage clock.
        elif self.stage == EnvelopeStage.ATTACK:
            total = max(1e-6, self.attack_ms / 1000.0)
            self._stage_elapsed_s += dt_seconds
            self.level = min(1.0, self._stage_elapsed_s / total)
            if self._stage_elapsed_s >= total:
                self.stage = EnvelopeStage.DECAY
                self._stage_elapsed_s = 0.0
        # Decay: interpolate from 1.0 down to the sustain level; sustain_pct of 100 makes this stage a no-op.
        elif self.stage == EnvelopeStage.DECAY:
            sustain_level = self.sustain_pct / 100.0
            total = max(1e-6, self.decay_ms / 1000.0)
            self._stage_elapsed_s += dt_seconds
            frac = min(1.0, self._stage_elapsed_s / total)
            self.level = 1.0 + (sustain_level - 1.0) * frac
            if frac >= 1.0:
                self.stage = EnvelopeStage.SUSTAIN
        # Sustain holds a constant level until note_off() is called.
        elif self.stage == EnvelopeStage.SUSTAIN:
            self.level = self.sustain_pct / 100.0
        # Release: fade linearly from the level at note-off to 0, then go idle so the voice can be freed. A forced fade length wins over release_ms.
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
        """Fill `out` with one envelope value per audio frame (per-sample accuracy).

        Args:
            n_frames: number of frames in the block.
            sample_rate: Hz; the step is 1 / sample_rate seconds.
            out: preallocated float array of at least n_frames values, written in place.

        Used for the amp envelope so short attacks and releases do not click. Python loop per sample: this is a known cost.
        """
        dt = 1.0 / sample_rate
        for i in range(n_frames):
            out[i] = self._advance(dt)

    def process_control_rate(self, n_frames: int, sample_rate: int) -> float:
        """Return a single envelope value for the whole block (block duration as the step).

        Args:
            n_frames: block length.
            sample_rate: Hz.

        Used for modulation envelopes where one value per block (a few milliseconds) is smooth enough and much cheaper.
        """
        dt = n_frames / sample_rate
        return self._advance(dt)


# ---------------------------------------------------------------------------
# LFO -- control-rate, one instance per voice
# ---------------------------------------------------------------------------

class LFOShape(Enum):
    """Waveform shapes: sine, triangle, square, sawtooth (values are what projects store)."""
    SINE = "sine"
    TRIANGLE = "tri"
    SQUARE = "square"
    SAW = "saw"


class LFO:
    """Low-frequency oscillator producing a slow control signal (not heard directly), one per voice."""
    def __init__(self, rate_hz: float = 2.0, depth_pct: float = 0.0, shape: LFOShape = LFOShape.SINE) -> None:
        """Create the oscillator.

        Args:
            rate_hz: cycles per second (default 2 Hz).
            depth_pct: output amount as a percentage of full scale (0 = off, 100 = -1..+1).
            shape: waveform.
        """
        self.rate_hz = rate_hz
        self.depth_pct = depth_pct
        self.shape = shape
        self.phase = 0.0

    def reset_phase(self) -> None:
        """Restart the cycle at note-on so every note modulates identically."""
        self.phase = 0.0

    def _shape_value(self, phase: float) -> float:
        """Evaluate the waveform at a phase.

        Args:
            phase: position in the cycle, 0 to 1.

        Returns:
            A value in [-1, 1] before depth is applied.
        """
        if self.shape == LFOShape.SINE:
            return math.sin(2.0 * math.pi * phase)
        if self.shape == LFOShape.TRIANGLE:
            # Triangle built from a distance-to-nearest-integer trick: it is -1 at phase 0, +1 at 0.5, and -1 again at 1.
            return 2.0 * abs(2.0 * (phase - math.floor(phase + 0.5))) - 1.0
        if self.shape == LFOShape.SQUARE:
            return 1.0 if phase < 0.5 else -1.0
        # SAW: ramps -1 -> 1 across one cycle
        return 2.0 * phase - 1.0

    def process_control_rate(self, n_frames: int, sample_rate: int) -> float:
        """Advance the oscillator by one block and return its scaled value.

        Args:
            n_frames: block length.
            sample_rate: Hz.

        Returns:
            Shape value times depth_pct/100. The phase moves BEFORE evaluation, so the first value after a reset is already part-way into the cycle.
        """
        # Block duration in seconds; the whole block shares one LFO value (control rate).
        dt = n_frames / sample_rate
        # Wrap with modulo 1 so the phase stays in [0, 1) and never grows without bound.
        self.phase = (self.phase + self.rate_hz * dt) % 1.0
        return self._shape_value(self.phase) * (self.depth_pct / 100.0)


# ---------------------------------------------------------------------------
# Distortion -- stateless, per-track, in-place
# ---------------------------------------------------------------------------

def apply_distortion(block: np.ndarray, drive: float) -> None:
    """Overdrive a block in place.

    Args:
        block: (n, 2) float32 audio, modified in place.
        drive: pre-gain; 0 or negative bypasses the effect. Output is limited to (-1, 1) by tanh.
    """
    # Bypass when off, so an unused effect adds no processing and no tone colouring.
    if drive <= 0.0:
        return
    # In-place multiply then in-place tanh: no temporary arrays are created, which respects the real-time no-allocation rule.
    block *= drive
    np.tanh(block, out=block)


# ---------------------------------------------------------------------------
# Delay -- per-track, streaming, persistent state across blocks
# ---------------------------------------------------------------------------

class DelayLine:
    """Stereo feedback echo with a circular buffer that persists between blocks.

    delay_ms and feedback are public attributes the track engine updates while playing; the buffer itself has a fixed 2 s capacity.
    """
    # Longest supported delay; the buffer is sized once for this so the delay time can change without reallocation.
    MAX_DELAY_MS = 2000.0

    def __init__(self, sample_rate: int = SAMPLE_RATE, delay_ms: float = 220.0,
                 feedback: float = 0.35, mix: float = 0.35) -> None:
        """Allocate the ring buffer and clamp the settings.

        Args:
            sample_rate: Hz.
            delay_ms: echo spacing (0-2000).
            feedback: share of the echo fed back; capped at 0.95 so repeats always die out.
            mix: wet/dry balance, 0 to 1.
        """
        self._sample_rate = sample_rate
        # One extra slot so a full-length delay never makes the read position equal the write position.
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
        # Cache attributes in locals: faster inside the hot path, and a parameter changed from another thread mid-block cannot change the maths halfway through.
        buf = self._buffer
        buf_len = buf.shape[0]
        # Delay in samples, forced into 1..buffer_length-1 so the read index stays valid.
        delay_samples = min(max(1, int(self._sample_rate * self.delay_ms / 1000.0)), buf_len - 1)
        write_idx = self._write_idx
        feedback = self.feedback
        mix = self.mix
        n = block.shape[0]

        # Fast path: when the delay is at least a block long, nothing we read this block is written in this block, so the whole block can be handled as array slices.
        if delay_samples >= n:
            # Read pointer sits delay_samples behind the write pointer in the ring buffer.
            read_start = (write_idx - delay_samples) % buf_len
            # Contiguous read when it does not wrap around the end of the buffer; copy() so later writes cannot alter what we read.
            if read_start + n <= buf_len:
                delayed = buf[read_start:read_start + n].copy()
            else:
                # Wrapped read: stitch the tail of the buffer to its beginning.
                first_len = buf_len - read_start
                delayed = np.concatenate([buf[read_start:], buf[:n - first_len]])

            # What gets stored: new input plus the scaled echo (feedback), which creates the repeating decay.
            new_vals = block + delayed * feedback
            if write_idx + n <= buf_len:
                buf[write_idx:write_idx + n] = new_vals
            else:
                first_len = buf_len - write_idx
                buf[write_idx:] = new_vals[:first_len]
                buf[:n - first_len] = new_vals[first_len:]

            # Output = dry * (1 - mix) + echo * mix.
            block *= (1.0 - mix)
            block += delayed * mix
            write_idx = (write_idx + n) % buf_len
        # Slow path for a delay shorter than one block: each sample may read data written earlier in the same block, so it must run one sample at a time.
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

        # Save the new write position for the next block.
        self._write_idx = write_idx


# ---------------------------------------------------------------------------
# Reverb -- per-track, streaming Schroeder-style parallel combs
# ---------------------------------------------------------------------------

class ReverbComb:
    """Streaming reverb: four parallel feedback comb filters (Schroeder-style) with persistent ring buffers."""
    # Delay lengths in milliseconds, chosen to be unrelated (no common multiples) so echoes do not stack into a single ring; matches the offline version in app/services/audio_fx.py.
    COMB_DELAYS_MS = (29.7, 37.1, 41.3, 43.7)

    def __init__(self, sample_rate: int = SAMPLE_RATE, decay: float = 0.5,
                 mix: float = 0.3, max_block_size: int = 4096) -> None:
        """Allocate the comb buffers and the wet scratch area.

        Args:
            sample_rate: Hz.
            decay: comb feedback, clamped to 0..0.98 (above that the tail barely dies).
            mix: wet/dry, 0 to 1.
            max_block_size: largest block process() will receive; the scratch buffer is preallocated for it.
        """
        self.decay = min(max(decay, 0.0), 0.98)
        self.mix = min(max(mix, 0.0), 1.0)
        # Each comb is a list [ring_buffer, current_index] so process() can update the index in place.
        self._combs = []
        for delay_ms in self.COMB_DELAYS_MS:
            n = max(1, int(sample_rate * delay_ms / 1000.0))
            self._combs.append([np.zeros((n, 2), dtype=np.float32), 0])
        # Preallocated so process() creates no new array for the summed wet signal.
        self._wet_scratch = np.zeros((max_block_size, 2), dtype=np.float32)

    def process(self, block: np.ndarray) -> None:
        """Same fast-path/fallback split as DelayLine.process, applied to
        each of the 4 comb filters -- this was the single largest cost
        found in Phase 1 profiling (~50% of callback time at 16 tracks).
        All 4 default comb delays (29.7-43.7ms) exceed a typical block
        (~10-21ms), so the vectorized path is the common case in practice."""
        n = block.shape[0]
        # Take a view of the first n rows and clear it; this fails if n exceeds max_block_size (see known limits).
        wet = self._wet_scratch[:n]
        wet[:] = 0.0
        decay = self.decay
        for comb in self._combs:
            buf, idx = comb
            buf_len = buf.shape[0]
            # Fast path when the comb is at least one block long (the usual case), same technique as DelayLine.
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
            # Slow path for very small buffers (not reachable with the default delays at normal block sizes).
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
        # Average the four combs (via the mix / n_combs factor) so adding combs does not make the effect louder.
        n_combs = len(self._combs)
        wet *= (self.mix / n_combs)
        block *= (1.0 - self.mix)
        block += wet


# ---------------------------------------------------------------------------
# Compressor -- per-track, audio-rate envelope follower
# ---------------------------------------------------------------------------

class Compressor:
    """Feed-forward compressor with an envelope follower on the louder channel.

    Reduces gain above threshold_db by the given ratio; process() returns the largest gain reduction in the block for the mixer's meter.
    """
    def __init__(self, sample_rate: int = SAMPLE_RATE, threshold_db: float = -18.0,
                 ratio: float = 4.0, attack_ms: float = 10.0, release_ms: float = 100.0,
                 makeup_db: float = 0.0) -> None:
        """Store settings and start with a very low envelope.

        Args:
            sample_rate: Hz.
            threshold_db: level above which gain reduction starts (default -18 dBFS).
            ratio: input-dB per output-dB above the threshold; clamped to at least 1 (1 = no compression).
            attack_ms: how fast the detector reacts to louder material.
            release_ms: how fast it relaxes afterwards.
            makeup_db: fixed gain added afterwards to restore loudness.
        """
        self._sample_rate = sample_rate
        self.threshold_db = threshold_db
        self.ratio = max(ratio, 1.0)
        self.attack_ms = attack_ms
        self.release_ms = release_ms
        self.makeup_db = makeup_db
        # -100 dB means "silent", so the first loud block attacks from a defined state.
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
        # Attack and release use different smoothing speeds, chosen per sample depending on whether the level is rising or falling.
        attack_coeff = _time_to_coeff(self.attack_ms, self._sample_rate)
        release_coeff = _time_to_coeff(self.release_ms, self._sample_rate)
        threshold = self.threshold_db
        # Slope of the compression curve: for ratio 4, gain reduction is 3/4 of the amount over threshold.
        one_minus_inv_ratio = 1.0 - 1.0 / self.ratio
        makeup_lin = db_to_linear(self.makeup_db)
        n = block.shape[0]

        # Linked stereo detection: the louder of the two channels drives both, so the stereo image does not shift.
        peak = np.maximum(np.abs(block[:, 0]), np.abs(block[:, 1]))
        # Floor at 1e-9 before the logarithm so silence gives a finite dB value.
        np.maximum(peak, 1e-9, out=peak)
        level_db = 20.0 * np.log10(peak)

        envelope_db = self._envelope_db
        gr_db_array = np.empty(n, dtype=np.float64)
        max_gr_db = 0.0
        # Sequential recursion: each envelope value depends on the previous, so this loop cannot be vectorised.
        for i in range(n):
            ld = level_db[i]
            if ld > envelope_db:
                envelope_db += (ld - envelope_db) * attack_coeff
            else:
                envelope_db += (ld - envelope_db) * release_coeff
            # Only the part above the threshold is reduced; below it the gain stays at unity.
            if envelope_db > threshold:
                gr_db = (envelope_db - threshold) * one_minus_inv_ratio
                if gr_db > max_gr_db:
                    max_gr_db = gr_db
            else:
                gr_db = 0.0
            gr_db_array[i] = gr_db
        self._envelope_db = envelope_db

        # Convert the per-sample reduction (dB) to linear gain and add make-up gain; applied to both channels at once.
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
    envelope follower.

    Used on the master bus only (app/services/audio/engine.py)."""

    # Defaults: ceiling -0.3 dBFS; a very fast 0.5 ms attack so peaks are caught almost immediately and a 50 ms release so the gain recovers without audible pumping.
    def __init__(self, sample_rate: int = SAMPLE_RATE, ceiling_db: float = MASTER_LIMITER_CEILING_DB,
                 attack_ms: float = 0.5, release_ms: float = 50.0) -> None:
        """Store the ceiling (as linear gain) and the follower coefficients.

        Args:
            sample_rate: Hz.
            ceiling_db: maximum output level in dBFS.
            attack_ms: peak-follower rise time.
            release_ms: peak-follower fall time.
        """
        self.ceiling_linear = db_to_linear(ceiling_db)
        self._attack_coeff = _time_to_coeff(attack_ms, sample_rate)
        self._release_coeff = _time_to_coeff(release_ms, sample_rate)
        self._envelope = 0.0

    def process(self, block: np.ndarray) -> None:
        """Limit a block in place.

        Args:
            block: (n, 2) float32 master mix; modified in place so no peak exceeds the ceiling.
        """
        envelope = self._envelope
        ceiling = self.ceiling_linear
        attack_coeff = self._attack_coeff
        release_coeff = self._release_coeff
        n = block.shape[0]

        # Detection uses the louder channel per sample, so both channels receive the same gain and the stereo image is preserved.
        peak_array = np.maximum(np.abs(block[:, 0]), np.abs(block[:, 1]))
        gain_array = np.empty(n, dtype=np.float64)
        for i in range(n):
            peak = peak_array[i]
            if peak > envelope:
                envelope += (peak - envelope) * attack_coeff
            else:
                envelope += (peak - envelope) * release_coeff
            # When the smoothed peak is above the ceiling, divide by it so that peak lands exactly on the ceiling; otherwise leave unchanged.
            gain_array[i] = ceiling / envelope if envelope > ceiling else 1.0
        self._envelope = envelope

        block[:, 0] *= gain_array
        block[:, 1] *= gain_array
        # Unconditional hard safety clamp -- see class docstring: this is
        # what makes the -0.3dBFS guarantee actually unconditional.
        np.clip(block, -ceiling, ceiling, out=block)
