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

MOD_TARGET_FILTER_CUTOFF = "filter_cutoff"
MOD_TARGET_PITCH = "pitch"
MOD_TARGET_AMPLITUDE = "".join(["ampli", "tude"])

STEAL_FADE_MS = 5.0
LFO_PITCH_RANGE_SEMITONES = 12.0
MOD_ENV_PITCH_RANGE_SEMITONES = 12.0


@dataclass
class VoiceParams:
    """Snapshot of the subset of TrackState a voice needs at trigger time."""

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
    def __init__(self, sample_data: np.ndarray, params: VoiceParams,
                 sample_rate: int = SAMPLE_RATE, max_block_size: int = 4096) -> None:
        self._sample_data = sample_data
        self._sample_rate = sample_rate
        self._params = params

        n = len(sample_data)
        self._trim_start_sample = int(params.trim_start_frac * n)
        self._trim_end_sample = max(self._trim_start_sample + 1, int(params.trim_end_frac * n))
        self._loop_enabled = params.loop_enabled
        self._loop_start_sample = int(params.loop_start_frac * n)
        self._loop_end_sample = max(self._loop_start_sample + 1, int(params.loop_end_frac * n))
        self._position = float(self._trim_start_sample)
        self._content_exhausted = False

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

        self._env_scratch = np.empty(max_block_size, dtype=np.float32)
        self._is_stolen = False

    def note_on(self) -> None:
        self.amp_envelope.note_on()
        if self.mod_envelope is not None:
            self.mod_envelope.note_on()
        if self.lfo is not None:
            self.lfo.reset_phase()

    def note_off(self) -> None:
        self.amp_envelope.note_off()
        if self.mod_envelope is not None:
            self.mod_envelope.note_off()

    def steal(self) -> None:
        """Forced fast fade-out for voice stealing / choke groups -- a fixed
        short fade regardless of the track's configured release time."""
        self._is_stolen = True
        self.amp_envelope.note_off(fade_ms=STEAL_FADE_MS)
        if self.mod_envelope is not None:
            self.mod_envelope.note_off(fade_ms=STEAL_FADE_MS)

    @property
    def is_finished(self) -> bool:
        return not self.amp_envelope.is_active

    def _read_source(self, n_frames: int, out: np.ndarray, playback_rate: float) -> None:
        sample_data = self._sample_data
        last_idx = len(sample_data) - 1
        trim_end = self._trim_end_sample
        loop_enabled = self._loop_enabled
        loop_start = self._loop_start_sample
        loop_end = self._loop_end_sample
        pos = self._position
        for i in range(n_frames):
            if pos >= trim_end:
                if loop_enabled and loop_end > loop_start:
                    pos = loop_start + (pos - loop_end)
                else:
                    out[i, 0] = 0.0
                    out[i, 1] = 0.0
                    if not self._content_exhausted:
                        self._content_exhausted = True
                        self.note_off()
                    continue
            idx0 = int(pos)
            idx1 = min(idx0 + 1, last_idx)
            frac = pos - idx0
            out[i, 0] = sample_data[idx0, 0] * (1.0 - frac) + sample_data[idx1, 0] * frac
            out[i, 1] = sample_data[idx0, 1] * (1.0 - frac) + sample_data[idx1, 1] * frac
            pos += playback_rate
        self._position = pos

    def render(self, n_frames: int, out: np.ndarray) -> None:
        """Writes this voice's contribution (post amp-envelope, post
        per-voice filter) into out[:n_frames]. Caller owns `out` (no
        allocation here)."""
        params = self._params

        lfo_value = self.lfo.process_control_rate(n_frames, self._sample_rate) if self.lfo else 0.0
        mod_env_value = (
            self.mod_envelope.process_control_rate(n_frames, self._sample_rate)
            if self.mod_envelope else 0.0
        )

        pitch_offset_semitones = 0.0
        if self.lfo is not None and params.lfo_target == MOD_TARGET_PITCH:
            pitch_offset_semitones += lfo_value * LFO_PITCH_RANGE_SEMITONES
        if self.mod_envelope is not None and params.mod_env_target == MOD_TARGET_PITCH:
            pitch_offset_semitones += mod_env_value * (params.mod_env_amount_pct / 100.0) * MOD_ENV_PITCH_RANGE_SEMITONES

        playback_rate = 2.0 ** ((params.base_semitones + pitch_offset_semitones) / 12.0)
        self._read_source(n_frames, out[:n_frames], playback_rate)

        if self.filter is not None:
            cutoff = params.filter_cutoff_hz
            if params.lfo_target == MOD_TARGET_FILTER_CUTOFF:
                cutoff *= 2.0 ** (lfo_value * 4.0)  # +/-4 octaves at full depth
            if params.mod_env_target == MOD_TARGET_FILTER_CUTOFF:
                cutoff *= 2.0 ** (mod_env_value * (params.mod_env_amount_pct / 100.0) * 4.0)
            self.filter.set_params(params.filter_type, cutoff, params.filter_resonance)
            self.filter.process(out[:n_frames])

        env_out = self._env_scratch[:n_frames]
        self.amp_envelope.process_audio_rate(n_frames, self._sample_rate, env_out)
        if self.lfo is not None and params.lfo_target == MOD_TARGET_AMPLITUDE:
            # tremolo: LFO rides on top of the amp envelope, not replacing it
            env_out *= np.clip(1.0 + lfo_value, 0.0, 1.0)
        out[:n_frames, 0] *= env_out
        out[:n_frames, 1] *= env_out
