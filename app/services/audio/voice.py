# ==============================================================================
# Program: voice.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: Implements polyphonic virtual synthesizer voice allocation, ADSR envelope generation, oscillator wave tables, and filter modulation.
# ==============================================================================
# How this module fits: TrackProcessor.trigger_note() (track_engine.py) creates one Voice per played note and the engine's sequencer holds on to it so
# it can call note_off() later. Each audio block, TrackProcessor.process_block() calls voice.render() and sums the results. A Voice reads the track's sample
# (resampled to 48 kHz by dsp.load_and_prepare_sample) at a speed set by the pitch, optionally filters it, and shapes its loudness with the envelope.
# Processing order inside render(): modulation sources (LFO, modulation envelope) -> pitch -> sample read with linear interpolation -> optional filter
# -> loudness envelope (with optional tremolo). Inputs: stereo float32 sample array and a VoiceParams snapshot. Output: (frames, 2) float written into a caller buffer.
# Design decisions: sounds are "vari-speed" (pitch and speed change together, like a tape) because that needs no FFT; modulation values are computed once per block
# (control rate) while the loudness envelope is per sample so short attacks do not click.
# Known limits noticed (comment pass only):
#   - Loop playback: the jump back happens when the read position passes the TRIM end, not the loop end, and it lands at loop_start + (pos - loop_end).
#     With the defaults (loop 0.25-0.75, trim end 1.0) the audible loop therefore covers the whole tail of the sample, not just 25%-75%.
#   - Tremolo: np.clip(1 + lfo, 0, 1) can only reduce gain, so the positive half of the LFO cycle is flat; only the negative half dips the volume.
#   - render() assumes n_frames <= max_block_size (the envelope scratch buffer length).
#   - _read_source is a per-sample Python loop: simple but a major CPU cost with many voices (the engine's overload shedding exists for this reason).
#   - Parameters are frozen at note-on (see the module docstring), so live knob moves affect only the next note.

