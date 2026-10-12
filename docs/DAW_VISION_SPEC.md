# NeonDAW Vision Spec

This document records the product and architecture direction for the DAW
pillar, decided with Ethan across scoping questions starting 2026-08-01. It is
additive: it does not replace README.md, ARCHITECTURE.md, THREAT_MODEL.md,
PRIVACY.md, or the Feature Security Review process. Every phase below still
needs its own `FEATURE_SECURITY_REVIEW.md` entry before merge.

Revision 2 turned the prose decisions from revision 1 into a concrete,
implementable contract: exact field names/types/ranges, exact thread-safety
rules for the audio callback, and exact numeric safety clamps. Revision 3
resolved the four remaining product/creative calls revision 2 flagged as
open — stereo, per-voice modulation, choke groups, and resonance character —
see "Resolved Decisions Log." Revision 4 (this pass) is different in kind
from the first three: Phase 1 (the audio engine foundation) actually got
built and profiled, not just specified. The original "8-16 / 16-32 track"
budget in this doc was a pre-implementation estimate; it turned out to be
wrong in an interesting way (see "Measured Performance" below) and the fix
required a dependency this doc didn't originally plan for (scipy). Nothing
below is left as "a filter," "TBD," or an estimate presented as a
measurement.

## Why this is a bigger change than it looks

The current DAW (`app/ui/daw.py`, `app/services/audio_fx.py`,
`app/services/audio_player.py`) is a **one-shot, offline-render** design:

- Playback is fire-and-forget via a `paplay`/`aplay` subprocess. There is no
  position feedback, no level access, no live mixing.
- FX (distortion/delay/reverb + pitch) are pre-rendered to a cached WAV per
  unique `(sample, fx-combo, semitones)` key, because `QSoundEffect` /
  `QMediaPlayer` have no live DSP graph.
- The step sequencer fires a sample and never tracks its lifetime; MIDI
  `trigger_live()` is the same one-shot fire, not a held note.
- The only third-party dependency is PySide6.

The decisions below deliberately move off that model: a real live audio
backend, a live per-track DSP graph, true VU/gain-reduction metering, and a
note-on/note-off playback model with sustain and loop points. That is the
correct call for a "top-tier" DAW, but it means the offline render/cache path
in `audio_fx.py` becomes legacy, the busy-wait `AudioEngineThread` scheduler
gets replaced, and the saved-pattern schema needs a migration path. This spec
exists so that tradeoff is explicit, not discovered mid-implementation.

## North Star

NeonDAW becomes a real-time, native desktop production surface: live
per-track DSP (filter, LFO, envelope, compressor), a waveform-based sampler
with trim/loop/ADSR, a vertical mixer deck with real metering, and a
sample-accurate transport — all running as one local process with no network,
matching the rest of I.D.E.A.S.'s local-first architecture.

## Architecture Decision: Live Audio Backend

- **Dependency**: add `sounddevice` (PortAudio binding) + `numpy` as required
  dependencies in `pyproject.toml`, alongside PySide6. Both are local-only,
  no network, no telemetry — they satisfy the same local-first bar as the
  existing stack. This is a new-dependency entry in
  `FEATURE_SECURITY_REVIEW.md` (new dependency, new long-running audio
  callback thread) and needs a `docs/THREAT_MODEL.md` addition under
  "Operating-system services such as audio backends are outside the app trust
  boundary."
- **Model**: one PortAudio output stream, driven by a real-time audio
  callback (not the current busy-wait `time.sleep(0.001)` loop in
  `AudioEngineThread`). The callback:
  1. Computes the current sample position and derives step index / playhead
     from BPM + elapsed samples (sample-accurate, replaces the QTimer-style
     estimate that was considered and rejected).
  2. Renders each active voice (a voice = one triggered sample instance
     moving through its own envelope/LFO/filter — see the per-voice-vs-
     per-track split below) and sums same-track voices into that track's bus.
  3. Runs each track's **post-mixdown** DSP chain (distortion → delay →
     reverb → compressor → pan → fader) on the summed track signal before
     adding it to the master bus.
