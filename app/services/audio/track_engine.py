"""TrackState (the JSON-serializable parameter contract from
docs/DAW_VISION_SPEC.md's "Data Model & Parameter Contract") and
TrackProcessor (the runtime object that owns a track's voice pool and its
post-mixdown DSP chain: distortion -> delay -> reverb -> compressor -> EQ ->
pan/balance -> fader).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.services.audio.dsp import (
    Compressor,
    DelayLine,
    FilterType,
    LFOShape,
    ReverbComb,
    SAMPLE_RATE,
    ThreeBandEQ,
    apply_distortion,
    db_to_linear,
)
from app.services.audio.voice import STEAL_FADE_MS, Voice, VoiceParams

MAX_VOICES_PER_TRACK = 8
MIXER_FADER_MIN_DB = float("-inf")
MIXER_FADER_MAX_DB = 6.0


@dataclass
class TrimState:
    start_frac: float = 0.0
    end_frac: float = 1.0


@dataclass
class LoopState:
    enabled: bool = False
    start_frac: float = 0.25
    end_frac: float = 0.75


@dataclass
class EnvelopeState:
    attack_ms: float = 0.0
    decay_ms: float = 0.0
    sustain_pct: float = 100.0
    release_ms: float = 5.0


@dataclass
class FilterState:
    enabled: bool = False
    type: str = "lp"
    cutoff_hz: float = 20000.0
    resonance: float = 0.7


@dataclass
class LFOState:
    enabled: bool = False
    rate_hz: float = 2.0
    depth_pct: float = 0.0
    shape: str = "sine"
    target: str = "filter_cutoff"


@dataclass
class ModEnvelopeState:
    enabled: bool = False
    attack_ms: float = 0.0
    decay_ms: float = 0.0
    sustain_pct: float = 100.0
    release_ms: float = 0.0
    target: str = "filter_cutoff"
    amount_pct: float = 0.0


@dataclass
class CompressorState:
    enabled: bool = False
    threshold_db: float = -18.0
    ratio: float = 4.0
    attack_ms: float = 10.0
    release_ms: float = 100.0
    makeup_db: float = 0.0


@dataclass
class MixerState:
    fader_db: float = 0.0
    pan: float = 0.0
    eq_low_db: float = 0.0
    eq_mid_db: float = 0.0
    eq_high_db: float = 0.0


@dataclass
class TrackState:
    """Mirrors the JSON shape in docs/DAW_VISION_SPEC.md exactly -- field
    names here are the field names in tracks_json, schema_version 2+."""

    gate_length_pct: float = 100.0
    step_gate_overrides: dict[int, float] = field(default_factory=dict)
    trim: TrimState = field(default_factory=TrimState)
    loop: LoopState = field(default_factory=LoopState)
    amp_envelope: EnvelopeState = field(default_factory=lambda: EnvelopeState(release_ms=5.0))
    filter: FilterState = field(default_factory=FilterState)
    lfo: LFOState = field(default_factory=LFOState)
    mod_envelope: ModEnvelopeState = field(default_factory=ModEnvelopeState)
    compressor: CompressorState = field(default_factory=CompressorState)
    mixer: MixerState = field(default_factory=MixerState)
    choke_group: int | None = None

    # Continuous knobs replacing the old Distortion/Delay/Reverb booleans.
    distortion_drive: float = 0.0
    delay_enabled: bool = False
    delay_ms: float = 220.0
    delay_feedback: float = 0.35
    delay_mix: float = 0.35
    reverb_enabled: bool = False
    reverb_decay: float = 0.5
    reverb_mix: float = 0.3

    stereo_is_mono_source: bool = True  # drives pan-law vs. balance behavior

    def to_dict(self) -> dict:
        return {
            "gate_length_pct": self.gate_length_pct,
            "step_gate_overrides": dict(self.step_gate_overrides),
            "trim": {"start_frac": self.trim.start_frac, "end_frac": self.trim.end_frac},
            "loop": {"enabled": self.loop.enabled, "start_frac": self.loop.start_frac, "end_frac": self.loop.end_frac},
            "amp_envelope": vars(self.amp_envelope).copy(),
            "filter": vars(self.filter).copy(),
            "lfo": vars(self.lfo).copy(),
            "mod_envelope": vars(self.mod_envelope).copy(),
            "compressor": vars(self.compressor).copy(),
            "mixer": vars(self.mixer).copy(),
            "choke_group": self.choke_group,
            "distortion_drive": self.distortion_drive,
            "delay_enabled": self.delay_enabled,
            "delay_ms": self.delay_ms,
            "delay_feedback": self.delay_feedback,
            "delay_mix": self.delay_mix,
            "reverb_enabled": self.reverb_enabled,
            "reverb_decay": self.reverb_decay,
            "reverb_mix": self.reverb_mix,
            "stereo_is_mono_source": self.stereo_is_mono_source,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TrackState":
        state = cls()
        state.gate_length_pct = data.get("gate_length_pct", 100.0)
        state.step_gate_overrides = {int(k): v for k, v in data.get("step_gate_overrides", {}).items()}
        trim = data.get("trim", {})
        state.trim = TrimState(trim.get("start_frac", 0.0), trim.get("end_frac", 1.0))
        loop = data.get("loop", {})
        state.loop = LoopState(loop.get("enabled", False), loop.get("start_frac", 0.25), loop.get("end_frac", 0.75))
        amp = data.get("amp_envelope", {})
        state.amp_envelope = EnvelopeState(
            amp.get("attack_ms", 0.0), amp.get("decay_ms", 0.0), amp.get("sustain_pct", 100.0), amp.get("release_ms", 5.0)
        )
        filt = data.get("filter", {})
        state.filter = FilterState(filt.get("enabled", False), filt.get("type", "lp"),
                                    filt.get("cutoff_hz", 20000.0), filt.get("resonance", 0.7))
        lfo = data.get("lfo", {})
        state.lfo = LFOState(lfo.get("enabled", False), lfo.get("rate_hz", 2.0), lfo.get("depth_pct", 0.0),
                              lfo.get("shape", "sine"), lfo.get("target", "filter_cutoff"))
        mod = data.get("mod_envelope", {})
        state.mod_envelope = ModEnvelopeState(
            mod.get("enabled", False), mod.get("attack_ms", 0.0), mod.get("decay_ms", 0.0),
            mod.get("sustain_pct", 100.0), mod.get("release_ms", 0.0),
            mod.get("target", "filter_cutoff"), mod.get("amount_pct", 0.0),
        )
        comp = data.get("compressor", {})
        state.compressor = CompressorState(
            comp.get("enabled", False), comp.get("threshold_db", -18.0), comp.get("ratio", 4.0),
            comp.get("attack_ms", 10.0), comp.get("release_ms", 100.0), comp.get("makeup_db", 0.0),
        )
        mixer = data.get("mixer", {})
        state.mixer = MixerState(mixer.get("fader_db", 0.0), mixer.get("pan", 0.0), mixer.get("eq_low_db", 0.0),
                                  mixer.get("eq_mid_db", 0.0), mixer.get("eq_high_db", 0.0))
        state.choke_group = data.get("choke_group")
        state.distortion_drive = data.get("distortion_drive", 0.0)
        state.delay_enabled = data.get("delay_enabled", False)
        state.delay_ms = data.get("delay_ms", 220.0)
        state.delay_feedback = data.get("delay_feedback", 0.35)
        state.delay_mix = data.get("delay_mix", 0.35)
        state.reverb_enabled = data.get("reverb_enabled", False)
        state.reverb_decay = data.get("reverb_decay", 0.5)
        state.reverb_mix = data.get("reverb_mix", 0.3)
        state.stereo_is_mono_source = data.get("stereo_is_mono_source", True)
        return state

    @classmethod
    def from_legacy_dict(cls, old_track: dict) -> "TrackState":
        """Migrates a schema_version 1 track record (the shape DawTrack.
        to_dict() / app.storage.database's demo seed patterns still use --
        `fx_state: [distortion, delay, reverb]` booleans, no gate/loop/
        envelope/filter/LFO/compressor/mixer fields at all) into a v2
        TrackState.

        Per docs/DAW_VISION_SPEC.md's "Saved Pattern Migration" section, the
        defaults here are chosen to be audibly transparent, not arbitrary:
        the old boolean FX map onto the *exact* fixed parameter values
        `audio_fx.py`'s render functions used to use (LEGACY_DISTORTION_DRIVE
        etc. below), so a migrated pattern sounds the same as it did under
        the old offline-render engine, not just "roughly similar."
        """
        state = cls()
        fx_state = old_track.get("fx_state", [False, False, False])
        distortion, delay, reverb = (bool(v) for v in fx_state) if fx_state else (False, False, False)

        state.distortion_drive = LEGACY_DISTORTION_DRIVE if distortion else 0.0
        state.delay_enabled = delay
        state.delay_ms = LEGACY_DELAY_MS
        state.delay_feedback = LEGACY_DELAY_FEEDBACK
        state.delay_mix = LEGACY_DELAY_MIX
        state.reverb_enabled = reverb
        state.reverb_decay = LEGACY_REVERB_DECAY
        state.reverb_mix = LEGACY_REVERB_MIX
        # Everything else (gate length, trim, loop, envelopes, filter, LFO,
        # mod envelope, compressor, mixer, choke group) stays at the
        # dataclass defaults above, which are already the "transparent, does
        # nothing new" values documented in the Data Model contract.
        return state


# Fixed parameter values the pre-Phase-1 offline render engine
# (app/services/audio_fx.py) used for its boolean-toggle FX -- migration
# targets these exactly so old patterns keep their old sound. Do not change
# these to "improve" the sound; that would defeat the point of a transparent
# migration. If the defaults ever need to change, do it as a new, separate,
# deliberate sound-design decision, not a side effect of a migration fix.
LEGACY_DISTORTION_DRIVE = 6.0
LEGACY_DELAY_MS = 220.0
LEGACY_DELAY_FEEDBACK = 0.35
LEGACY_DELAY_MIX = 0.35
LEGACY_REVERB_DECAY = 0.5
LEGACY_REVERB_MIX = 0.3


def migrate_legacy_track_record(old_track: dict) -> dict:
    """Migrates one full schema_version 1 track record -- sample selection
    fields (relative_path/steps/pitch_semitones/chance) plus the FX state --
    into a schema_version 2 record: the same selection fields, flattened
    together with TrackState.from_legacy_dict(...).to_dict().

    This is what actually gets stored in a pattern's `tracks` list; splitting
    the DSP-only migration into TrackState.from_legacy_dict keeps that piece
    testable on its own (it has no opinion on sample selection).
    """
    state = TrackState.from_legacy_dict(old_track)
    record = {
        "relative_path": old_track.get("relative_path"),
        "steps": list(old_track.get("steps", [])),
        "pitch_semitones": old_track.get("pitch_semitones", 0),
        "chance": old_track.get("chance", 100),
    }
    record.update(state.to_dict())
    return record


def _equal_power_pan(pan: float) -> tuple[float, float]:
    """pan in [-1, 1] -> (left_gain, right_gain), constant total power."""
    pan = min(max(pan, -1.0), 1.0)
    angle = (pan + 1.0) * (np.pi / 4.0)
    return float(np.cos(angle)), float(np.sin(angle))


class TrackProcessor:
    def __init__(self, track_id: str, state: TrackState, sample_data: np.ndarray,
                 base_semitones: float = 0.0, sample_rate: int = SAMPLE_RATE,
                 max_block_size: int = 4096) -> None:
        self.track_id = track_id
        self.state = state
        self.sample_data = sample_data
        self.base_semitones = base_semitones
        self.muted = False
        self.soloed = False

        self._sample_rate = sample_rate
        self._max_block_size = max_block_size
        self._voices: list[Voice] = []
        self._bus_scratch = np.zeros((max_block_size, 2), dtype=np.float32)
        # Reused for every voice's render() in sequence within one block --
        # voices render one at a time, so a single shared scratch buffer is
        # enough (no need for a per-voice buffer pool).
        self._voice_scratch = np.zeros((max_block_size, 2), dtype=np.float32)

        self._delay = DelayLine(sample_rate)
        self._reverb = ReverbComb(sample_rate, max_block_size=max_block_size)
        self._compressor = Compressor(sample_rate)
        self._eq = ThreeBandEQ(sample_rate)

        # Meters, read by the engine's throttled UI-signal emission.
        self.peak_level: float = 0.0
        self.gain_reduction_db: float = 0.0

        # Filled in by ChokeGroupRegistry.register(); used to skip
        # notifying yourself and to look up choke_group membership quickly.
        self.choke_group: int | None = state.choke_group

    def _make_voice_params(self, gate_length_frac_override: float | None = None) -> VoiceParams:
        s = self.state
        return VoiceParams(
            base_semitones=self.base_semitones,
            trim_start_frac=s.trim.start_frac,
            trim_end_frac=s.trim.end_frac,
            loop_enabled=s.loop.enabled,
            loop_start_frac=s.loop.start_frac,
            loop_end_frac=s.loop.end_frac,
            amp_attack_ms=s.amp_envelope.attack_ms,
            amp_decay_ms=s.amp_envelope.decay_ms,
            amp_sustain_pct=s.amp_envelope.sustain_pct,
            amp_release_ms=s.amp_envelope.release_ms,
            filter_enabled=s.filter.enabled,
            filter_type=FilterType(s.filter.type),
            filter_cutoff_hz=s.filter.cutoff_hz,
            filter_resonance=s.filter.resonance,
            lfo_enabled=s.lfo.enabled,
            lfo_rate_hz=s.lfo.rate_hz,
            lfo_depth_pct=s.lfo.depth_pct,
            lfo_shape=LFOShape(s.lfo.shape),
            lfo_target=s.lfo.target,
            mod_env_enabled=s.mod_envelope.enabled,
            mod_env_attack_ms=s.mod_envelope.attack_ms,
            mod_env_decay_ms=s.mod_envelope.decay_ms,
            mod_env_sustain_pct=s.mod_envelope.sustain_pct,
            mod_env_release_ms=s.mod_envelope.release_ms,
            mod_env_target=s.mod_envelope.target,
            mod_env_amount_pct=s.mod_envelope.amount_pct,
        )

    def trigger_note(self) -> Voice:
        """Starts a new voice. Callers (the engine's step scheduler or MIDI
        routing) hold the returned Voice and call .note_off() on it directly
        when the gate/held-key ends -- see docs/DAW_VISION_SPEC.md's
        Playback Model section for why release targets a specific voice
        rather than "the track"."""
        if len(self._voices) >= MAX_VOICES_PER_TRACK:
            oldest = next((v for v in self._voices if not v.is_finished), None)
            if oldest is not None:
                oldest.steal()
        voice = Voice(self.sample_data, self._make_voice_params(), self._sample_rate, self._max_block_size)
        voice.note_on()
        self._voices.append(voice)
        return voice

    def choke_all_voices(self) -> None:
        for voice in self._voices:
            if not voice.is_finished:
                voice.steal()

    @property
    def voice_count(self) -> int:
        return len(self._voices)

    def process_block(self, n_frames: int, force_bypass_compressor: bool = False,
                       force_bypass_reverb: bool = False) -> np.ndarray:
        """Returns this track's contribution for the block: bus_scratch[:n_frames],
        a preallocated view (caller must consume before the next call)."""
        bus = self._bus_scratch[:n_frames]
        bus[:] = 0.0

        if self._voices:
            voice_out = self._voice_scratch[:n_frames]
            for voice in self._voices:
                voice.render(n_frames, voice_out)
                bus += voice_out
            self._voices = [v for v in self._voices if not v.is_finished]

        if not self.muted:
            s = self.state
            if s.distortion_drive > 0.0:
                apply_distortion(bus, s.distortion_drive)
            if s.delay_enabled:
                self._delay.delay_ms = s.delay_ms
                self._delay.feedback = min(max(s.delay_feedback, 0.0), 0.95)
                self._delay.mix = min(max(s.delay_mix, 0.0), 1.0)
                self._delay.process(bus)
            if s.reverb_enabled and not force_bypass_reverb:
                self._reverb.decay = min(max(s.reverb_decay, 0.0), 0.98)
                self._reverb.mix = min(max(s.reverb_mix, 0.0), 1.0)
                self._reverb.process(bus)
            if s.compressor.enabled and not force_bypass_compressor:
                self._compressor.threshold_db = s.compressor.threshold_db
                self._compressor.ratio = max(s.compressor.ratio, 1.0)
                self._compressor.attack_ms = s.compressor.attack_ms
                self._compressor.release_ms = s.compressor.release_ms
                self._compressor.makeup_db = s.compressor.makeup_db
                self.gain_reduction_db = self._compressor.process(bus)
            else:
                self.gain_reduction_db = 0.0

            self._eq.process(bus, s.mixer.eq_low_db, s.mixer.eq_mid_db, s.mixer.eq_high_db)

            if s.stereo_is_mono_source:
                left_gain, right_gain = _equal_power_pan(s.mixer.pan)
                mono = (bus[:, 0] + bus[:, 1]) * 0.5
                bus[:, 0] = mono * left_gain
                bus[:, 1] = mono * right_gain
            else:
                pan = min(max(s.mixer.pan, -1.0), 1.0)
                if pan < 0.0:
                    bus[:, 1] *= (1.0 + pan)
                elif pan > 0.0:
                    bus[:, 0] *= (1.0 - pan)

            fader_db = min(s.mixer.fader_db, MIXER_FADER_MAX_DB)
            bus *= db_to_linear(fader_db)

            self.peak_level = float(np.max(np.abs(bus))) if n_frames else 0.0
        else:
            bus[:] = 0.0
            self.peak_level = 0.0
            self.gain_reduction_db = 0.0

        return bus
