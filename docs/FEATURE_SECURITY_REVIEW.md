# Feature Security Review

Copy this checklist into the implementation notes for every new pillar feature.
Do not merge the feature until every applicable item is answered.

## Feature

- Name:
- Pillar: IDE / DAW / Engines / AI / Studio
- Owner:
- New dependencies:
- New file types:
- New write locations:
- New long-running tasks:

## Threat Model Updates

- What user-controlled input enters this feature?
- What files can it read?
- What files can it write?
- Can it start processes, threads, sockets, web views, plugins, interpreters, or VMs?
- Can it allocate large memory buffers or decode untrusted media?
- What happens if the input is malformed, huge, recursive, or path-traversal shaped?

## Required Controls

- Validate all paths before use.
- Cap file sizes, project sizes, media duration, track/layer counts, and memory use.
- Keep generated/cache data under private app data.
- Keep network disabled unless explicitly approved in `docs/PRIVACY.md`.
- Keep arbitrary code/process execution disabled unless explicitly approved in
  `docs/THREAT_MODEL.md` and covered by OS sandboxing.
- Add negative tests for malicious input.
- Run `python scripts/security_audit.py`.
- Run `python -m pytest tests -q`.

## High-Risk Feature Gates

These require a dedicated threat-model section before implementation:

- Terminal or shell.
- Plugins or dynamic imports.
- Git/network/cloud.
- AI remote calls or prompt upload.
- Embedded browser/web view.
- Video/photo import of complex formats.
- Emulator/VM execution.
- Auto-update.

## Completed Reviews

### Local Asset Catalog Foundation

- Name: Local Asset Catalog Foundation
- Pillar: Studio
- Owner: Agent 2
- New dependencies: None
- New file types: `.wav`, `.png`, `.jpg`, `.jpeg`, `.obj`, `.py`, `.txt` metadata only
- New write locations: None
- New long-running tasks: Bounded recursive directory scan only

User-controlled input is the explicit catalog root path and filenames beneath it.
The feature reads directory entries and file sizes only, rejects non-directory
roots, validates relative paths, caps scan count, path depth, path length, and
per-file size, and skips unsupported or invalid files. It cannot write files,
start processes, create sockets, load plugins, execute scripts, or decode media.

### Engine Workbench Deepening Slice

- Name: Engine Workbench Deepening Slice
- Pillar: Engines
- Owner: Agent 1
- New dependencies: None
- New file types: `.qss` import/export only, JSON-compatible shader descriptors
- New write locations: Configured theme directory for imported themes; explicit
  user-selected `.qss` export destinations; app-owned layout preference JSON
- New long-running tasks: Bounded Qt timer simulation loop only while the Engine
  workspace exists

User-controlled input enters through scene object fields, theme import/export
paths, theme names, mode layout names, and shader graph node parameters. Scene
data remains in memory and workspace JSON state. Shader descriptor generation
returns inert text and does not compile, execute, load plugins, call subprocesses,
or touch the GPU runtime. Theme import/export validates suffixes, file size,
theme names, source paths, destination paths, and allowed directories before
copying local QSS text. Dock layout persistence stores opaque Qt state bytes as
base64 under a validated app-owned config path. The feature does not add
networking, HTTP, WebViews, telemetry, dynamic Python execution, or dependency
installation.

### Engine Core Editor Loop

- Name: Engine Core Editor Loop
- Pillar: Engines
- Owner: Agent 1
- New dependencies: None
- New file types: `.ideas-scene` JSON
- New write locations: Explicit user-selected `.ideas-scene` export paths
- New long-running tasks: None beyond the existing bounded Engine physics timer

This pass adds hierarchy movement, inspector-style property mutation, undo/redo,
keyboard shortcuts, richer physics metadata, and standalone scene import/export.
Scene files are inert JSON documents. Import reads only explicit local
`.ideas-scene` files through the safe UTF-8 text reader and size caps; export
writes only explicit `.ideas-scene` destinations through the safe output writer.
Scene import/export does not execute code, load plugins, call subprocesses,
decode media, open sockets, or resolve asset references. Trigger/collision-event
fields are metadata only until a later reviewed capability API exists.

Follow-up hardening adds explicit caps for scene complexity and physics values:
node count, hierarchy depth, polygon points, id/name length, coordinates,
dimensions, velocities, mass, restitution, friction, damping, gravity scale,
collision layers, and undo/redo serialized byte size. The loader rejects
duplicate ids throughout the full hierarchy, including nested children.

### DAW Live Audio Engine (Phase 1: Foundation)