- **Per-voice vs. per-track processing split** (this follows directly from
  the per-voice LFO/mod-envelope decision in the Resolved Decisions Log —
  a modulation target only makes sense on a stage that still has one
  instance *per note*):
  - **Per-voice**: amp envelope, mod envelope, LFO, and the **multi-mode
    filter** (each voice owns its own filter instance; its cutoff is offset
    per-block by that voice's own LFO/mod-envelope output before the voice
    is summed into the track bus — this is why the filter moved out of the
    "per-track" FX chain despite being listed under FX Rack for UI/knob
    purposes). Pitch modulation (LFO/mod-envelope targeting pitch) similarly
    offsets that voice's own vari-speed playback rate.
  - **Per-track** (after voices are summed): distortion, delay, reverb,
    compressor, pan/balance, fader. None of these are LFO/mod-envelope
    targets, so one shared instance per track is correct and far cheaper
    than duplicating delay/reverb state per voice.
  - The FX rack UI still shows one filter control per track — it's setting
    the *shared parameters* (base cutoff/resonance) that every voice's
    per-voice filter instance reads, the same relationship the per-track
    LFO/mod-envelope JSON fields already have to their per-voice runtime
    instances.
- **Buffer / latency target**: 512–1024 sample blocks at 48kHz (~10–20ms
  round trip) — the "practical desktop" target chosen over aggressive
  low-latency. See "Measured Performance" below for the real, profiled track
  budget — the number that belongs here changed twice during implementation
  and a stale estimate is worse than pointing at the measurement.
- **Overload behavior**: a veteran DSP engineer's non-negotiable — CPU
  overload must degrade gracefully, never silently glitch or crash the
  stream. If a callback's measured execution time exceeds **70% of the
  block period** (headroom, not the redline) on two consecutive blocks, the
  engine sheds load in a fixed, deterministic order — no product judgment
  call needed here, it's just "least audible first": (1) muted tracks and
  tracks silenced by another track's solo are skipped entirely, already
  true today; (2) compressor bypassed on the track with the lowest current
  peak level; (3) reverb bypassed next, same ordering; repeat down the
  level-sorted track list until back under 70%. An "audio overload"
  indicator lights in the transport bar for as long as shedding is active,
  and clears once a full second passes under threshold with nothing shed.
- **Shared DSP core**: filter/envelope/LFO/compressor/distortion/delay/reverb
  must be implemented as pure numpy functions operating on blocks, used by
  *both* the live callback path and an offline "bounce" pass for
  `export_audio()`. This guarantees playback and exported `.wav` sound
  identical, and lets `audio_fx.py`'s render-and-cache path be retired rather
  than kept as a second, diverging implementation. One deliberate
  difference: the offline bounce is **not** time-constrained, so it never
  applies the overload-shedding degradation described below — an export
  always renders every track's full chain (reverb, compressor, everything)
  at full quality, even if the live callback had to shed load on that same
  pattern during playback because the user's machine couldn't keep up in
  real time.
- **paplay/aplay path**: removed once the backend lands. `audio_player.py`'s
  subprocess approach was a workaround for `QSoundEffect`/`QMediaPlayer`
  unreliability, not a design goal — sounddevice/PortAudio sidesteps that
  problem directly.

## Measured Performance

Phase 1 (the audio engine foundation) is built, not just specified — this
section is measurement, not estimate, from `app/services/audio/{dsp,voice,
track_engine,engine}.py`.

**First implementation (pure numpy, no scipy)**: profiled at 16 tracks with
every track running the full chain (filter, LFO, mod envelope, distortion,
delay, reverb, compressor) at a 1024-sample/48kHz block (21.3ms budget). Mean
callback time was **78ms — 3.7x over budget**, which would glitch
continuously in real playback. `cProfile` traced it to the per-sample Python
loops in `DelayLine.process` and `ReverbComb.process` (a combined ~68% of
callback time): a comb/delay filter with feedback recomputes one sample from
the previous one, and CPython's per-iteration interpreter overhead dominates
at that call volume.

**Root-cause fix, not a workaround**: `scipy.signal.lfilter` was evaluated
and rejected for delay/reverb specifically — a delay line's coefficient
vector would be `delay_samples` long (thousands of mostly-zero taps), and
`lfilter` doesn't exploit that sparsity, so it would cost O(delay_samples)
per output sample instead of O(1). The actual fix: every default delay time
in this spec (220ms delay, 30-44ms reverb combs) is longer than one audio
block (~10-21ms), so within a block, every delay-line/comb read comes from
buffer content written in a *previous* block — meaning the whole block can
be read and written as vectorized numpy slice operations instead of a
per-sample loop (falls back to the old per-sample loop only if a delay
shorter than one block is ever configured). Separately, `scipy.signal.
lfilter` **is** the right tool for the small fixed-order (2) per-voice
biquad filter, so that one component now uses it — this is why `scipy` was
added as a dependency (see below) even though the delay/reverb fix turned
out not to need it. The compressor/limiter's per-sample envelope-follower
loops are genuinely sequential (attack/release coefficient depends on
whether level is rising, so they can't be expressed as a fixed-coefficient
filter) but their level-detection math (abs/max/log10, ~1.1M Python-level
calls at 16 tracks) was vectorized outside the loop, leaving only the
irreducible recursion inside it.