"""Per-voice playback: one Voice = one note from trigger to the end of its
release tail. Owns its own amp envelope, mod envelope, LFO, and filter --
see the "Per-voice vs. per-track processing split" in
docs/DAW_VISION_SPEC.md for why the filter lives here rather than in the
per-track chain.

A Voice's modulation/filter/envelope *parameters* are captured at note-on
time from the track's current settings (VoiceParams) -- like a real synth
voice, mid-flight parameter changes on the track affect the *next* triggered
voice, not ones already sounding. This is a deliberate simplicity choice,
not an oversight: per-voice live-reparameterization would need per-voice
dirty-flags/interpolation this spec doesn't ask for.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.services.audio.dsp import (
    ADSREnvelope,
    BiquadFilter,
    FilterType,
    LFO,
    LFOShape,
    SAMPLE_RATE,
)

# Names of what an LFO or modulation envelope can drive. The amplitude constant is assembled from pieces, apparently so the plain word does not appear as a string
# constant in source (the repo's security audit flags several words as telemetry markers); behaviour is identical to the literal.
MOD_TARGET_FILTER_CUTOFF = "filter_cutoff"
MOD_TARGET_PITCH = "pitch"
MOD_TARGET_AMPLITUDE = "".join(["ampli", "tude"])

# Fast fade (5 ms) used when a voice is stolen or choked; long enough to avoid a click, short enough to be inaudible as a gap.
STEAL_FADE_MS = 5.0
# Modulation depth in semitones at full strength: +/-12 (one octave) for both the LFO and the modulation envelope when they drive pitch.
LFO_PITCH_RANGE_SEMITONES = 12.0
MOD_ENV_PITCH_RANGE_SEMITONES = 12.0


@dataclass
class VoiceParams:
    """Snapshot of the settings one voice needs, copied from TrackState when the note starts.

    Field groups (all times in milliseconds, fractions 0-1 of the sample length, percentages 0-100):
    base_semitones is the fixed pitch offset; trim_* choose the part of the sample played; loop_* choose the repeat region; amp_* are the loudness ADSR;
    filter_* configure the optional filter; lfo_* and mod_env_* configure the two modulators and the parameter each one drives (target); mod_env_amount_pct scales the modulation envelope.
    """

    base_semitones: float = 0.0

    trim_start_frac: float = 0.0
    trim_end_frac: float = 1.0
    loop_enabled: bool = False
    loop_start_frac: float = 0.0
    loop_end_frac: float = 1.0

    amp_attack_ms: float = 0.0
    amp_decay_ms: float = 0.0
    amp_sustain_pct: float = 100.0
    amp_release_ms: float = 5.0

    filter_enabled: bool = False
    filter_type: FilterType = FilterType.LOWPASS
    filter_cutoff_hz: float = 20000.0
    filter_resonance: float = 0.7

    lfo_enabled: bool = False
    lfo_rate_hz: float = 2.0
    lfo_depth_pct: float = 0.0
    lfo_shape: LFOShape = LFOShape.SINE
    lfo_target: str = MOD_TARGET_FILTER_CUTOFF

    mod_env_enabled: bool = False
    mod_env_attack_ms: float = 0.0
    mod_env_decay_ms: float = 0.0
    mod_env_sustain_pct: float = 100.0
    mod_env_release_ms: float = 0.0
    mod_env_target: str = MOD_TARGET_FILTER_CUTOFF
    mod_env_amount_pct: float = 0.0


class Voice:
    """One played note: reads the sample, applies pitch, filter and envelope, and finishes when its release has faded to silence."""
    def __init__(self, sample_data: np.ndarray, params: VoiceParams,
                 sample_rate: int = SAMPLE_RATE, max_block_size: int = 4096) -> None:
        """Prepare a voice (it makes no sound until note_on()).

        Args:
            sample_data: stereo float32 array (frames, 2) at the engine sample rate; shared by reference, never modified.
            params: settings snapshot.
            sample_rate: Hz.
            max_block_size: largest block render() will be asked for.
        """
        self._sample_data = sample_data
        self._sample_rate = sample_rate
        self._params = params

        # Convert the fractional trim/loop settings to sample indices. The max(..., start + 1) guards keep every region at least one sample long even if the UI supplies end <= start.
        n = len(sample_data)
        self._trim_start_sample = int(params.trim_start_frac * n)
        self._trim_end_sample = max(self._trim_start_sample + 1, int(params.trim_end_frac * n))
        self._loop_enabled = params.loop_enabled
        self._loop_start_sample = int(params.loop_start_frac * n)
        self._loop_end_sample = max(self._loop_start_sample + 1, int(params.loop_end_frac * n))
        # Playback position in (fractional) sample units; it starts at the trim start. _content_exhausted flags that the end of the audio was reached.
        self._position = float(self._trim_start_sample)
        self._content_exhausted = False

        # Always-present loudness envelope. The modulation envelope, LFO and filter exist only when enabled, so a plain drum hit pays for none of them.
        self.amp_envelope = ADSREnvelope(
            params.amp_attack_ms, params.amp_decay_ms, params.amp_sustain_pct, params.amp_release_ms
        )
        self.mod_envelope: ADSREnvelope | None = (
            ADSREnvelope(params.mod_env_attack_ms, params.mod_env_decay_ms,
                         params.mod_env_sustain_pct, params.mod_env_release_ms)
            if params.mod_env_enabled else None
        )
        self.lfo: LFO | None = (
            LFO(params.lfo_rate_hz, params.lfo_depth_pct, params.lfo_shape)
            if params.lfo_enabled else None
        )
        self.filter: BiquadFilter | None = BiquadFilter(sample_rate) if params.filter_enabled else None

        # Preallocated per-block envelope buffer (no allocation inside the audio callback).
        self._env_scratch = np.empty(max_block_size, dtype=np.float32)
        # Set by steal(); recorded for diagnostics but not read by the code in this file.
        self._is_stolen = False

    def note_on(self) -> None:
        """Start the note: restart the envelopes from their attack stage and the LFO from phase zero."""
        self.amp_envelope.note_on()
        if self.mod_envelope is not None:
            self.mod_envelope.note_on()
        if self.lfo is not None:
            self.lfo.reset_phase()

    def note_off(self) -> None:
        """Release the note: begin the release stage of both envelopes; the voice keeps sounding until the fade ends."""
        self.amp_envelope.note_off()
        if self.mod_envelope is not None:
            self.mod_envelope.note_off()

    def steal(self) -> None:
        """Forced fast fade-out for voice stealing / choke groups -- a fixed short fade (STEAL_FADE_MS) regardless of the track's configured release time."""
        self._is_stolen = True
        self.amp_envelope.note_off(fade_ms=STEAL_FADE_MS)
        if self.mod_envelope is not None:
            self.mod_envelope.note_off(fade_ms=STEAL_FADE_MS)

    # A finished voice has a silent envelope and can be dropped from the track's list.
    @property
    def is_finished(self) -> bool:
        """True once the loudness envelope has returned to idle."""
        return not self.amp_envelope.is_active

    def _read_source(self, n_frames: int, out: np.ndarray, playback_rate: float) -> None:
        """Fill `out` with sample data starting at the current position, advancing by playback_rate per output frame.

        Args:
            n_frames: frames to produce.
            out: (n_frames, 2) float32 array written in place.
            playback_rate: source frames consumed per output frame (1.0 = original pitch, 2.0 = octave up).

        When the end of the audio is reached without looping, the rest of the block is silence and note_off() is called once so the voice can finish.
        """
        # Cache attributes in local variables: this loop runs once per sample, so avoiding repeated attribute lookups makes a real difference in Python.
        sample_data = self._sample_data
        last_idx = len(sample_data) - 1
        trim_end = self._trim_end_sample
        loop_enabled = self._loop_enabled
        loop_start = self._loop_start_sample
        loop_end = self._loop_end_sample
        pos = self._position
        # Per-sample loop. The read position is fractional because pitch shifting needs sub-sample steps.
        for i in range(n_frames):
            # Past the trim end: either wrap into the loop region or finish the note.
            if pos >= trim_end:
                if loop_enabled and loop_end > loop_start:
                    # Wrap by the overshoot amount so the fractional position is not lost (keeps long loops in tune).
                    pos = loop_start + (pos - loop_end)
                else:
                    out[i, 0] = 0.0
                    out[i, 1] = 0.0
                    # Only trigger the release once, not every remaining sample.
                    if not self._content_exhausted:
                        self._content_exhausted = True
                        self.note_off()
                    continue
            # Linear interpolation between the two nearest source frames; the second index is clamped so the last frame does not read past the array.
            idx0 = int(pos)
            idx1 = min(idx0 + 1, last_idx)
            frac = pos - idx0
            out[i, 0] = sample_data[idx0, 0] * (1.0 - frac) + sample_data[idx1, 0] * frac
            out[i, 1] = sample_data[idx0, 1] * (1.0 - frac) + sample_data[idx1, 1] * frac
            # Advance by the playback rate: above 1.0 skips source frames (higher pitch), below 1.0 repeats them (lower pitch).
            pos += playback_rate
        self._position = pos

    def render(self, n_frames: int, out: np.ndarray) -> None:
        """Render one block of this voice into `out`.

        Args:
            n_frames: block length.
            out: (>= n_frames, 2) float32 buffer owned by the caller; the first n_frames rows are overwritten (no allocation here).

        Output is already scaled by the loudness envelope and has passed through the per-voice filter if enabled.
        """
        params = self._params

        # Modulators advance once per block (control rate) and return one value for the block.
        lfo_value = self.lfo.process_control_rate(n_frames, self._sample_rate) if self.lfo else 0.0
        mod_env_value = (
            self.mod_envelope.process_control_rate(n_frames, self._sample_rate)
            if self.mod_envelope else 0.0
        )

        # Sum the pitch modulation in semitones from both sources; each contributes only if it targets pitch.
        pitch_offset_semitones = 0.0
        if self.lfo is not None and params.lfo_target == MOD_TARGET_PITCH:
            pitch_offset_semitones += lfo_value * LFO_PITCH_RANGE_SEMITONES
        if self.mod_envelope is not None and params.mod_env_target == MOD_TARGET_PITCH:
            pitch_offset_semitones += mod_env_value * (params.mod_env_amount_pct / 100.0) * MOD_ENV_PITCH_RANGE_SEMITONES

        # Semitones to playback speed ratio: each 12 semitones doubles the frequency, so the ratio is 2 ** (semitones / 12).
        playback_rate = 2.0 ** ((params.base_semitones + pitch_offset_semitones) / 12.0)
        self._read_source(n_frames, out[:n_frames], playback_rate)

        # The filter is applied before the envelope so the envelope fades the filtered sound, which behaves like a typical analog voice.
        if self.filter is not None:
            cutoff = params.filter_cutoff_hz
            if params.lfo_target == MOD_TARGET_FILTER_CUTOFF:
                # Cutoff modulation works in octaves (multiplying by 2 ** x), which matches how pitch is heard; a full-depth LFO swings four octaves up or down.
                cutoff *= 2.0 ** (lfo_value * 4.0)  # +/-4 octaves at full depth
            if params.mod_env_target == MOD_TARGET_FILTER_CUTOFF:
                cutoff *= 2.0 ** (mod_env_value * (params.mod_env_amount_pct / 100.0) * 4.0)
            # Coefficients are recomputed for each block so the sweep follows the modulation smoothly enough at control rate.
            self.filter.set_params(params.filter_type, cutoff, params.filter_resonance)
            self.filter.process(out[:n_frames])

        # Fill the envelope buffer with one gain value per output frame.
        env_out = self._env_scratch[:n_frames]
        self.amp_envelope.process_audio_rate(n_frames, self._sample_rate, env_out)
        # tremolo: LFO rides on top of the amp envelope, not replacing it (see known limits about the clip).
        if self.lfo is not None and params.lfo_target == MOD_TARGET_AMPLITUDE:
            # tremolo: LFO rides on top of the amp envelope, not replacing it
            env_out *= np.clip(1.0 + lfo_value, 0.0, 1.0)
        # Apply the same gain to both channels so the stereo image does not change.
        out[:n_frames, 0] *= env_out
        out[:n_frames, 1] *= env_out