- Name: DAW Live Audio Engine — Phase 1 (audio engine foundation)
- Pillar: DAW
- Owner: Agent (2026-08-01 session), per docs/DAW_VISION_SPEC.md
- New dependencies: `numpy`, `scipy`, `sounddevice` (+ its transitive deps
  `cffi`, `pycparser`). All local-only, no network, no telemetry. `numpy`
  and `scipy` were previously on `scripts/dependency_policy.py`'s
  `BLOCKED_RELEASE_REQUIREMENTS` with no recorded rationale; moved to
  `ALLOWED_RELEASE_REQUIREMENTS` with this entry as the recorded reason —
  see docs/DAW_VISION_SPEC.md's "Measured Performance" section for why a
  pure-Python implementation couldn't hit the real-time budget.
- New file types: None beyond the `.wav` reading `audio_fx.py` already did
  (generalized from mono to stereo).
- New write locations: None. Sample loading is read-only; this phase does
  not render or cache audio to disk (the old `audio_fx.py` offline
  render-and-cache path is superseded, not extended).
- New long-running tasks: One real-time audio callback thread, owned by
  `sounddevice.OutputStream` (PortAudio), started by `AudioEngine.start()`
  and stopped by `AudioEngine.stop()`.

**Threat model.** User-controlled input is: (1) WAV file paths, which flow
through the same `resolve_sample_path`/`sample_import.py` validation
`audio_fx.py` already used — this phase does not add a new file-reading
surface, `app/services/audio/dsp.py:read_wav_stereo` generalizes the
existing mono reader; (2) numeric track/DSP parameters (filter cutoff, LFO
rate, compressor ratio, etc.), which are plain dataclass fields clamped at
the DSP layer (see docs/DAW_VISION_SPEC.md's per-module ranges) rather than
free-form input. The engine cannot start processes, open sockets, load
plugins, or execute dynamically-loaded code — `app/services/audio/
plugin_bridge.py` remains an inert stub, untouched by this work. It does
decode WAV media into float32 numpy buffers (large-memory-buffer risk,
addressed below).

**Required controls status:**
- Caps in place: `MAX_SAMPLE_DURATION_SECONDS = 30.0` per loaded sample
  (~11.5MB at 48kHz/stereo/float32), `MAX_VOICES_PER_TRACK = 8`,
  `GLOBAL_VOICE_CAP = 256`. All scratch/state buffers are pre-sized at
  track/voice construction, not grown at runtime.
- Known gap, not yet closed: `read_wav_stereo` does not itself catch
  `wave.Error` on a malformed WAV header — today this is fine because
  Phase 1 isn't wired to any UI-facing import path yet, but **before Phase
  3 (Sampler UI) lets a user point the live engine at an arbitrary
  custom-imported WAV, that path must route through the same
  "real WAV parse, size cap, malformed-input rejection" validation
  `docs/THREAT_MODEL.md`'s "Custom DAW sample import" entry already
  requires**, the same way the old `audio_fx.py` render path did. Flagging
  this now rather than letting it be discovered later.
- `python scripts/security_audit.py`: passes (0 findings) against the new
  `app/services/audio/{dsp,voice,track_engine,engine}.py` modules.
- `python -m pytest tests -q`: 293 passed, including 62 new tests across
  `tests/test_audio_{dsp,voice,track_engine,engine}.py` covering filter
  stability at near-self-oscillation Q, envelope/LFO/voice-stealing timing,
  choke-group cross-track behavior, the master limiter's ceiling guarantee
  under a deliberately clipping input, and overload-shedding engaging and
  recovering under synthetic load.
- Negative/edge-case tests: near-self-oscillation resonance stays finite
  (no inf/nan), limiter never exceeds ceiling on a 5x-over-full-scale input,
  global voice cap refuses triggers once full, overload shedding engages
  within 2 blocks and recovers after 1s of headroom (matches the documented
  policy exactly, stress-tested at 64 tracks — 365% load down to 63%).

### Engine Live Dock Panels

- Name: Engine Live Dock Panels
- Pillar: Engines
- Owner: Agent 1
- New dependencies: None
- New file types: None
- New write locations: None
- New long-running tasks: None

This pass replaces placeholder Engine dock content with live Scene Tree and
Properties panels bound to the existing in-memory scene graph. User input is
limited to selecting tree items and editing the same bounded scene fields
already validated by `EngineWorkspace.update_selected_node()` and `SceneNode`.
The panels do not read or write files, execute code, load assets, create sockets,
start subprocesses, or dispatch trigger callbacks.