**Result**: at 32 tracks with the full chain active on every track
simultaneously (the worst case — a real pattern would rarely have every
track running reverb+compressor+delay+filter+LFO+mod-envelope at once),
mean callback load is **37%**, p95 **43%** — comfortably inside the 70%
overload threshold, and better than this doc's original pre-profiling
16-32-track estimate. Occasional single-block spikes (observed up to ~186%
at 32 tracks, consistent with GC pauses / OS scheduling jitter rather than
the algorithm) are exactly what the overload-shedding mechanism above exists
for — stress-tested at 64 tracks (deliberately past budget): load started at
365%, the shedding mechanism engaged within 2 blocks as designed, and
settled at 63% as it progressively bypassed compressors/reverbs on the
quietest tracks. The safety net works as specified, under real load.

- **New dependency**: `scipy>=1.11` added to `pyproject.toml` alongside
  numpy/sounddevice — same review class (local-only, no network, no
  telemetry), covered by the same `FEATURE_SECURITY_REVIEW.md` entry.
- **Pre-existing policy conflict, found and resolved**: `scripts/
  dependency_policy.py` had numpy and scipy on `BLOCKED_RELEASE_REQUIREMENTS`
  since before this feature existed, grouped with network/telemetry
  libraries (`requests`, `sentry-sdk`, etc.), with no recorded rationale
  anywhere in the docs. Confirmed with Ethan on 2026-08-01: move numpy,
  scipy, sounddevice, and sounddevice's transitive deps (`cffi`,
  `pycparser`) to `ALLOWED_RELEASE_REQUIREMENTS` instead, with the reasoning
  recorded inline in that file this time. `tests/test_dependency_policy.py`
  updated to match: the numpy-is-blocked test became a
  requests-is-still-blocked test, plus a new test asserting the audio
  engine's dependency set is allowed.
- **lfilter and allocation**: `scipy.signal.lfilter` always returns a new
  output array (no `out=` parameter in its API) — a small, fixed-size
  allocation every call. This is a deliberate, bounded exception to the
  Real-Time Safety Contract's no-allocation rule below, not an oversight:
  it's a fixed-size allocation at a bounded rate (once per voice per block),
  categorically different from the unbounded/data-dependent allocation the
  rule exists to prevent.

## Real-Time Safety Contract

Non-negotiable rules for the audio callback, because a real-time audio
thread that stalls audibly clicks/drops — this is where "top-tier" is won or
lost and it's the part most tutorials get wrong:

- **No locks in the callback.** The callback thread never blocks on a Python
  `threading.Lock`, queue `.get()`, or file I/O. Parameter changes from the
  UI thread (knob turns, mute toggles, step edits) write into plain
  pre-allocated fields on a per-track `TrackState` dataclass; single float/bool
  attribute writes are atomic under the GIL, so the callback reads
  whatever value is currently there with no synchronization. Compound
  updates (e.g. changing 4 ADSR fields together) accept one callback-side
  read tearing across a block boundary — at 512–1024 samples that's
  inaudible and far cheaper than locking.
- **No allocation in the callback.** All numpy buffers (per-track scratch
  block, mix bus, filter delay lines, compressor envelope-follower state)
  are pre-allocated once when a track is created or the block size changes,
  and reused in place (`out[:] = ...`, not `out = ...`) every callback.
  Voice objects are pooled, not created/destroyed per note-on.
- **UI updates cross threads via `Qt.ConnectionType.QueuedConnection`
  signals only** (playhead position, VU/GR meter values, overload
  indicator) — never direct widget mutation from the callback thread.
  Meter signals are throttled to UI frame rate (~30Hz), not emitted every
  audio block, to avoid flooding the Qt event queue.
- **Sample-rate normalization.** All source WAVs (bundled and
  custom-imported) are resampled to a single fixed internal rate —
  **48000 Hz** — at load time, once, off the callback thread. The engine
  never branches on source sample rate at mix time.
