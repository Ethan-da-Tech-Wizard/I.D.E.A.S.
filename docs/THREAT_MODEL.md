# Threat Model

## Security Objective

NeonDAW must behave as a local desktop tool that does not give project files,
audio samples, text opened in the editor, or UI actions a route to execute host
commands, start network activity, render injected HTML, escape bundled resource
directories, or prevent the app from starting.

## Trust Boundaries

- User-selected files and folders are untrusted.
- Text in the editor is untrusted, including files the user opens intentionally.
- Project files are untrusted even when the user selects them.
- Sample names and relative resource paths are untrusted until validated.
- Export output paths are untrusted until validated.
- Build dependencies and release artifacts are untrusted until pinned, hashed,
  reviewed, and signed.
- Operating-system services such as audio backends are outside the app trust
  boundary and must not be required for IDE startup.
- Network interfaces and remote peer packets are untrusted.
- Emulator binaries, ROM files, and emulator process memory spaces are untrusted.
- User-imported custom DAW samples are untrusted until copied into the
  workspace's `audio/custom/` folder through the same class of validation
  (regular file, `.wav` suffix, size cap, real WAV parse, traversal-checked
  destination name) that already governs bundled sample resolution. Import is
  a copy, never a live reference to the original file.

## Protected Assets

- User files outside the selected workflow.
- App database and configuration.
- Bundled audio resources.
- User-selected export destinations.
- Release build integrity.
- User workstation availability.

## Implemented Mitigations

- No arbitrary Python execution from the editor.
- Safe scripts are parsed with `ast` and reduced to literal sample-play commands.
- File opens reject binary, oversized, non-UTF-8, and non-regular files.
- Console output is plain text.
- Git clone and network features are absent from the app UI.
- Sample paths are relative WAV names resolved under bundled resources.
- Audio backends initialize lazily.
- SQLite storage uses private permissions and handled startup failure.
- CI and local audit tooling fail on risky API additions.
- Project save/load uses bounded JSON and schema sanitization.
- Audio export uses bounded offline rendering, validated sample names, validated
  output paths, and a fixed 44.1 kHz PCM writer.
- FX render caches live under private app data instead of generic home cache.
- Studio asset cataloging scans only an explicit local directory, stores metadata
  for reviewed file extensions, caps file count/path depth/path length/file size,
  and does not decode media or execute scripts.
- P2P networking is strictly local-only (restricting TCP/UDP binds and connects to private LAN ranges, rejecting wildcard/0.0.0.0 binds).
- P2P sessions enforce a 10MB message size cap and HMAC-SHA256 integrity validation.
- Retro emulators are launched under the sandboxed app/services/emulators.py wrapper, using explicit argument lists (never shell=True) and is_safe_subpath validation.
- Emulator memory hacking is restricted to Linux-only, validating that the PID matches the active emulator child process.
- Engine viewport editing, object selection, grid snapping, and physics playback
  mutate only in-memory scene graph and ECS data. The physics timer is a bounded
  Qt timer attached to the Engine workspace.
- Engine shader descriptor generation emits inert text from local graph JSON and
  does not compile, execute, load plugins, call subprocesses, or initialize GPU
  shader runtimes.
- Custom theme import/export accepts only explicit local `.qss` files, validates
  simple theme names, caps file size, and stores imported themes under a
  configured theme directory.
- Per-mode dock layout persistence stores only app-generated Qt state bytes in
  JSON files keyed by validated simple mode names.
- Standalone Engine scene import/export is limited to inert `.ideas-scene` JSON
  selected by the user. Scene files restore scene graph, shader graph, viewport,
  and physics metadata only; they cannot execute code or load external assets.
- Engine undo/redo stores serialized state snapshots in memory only. Trigger
  fields and collision layers are metadata until a later reviewed runtime event
  system exists.
- Engine scene graph parsing is bounded by node count, hierarchy depth, polygon
  point count, string lengths, coordinate ranges, physics value ranges, and
  undo/redo snapshot byte size. Duplicate ids are rejected across the full
  hierarchy.
- Engine Scene Tree and Properties dock panels expose only in-memory scene graph
  selection and bounded property editing. They add no new file, network,
  subprocess, plugin, media decoding, or callback execution capability.
- Custom DAW sample import (Task F1 in README.md's Agent 2 follow-up board,
  implemented) copies a user-selected `.wav` file into `<workspace>/audio/custom/`
  only after it passes a real `wave.open()` parse, a regular-file check, and
  the same size cap as asset cataloging. The DAW's existing bundled-sample
  root (`app/resources/samples`) and its `resolve_sample_path()` boundary are
  unchanged and unweakened -- custom samples are resolved through a second,
  equally strict function scoped to the workspace's `audio/custom/`
  directory. The import action is unavailable until a workspace folder is
  open, matching the Explorer's existing "no home-directory indexing until a
  workspace is chosen" rule.

## Forbidden Without New Review

- Terminal panes.
- Plugin loading.
- Embedded browsers or web views.
- Local HTTP/WebSocket servers.
- Remote repository clone/pull/fetch outside of the reviewed sandboxed git.py wrapper.
- Arbitrary script execution outside of the sandboxed compilers.py or execution wrappers.
- Opening files for write outside app-owned storage or user-selected workspaces.
- Project or export writes without explicit user-selected destinations.
- Automatic dependency installation during release builds.
- P2P networking binds outside private LAN/loopback ranges.
- Emulator execution outside the reviewed subprocess wrapper.
- Shader compilation/execution from Engine shader descriptors.
- Theme formats other than bounded local `.qss` text files.
- Dock layout files that are not app-generated Qt state payloads.
- Runtime execution of trigger/collision event callbacks.
- Scene files that reference or auto-load external assets without Asset Manager
  validation.

## Residual Risks

- Qt and platform audio decoders remain native-code dependencies.
- Code signing and installer verification need real signing credentials.
- Offline wheel hashes must be populated before release CI can produce artifacts.
- OS-level sandboxing must be configured per distribution/installer target.