- **Channel count**: **stereo-native throughout** (decided over staying
  mono — see Resolved Decisions Log). `read_wav_mono` in `audio_fx.py` is
  superseded by a `read_wav_stereo` that preserves both channels (mono
  source files are duplicated L=R on load, not a separate code path). Every
  DSP primitive (filter, delay, reverb, compressor envelope-follower) runs
  on independent L/R buffers. The mixer's **pan** control is pan-law
  (equal-power placement) for a mono-sourced track and **balance**
  (relative L/R gain, no re-panning) for a stereo-sourced track — same knob,
  behavior selected automatically from the loaded source's channel count so
  the user never has to know which mode they're in.
- **Internal format.** Mixing happens in 32-bit float, full-scale ±1.0.
  Output conversion to the device's native format is PortAudio's job via
  `sounddevice`, not hand-rolled.
- **BPM changes are quantized to the next step boundary**, not applied
  mid-step. Changing BPM while playing does not retroactively shift the
  current step's timing or cause a click/skip — it takes effect starting at
  the next step.
- **Master bus gain staging**: per-track fader range is **-inf to +6 dB**
  (default 0 dB / unity), master fader same range. After summing all tracks,
  a **brickwall limiter at -0.3 dBFS** sits on the master bus unconditionally
  — this is a safety rail, not a creative tool, and cannot be disabled from
  the UI. It exists so any number of summed tracks clipping simultaneously
  (including a resonance knob pushed to its near-self-oscillation ceiling)
  can never produce a discontinuous/damaging output sample.

## Playback Model: Note-On / Note-Off

- `DawTrack.trigger_live()` becomes a `trigger_note_on()` / `trigger_note_off()`
  pair. MIDI note-on/off already exists at the routing layer
  (`MidiTrackRouter`) and maps directly onto this.
- **Step sequencer gate model**: each *active* step gets a draggable gate-tail
  handle directly on the step grid (extends/shrinks how long that step's note
  stays on, independent of the neighboring steps). This is closer to a
  piano-roll's note length than the old binary on/off step. A per-track
  default gate length (e.g. 100% of a step) seeds new steps; dragging a
  step's tail overrides just that step.
- Synthetic note-off for a sequenced step fires at
  `step_start_time + gate_length`, computed by the engine from BPM and the
  step's gate value — no change needed to how MIDI-held notes end (their
  note-off is the physical key-up).

## Waveform + Sampler

- **Layout**: collapsible per track, hidden by default. `DawTrack` keeps its
  current compact row (sample picker, FX rack, step grid) as the default —
  important since patterns can have many tracks in a scroll area. An
  "expand" affordance opens a waveform/envelope editor in place for the
  focused track only.
- **What's drawn on the waveform** (reusing the engine's stereo-aware
  `read_wav_stereo` for the static sample data, rendered as an overlaid or
  stacked L/R trace):
  - Start/end **trim markers** — draggable handles that bound playback to a
    sub-range of the source WAV.
  - **Loop start/end markers** — independent of trim; when set, playback
    sustains by looping the region between them for as long as the note is
    held (true sustain, not the old one-shot-to-completion behavior).
  - **Amp envelope (ADSR) overlay** — Attack/Decay ramp from note-on,
    Sustain level held while looping (or while the trimmed one-shot plays, if
    no loop points are set), Release ramp starting at note-off — or at the
    end of trimmed content for a non-looped sample, whichever comes first.
    A short minimum release (~5ms) is enforced even at Release=0 to avoid
    clicks; this replaces the old instant fire-and-forget cutoff.
  - **Live playhead**, sample-accurate, driven by the audio callback's actual
    read position — not an estimate. When a track has multiple overlapping
    voices (up to the 8-voice cap), the waveform shows the **most recently
    triggered** voice's position; older overlapping voices keep sounding but
    aren't separately visualized — one waveform strip per track, not one
    per voice.
- This "amp envelope" is a **per-track, always-active** control, distinct
  from the mod envelope in the FX rack (see below), even though both reuse
  the same ADSR widget.

## FX Rack v1

All four modules are in scope for v1, as **continuous, real-time knobs**
against the live DSP graph — no offline re-render, no quantized steps. Each
module operates on numpy blocks, at whichever of the per-voice or per-track
processing stages is documented above for that module. LFOs and mod
envelopes are control-rate (updated once per audio block, ~10–20ms — plenty
for musical modulation rates and standard DSP practice); the filter and
compressor are audio-rate (per-sample coefficient/envelope response) since
their attack times and resonance behavior need finer resolution than one
update per block would give.

1. **Multi-mode filter** — LP/HP/BP, biquad (RBJ cookbook coefficients),
   processed independently per L/R channel. Cutoff **20 Hz – 20,000 Hz,
   logarithmic knob taper**. Resonance/Q **0.5 – 8.0**, clamped there in
   code. Per the Resolved Decisions Log, 8.0 is deliberately tuned **close
   to instability** — the filter can ring/squeal on transients as a
   creative acid-style extreme, rather than being tamed to always settle.
   The master bus brickwall limiter (Real-Time Safety Contract) is the
   safety backstop for this, not the filter itself — the filter is allowed
   to be dangerous-sounding as long as the output never is. Live
   frequency-response graph redraws on every knob move (UI-thread
   computation from the current coefficients, not per-block).
2. **LFO** — rate **0.05 – 20 Hz**, depth **0–100%**, shape
   sine/tri/square/saw. **One instance per voice** (per the Resolved
   Decisions Log): each note gets its own LFO phase, reset to 0 at note-on,
   so overlapping/retriggered notes on the same track sweep independently
   rather than sharing a locked phase. The per-track JSON fields below are
   the *parameters* (rate/depth/shape/target) that seed every voice's
   instance — not a single shared runtime state. Target: filter cutoff,
   pitch (±12 semitones range), or amplitude (0–100% depth of tremolo).
3. **Mod envelope (ADSR)** — same numeric ranges as the sampler's amp
   envelope (attack/decay/release **0–5000 ms** each, sustain **0–100%**),
   **one instance per voice**, retriggering fresh on every note-on same as
   the amp envelope. Target: filter cutoff or pitch, with a bipolar amount
   knob (**-100% to +100%**).
4. **Dynamics compressor** — threshold **-60 to 0 dBFS**, ratio **1:1 to
   20:1**, attack **0.1–200 ms**, release **10–2000 ms**, plus a **makeup
   gain knob (0–24 dB, manual, not auto)** so the user controls loudness
   compensation explicitly rather than the engine guessing. Live
   gain-reduction meter shares the same per-block level-detection code the
   mixer's VU meters use.

The existing toggle-based Distortion/Delay/Reverb + pitch/chance controls in
`app/widgets/fx_rack.py` get rebuilt on top of the live graph rather than
`get_or_render_fx` — same sonic building blocks, continuous knobs instead of
booleans, no cache.

Explicitly **out of scope for v1**: VST3/AU plugin hosting (stays behind
`plugin_bridge.py`'s architecture-only stub and the fail-closed plugin-loader
review gate in `THE_IDEAS_MANIFEST.md` — a live DSP graph is not the same
capability as arbitrary native code loading and must not be conflated with
it), sidechain compression, and automation recording/curves over time.

## Knob & Dial Interaction Convention

Applies to every continuous control introduced by this spec (filter
cutoff/resonance, LFO rate/depth, envelope stages, compressor knobs, mixer
fader/pan/EQ) so behavior is consistent across the whole surface rather than
invented per-widget:

- **Vertical click-drag** is the primary gesture (not radial/rotational
  dragging around the knob's circumference — harder to control precisely
  with a mouse). Drag up increases, drag down decreases.
- **Shift+drag** engages fine adjustment (reduced sensitivity, ~10x) for
  precise settings.
- **Double-click** resets the control to its default value (the values shown
  in the Data Model JSON above).
- **Ctrl/Cmd+click** (or right-click) opens a small numeric entry box for
  typing an exact value directly — necessary for reproducible settings, not
  just "close enough by ear."
- **Scroll wheel over a knob** is intentionally *not* bound to a value
  change, matching the existing `NoScrollComboBox` precedent in
  `app/ui/daw.py` (`QComboBox that ignores mouse wheel events to prevent
  accidental value changes while scrolling the DAW panel`) — the same
  reasoning applies even more to knobs sitting inside a scrollable track
  list.

## Widget Style Presets

Ethan's call: don't lock in one visual language for the new DSP widgets —
make it a selectable preset, the same way `THEME_PRESETS` already offers 10
named accent colors (Tokyo/Ember/Ruby/etc.). This adds a **second,
orthogonal** preset axis — control *skin*, not color — so a user can combine
any accent color with any widget skin:

- `WIDGET_STYLE_PRESETS` ships with two entries at launch:
  - **"Retro Hardware"** — rounded/chunky knobs with a radial indicator line,
    LED-segmented meters, screen-print-style labels. Matches the app's
    existing identity (TR-8 samples, "Chipmunk"/"Deepen" pitch presets,
    "Neon Pink" default theme).
  - **"Modern Flat"** — thin-line knobs/sliders, smooth gradient meters,
    minimal chrome. Closer to Ableton/FL Studio/Bitwig.
- Each preset defines: knob renderer (shape + indicator style), meter
  renderer (segmented vs. gradient), and frequency-response/ADSR graph line
  style (dashed/glow vs. solid/flat). The accent color from `THEME_PRESETS`
  applies on top of either skin identically — skin and color are independent
  choices.
- Selection persists alongside the existing `theme_combo` state (same
  workspace/app-preferences storage), as a new `widget_style` field next to
  the existing `theme` field.
- The preset list is designed to grow — a third/fourth skin later is additive
  (new dict entry), not a rearchitecture, same as adding an 11th accent
  color already works today via the `Custom` color-picker entry.

## Mixer Deck

A dedicated vertical deck docked at the bottom of the DAW workspace,
following the same per-mode dock-panel convention the Engine pillar uses
(`PanelManager`, toggleable, layout-persisted per mode). One channel strip
per track plus a master strip:

- Fader (dB) and pan/balance knob — mute/solo already exist per-track
  (`M`/`S` buttons); the mixer strip mirrors the same state rather than
  duplicating it as a separate source of truth.
- **3-band EQ**: low shelf (corner **200 Hz**), mid peak (center **1000 Hz**,
  fixed Q=1.0 — no sweepable mid frequency in v1, that's what the FX rack's
  multi-mode filter is for), high shelf (corner **5000 Hz**), each **±15 dB**.
  Built from the same biquad primitives as the FX rack's multi-mode filter,
  processed per L/R channel.
- **Real VU/peak meter** per track and master, driven by the live backend —
  not simulated. Shows both a **peak** value (instantaneous per-block max,
  decays at a fixed **20 dB/second** release for readability) and an
  **RMS/VU** value (300ms integration window, matching traditional VU-meter
  ballistics) on the same meter widget, as most modern DAWs do. This is the
  reason the live-backend decision was made before the mixer question:
  metering has no honest answer without it.
- Gain-reduction meter appears on a channel strip only when that track's
  compressor is engaged, same per-block level-detection code as the
  compressor itself.

## Data Model & Parameter Contract

New per-track JSON fields (stored inside `tracks_json` in the `patterns`
table, alongside the existing `relative_path`/`steps`/`fx_state`/
`pitch_semitones`):

```jsonc
{
  "gate_length_pct": 100.0,          // 1.0 – 400.0, default 100.0
  "step_gate_overrides": {},         // {"3": 220.0, "12": 50.0} step-index -> pct, sparse
  "trim": {"start_frac": 0.0, "end_frac": 1.0},   // 0.0-1.0 of source sample
  "loop": {"enabled": false, "start_frac": 0.25, "end_frac": 0.75},
  "amp_envelope": {"attack_ms": 0.0, "decay_ms": 0.0, "sustain_pct": 100.0, "release_ms": 5.0},
  "filter": {"enabled": false, "type": "lp", "cutoff_hz": 20000.0, "resonance": 0.7},
  "lfo": {"enabled": false, "rate_hz": 2.0, "depth_pct": 0.0, "shape": "sine", "target": "filter_cutoff"},
  "mod_envelope": {"enabled": false, "attack_ms": 0.0, "decay_ms": 0.0, "sustain_pct": 100.0,
                    "release_ms": 0.0, "target": "filter_cutoff", "amount_pct": 0.0},
  "compressor": {"enabled": false, "threshold_db": -18.0, "ratio": 4.0,
                 "attack_ms": 10.0, "release_ms": 100.0, "makeup_db": 0.0},
  "mixer": {"fader_db": 0.0, "pan": 0.0, "eq_low_db": 0.0, "eq_mid_db": 0.0, "eq_high_db": 0.0},
  "choke_group": null                // null, or an int shared across tracks that should choke each other
}
```

`patterns` table gains a `schema_version INTEGER NOT NULL DEFAULT 1` column.
Rows written before this revision are implicitly version 1; a migration
function bumps them to version 2 on load by filling in exactly the defaults
shown above (which is why the defaults above are chosen to be audibly
transparent, not arbitrary): full gate length, unset loop, instant/full/quick
envelope, bypassed filter/LFO/mod-envelope/compressor, unity mixer gain. The
existing `fx_state`/`pitch_semitones` boolean+int fields are read as before
and mapped onto equivalent fixed points on the new continuous controls
(distortion/delay/reverb become fixed-parameter versions of themselves rather
than being deleted). This is a real migration function (versioned, tested
with old-format fixtures), not a silent best-effort guess —
`Required Controls` already asks for "safe migrations when SQLite is used."

## Voice & Polyphony Rules

- **Per-track voice cap**: max **8 overlapping voices per track** (a voice =
  one note from trigger to the end of its release tail, now carrying its own
  LFO phase and mod-envelope state per the per-voice decision above). Beyond
  8, the oldest voice is stolen (its release is cut short with a 5ms fade to
  avoid a click) to make room for the new one. 8 is generous for drum/step
  use and prevents a fast retrigger + long release/loop combination from
  growing voices unboundedly.
- **Choke groups**: an *additional* voice-ending rule layered on top of
  normal voice stealing, scoped **across tracks**, not within one. When a
  track with a non-null `choke_group` triggers a new voice, every currently
  ringing voice on any *other* track sharing the same `choke_group` value is
  immediately forced into its release phase with the same 5ms anti-click
  fade used for stolen voices (e.g. a closed hi-hat track and an open hi-hat
  track both set `choke_group: 1` — triggering the closed hat cuts the open
  hat short). A track's own retriggers are already governed by the per-track
  voice cap above and are unaffected by choke groups.
- **Global voice cap**: max **256 simultaneous voices** across all tracks,
  enforced before the per-track chain even runs, as the hard backstop behind
  the 8–16/16–32 track CPU budget in the Real-Time Safety Contract.
- **Sample memory cap**: a single loaded/resampled source sample is capped
  at **30 seconds** at the internal 48kHz/float32/**stereo** rate (this
  supersedes `audio_fx.py`'s old `MAX_FX_DURATION_SECONDS = 6.0`, which
  existed only because offline rendering was slow — the live engine's
  constraint is memory, not render time: 30s × 48000 × 4 bytes × 2 channels
  ≈ 11.5 MB per loaded sample). Longer source files are truncated at
  import/load with a visible warning, matching the existing "malformed
  input" posture in `FEATURE_SECURITY_REVIEW.md`.

## Product Priorities

Ordered by what actually unblocks the next item — everything downstream
depends on the live backend existing first:

1. **Audio engine foundation** — done, 2026-08-01. sounddevice callback
   stream, shared numpy/scipy DSP core, note-on/note-off voice model,
   sample-accurate playhead/step clock, graceful overload handling. See
   "Measured Performance" above.
2. **Pattern migration** — done, 2026-08-01. `patterns` table gained a
   `schema_version` column (with a safe `ALTER TABLE` path for existing
   installs, tested against a simulated pre-migration database).
   `TrackState.from_legacy_dict`/`migrate_legacy_track_record` in
   `app/services/audio/track_engine.py` convert a schema_version 1 record
   (`relative_path`/`fx_state`/`pitch_semitones`/`steps`) into a v2 record,
   mapping the old boolean FX onto the *exact* fixed parameter values
   `audio_fx.py`'s render functions used (`LEGACY_DISTORTION_DRIVE = 6.0`,
   etc.) so migrated patterns sound identical, not just similar.
   `app/storage/database.py` gained `load_pattern_v2()` (returns migrated
   v2 data) alongside the **unchanged** `load_pattern()` (still returns v1
   shape) — deliberately, because `app/ui/daw.py` hasn't been rewired to
   the v2 shape yet (that's Phase 3) and must keep working unmodified until
   it is. Migration is a pure read-time transform, not a silent write:
   `load_pattern_v2()` never mutates stored rows.
   Writing these tests caught a real Phase 1 bug: `TrackState.to_dict()`/
   `from_dict()` never actually included the distortion/delay/reverb fields
   added later in Phase 1 — fixed, and now covered by
   `tests/test_pattern_migration.py` (15 tests) exercising the round trip
   the bug had been silently breaking.
3. **Sampler** — partially done, 2026-08-01. The prerequisite half of this
   phase (wiring `app/ui/daw.py` off the old fire-and-forget path onto the
   live engine) is done: `DawTrack` owns a `TrackProcessor`; `DawWorkspace`
   owns the `AudioEngine` and a `QObject` bridge (`AudioEngineUiBridge`)
   that marshals step-boundary events from the audio callback thread to a
   queued Qt signal for playhead highlighting, while the actual note
   triggering happens synchronously on the audio thread itself (not
   deferred through Qt) so timing stays sample-accurate. Mute/solo/FX/pitch
   controls mirror onto plain attributes (`track_processor.muted`, etc.)
   rather than being read as Qt widget state from the audio thread, per the
   Real-Time Safety Contract. MIDI is now true note-on/note-off (was one-shot
   fire-and-forget before). `load_pattern()` (used by the still-unmodified
   UI) and `save_pattern()` continue writing/reading schema_version 1 —
   this pass doesn't yet touch the FX rack's boolean-toggle UI or wire
   `load_pattern_v2()` in. Verified via offscreen smoke tests (mocked
   `sounddevice.OutputStream`): play/stop transport, step-trigger ->
   audible voice, save/load round trip, MIDI note-on/off releasing the
   correct voice, tuning-offset application, and track-removal engine
   cleanup. **Not done yet**: the waveform panel itself, trim/loop markers,
   and per-step gate-tail dragging on the step grid — those still need new
   widgets, not just engine wiring. `app/services/audio_player.py`
   (`SamplePlayer`) is now unused dead code, left in place rather than
   deleted in this pass.
4. **FX rack v2**: filter, LFO, mod envelope, compressor as live continuous
   controls, replacing the offline toggle chain.
5. **Mixer deck**: vertical bottom panel, faders/pan/EQ, real VU + GR
   metering, dock-persisted like other per-mode panels.

## Explicitly Out of Scope (for this spec)

To keep each phase honest and reviewable:

- VST3/AU plugin hosting (unchanged: architecture-only stub, future gated
  review).
- Piano-roll pitch editing per step (gate length only; per-track pitch stays
  a single knob as today).
- Aux sends/returns, sidechain routing — v1 is insert-only per track plus one
  master bus.
- Automation lanes / recorded parameter curves over time.
- MIDI clock / external sync.
- Any network or collaboration feature — no change to the local-first
  posture in `ARCHITECTURE.md` or `THREAT_MODEL.md`.

## Security Architecture

- The DAW pillar remains local-first: `sounddevice`/`numpy`/PortAudio are
  local system libraries, not network components, and introduce no new
  network surface. This still needs the standard new-dependency review
  (supply chain: pin, hash, review, as `THREAT_MODEL.md` already requires for
  build dependencies).
- The audio callback thread is a new long-running task and must be listed in
  the feature's `FEATURE_SECURITY_REVIEW.md` entry, with the same "must not
  crash or hang app startup if the audio backend is unavailable" guarantee
  the existing `audio_player.py` docstring already implies for its fallback
  chain (paplay → aplay → afplay).
- Custom sample import validation (`app/services/audio/sample_import.py`,
  already reviewed for the "Custom DAW sample import" threat-model entry) is
  unchanged — imported samples flow through the same live engine as bundled
  samples now, instead of a separate no-FX code path, which is a
  simplification, not a new trust boundary.
- Resource caps carry forward in spirit even though the offline render cache
  goes away: cap max simultaneous voices, max loaded-sample memory, and
  max track count, so a malformed or adversarial pattern file can't be used
  to exhaust memory or CPU on load. Concrete numbers are in "Voice &
  Polyphony Rules" above, not left as "TBD."

## Resolved Decisions Log

Four product/creative calls that revision 2 deliberately left open (they
were taste and scope tradeoffs, not engineering defaults) — resolved with
Ethan on 2026-08-01 and folded into the sections above:

- **Stereo vs. mono**: go stereo-native. Bigger lift (`read_wav_stereo`,
  dual-channel DSP throughout, revised track-count budget above) than
  staying mono, chosen deliberately over the cheaper mono-internal default.
- **LFO/mod-envelope scope**: per-voice, not per-track. Each note gets
  independent modulation phase/state; the per-track JSON fields are the
  parameters that seed each voice's instance, not a single shared runtime
  state.
- **Choke groups**: in scope for v1. Cross-track, keyed by the `choke_group`
  field already reserved in the data model, layered on top of (not a
  replacement for) the per-track voice-stealing rule.
- **Filter resonance character**: Q=8.0 is tuned to approach
  self-oscillation as a deliberate creative extreme, with the master bus
  brickwall limiter as the safety backstop rather than a tamed filter.

Any future round of open questions for this spec should append here rather
than overwrite this log, matching the "Refinement Pass" convention already
used in `ENGINE_START_TO_END_GOAL.md`.
