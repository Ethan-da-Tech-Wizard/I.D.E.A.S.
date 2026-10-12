# Security Hardening Notes

This app is a local-first desktop process. Its highest-risk features are anything
that can execute code, read arbitrary files, load local media paths, start network
activity, or render untrusted text as rich HTML.

## Implemented Controls

- The editor no longer uses Python `exec` for the Run button.
- Safe scripts are limited to literal `play_sample("relative/path.wav")` calls.
- Imports, attributes, loops, assignments, comprehensions, dynamic calls, and
  path traversal are blocked before any callback is reached.
- File opening is limited to regular UTF-8 text files under a 2 MiB cap.
- Binary files, device files, oversized files, and undecodable files are blocked.
- Console output is inserted as plain text, not rich HTML.
- Remote Git operations (cloning, fetching, pushing) are allowed only under the reviewed app/ide/git.py wrapper, using explicit user confirmation and sandboxed commands.
- Sample playback resolves only bundled relative WAV files under
  `app/resources/samples`.
- The DAW sample browser uses the bundled sample directory, not process cwd.
- The file explorer does not index the home directory until a workspace is chosen.
- SQLite app data directories/files are chmod-hardened on platforms that support it.
- Release builds no longer install build tools from the network at build time.
- CI release builds require offline wheels plus `--require-hashes` pinned inputs.
- The app initializes SQLite storage at startup through the hardened data path.
- `scripts/security_audit.py` fails CI/local checks on dangerous API additions.
- Audio backends initialize lazily so IDE startup is not dependent on host audio.
- `scripts/generate_sbom.py` records package and source-file inventory for releases.
- `scripts/run-linux-sandboxed.sh` provides a networkless Linux bubblewrap launcher.
- Release signing scripts create and verify detached OpenSSL or GPG signatures.
- Windows CI refuses non-PR release builds without code-signing secrets.
- Release builds exclude unused Qt DBus, QML/Quick, and WebEngine modules.
- QtNetwork remains packaged because QtMultimedia requires it internally.
- No-telemetry policy is enforced by audit rules for telemetry SDKs, network
  clients, sockets, embedded browser controls, and URL literals in app/scripts.
- Project save/load and audio export are bounded and validate output paths.
- The theme engine loads only bounded local `.qss` files from built-in or
  configured theme directories, rejects traversal-style theme names, and stores
  preferences under a validated app configuration path.
- The theme engine can import/export custom `.qss` themes only through explicit
  local paths, simple validated theme names, `.qss` suffix enforcement, regular
  file checks, and the existing QSS size cap. Imported themes are copied as
  local text stylesheets and are not plugins or executable extensions.
- Engine shader graph export generates inert descriptor text only. The Engine
  workbench does not compile, execute, dynamically import, or load generated
  shader output.
- Engine viewport editing and physics playback operate on in-memory scene/ECS
  data. The simulation timer is local to the Engine workspace and does not
  start subprocesses, sockets, file watchers, or background services.
- Dock panel mode layouts are stored as app-generated Qt state bytes encoded in
  JSON under validated per-mode names, keeping IDE/DAW/Engine/Studio layouts
  separate without reading arbitrary user files.
- Standalone Engine scene import/export uses inert `.ideas-scene` JSON only.
  Imports go through bounded UTF-8 text reading and suffix checks; exports go
  through explicit user-selected output paths and the safe output writer. Scene
  files cannot execute code, load assets, start processes, or access networks.
- Engine undo/redo stores bounded in-memory snapshots of existing serialized
  workspace state. Trigger/event fields are metadata only and do not dispatch
  callbacks or execute scripts.
- Engine scene graphs enforce caps on node count, hierarchy depth, polygon
  points, id/name length, coordinates, dimensions, velocities, mass, physics
  material values, and collision layers. Scene loading rejects duplicate ids
  anywhere in the hierarchy and oversized undo/redo snapshots.
- Engine Scene Tree and Properties dock panels are live views over the existing
  in-memory scene graph. Property edits route through the same bounded
  `EngineWorkspace.update_selected_node()` path used by the Inspector tab; the
  panels do not add file I/O, code execution, networking, plugin loading, or
  trigger callback dispatch.
- Sandboxed execution of C++/Rust compilers and emulator engines is allowed only under reviewed app/ide/compiler.py and app/services/emulators.py wrappers, isolating execution to the workspace.
- P2P networking is constrained to private IP ranges (LAN/loopback only) and rejects wildcard binds. Messages are authenticated with HMAC-SHA256 and restricted to a 10MB limit.
- Retro Emulator subprocesses are launched with explicit argument lists and checked with is_safe_subpath to prevent escape. Emulator memory modifications are strictly validated and restricted to the active emulator process.
- Linux install verifies signatures before copying artifacts and uses the
  sandbox launcher as the default entrypoint.
- `scripts/dependency_policy.py` keeps release dependencies reviewed and blocks
  high-risk unused packages from returning quietly.
- Studio pillar (Agent 2): the video timeline, photo editor, MIDI service,
  plugin bridge, and asset manager all keep pure-Python data/logic separate
  from their Qt widgets so file operations are unit-testable without a
  display. The asset manager's rename/delete operations and the plugin
  bridge's scan/load operations validate every path with `is_safe_subpath()`
  against a workspace root before touching disk. The MIDI service is fully
  optional: when `python-rtmidi` is not installed, or no hardware is
  connected, it logs a warning and returns an empty device list instead of
  raising, so app startup never depends on MIDI hardware. The VST3/AU plugin
  bridge is architecture-only -- `scan_plugins` returns no results and
  `process_block` is a pass-through until a sandboxed native plugin loader is
  reviewed and added.
- Studio cross-widget wiring (Agent 2): double-clicking an asset in the Asset
  Manager routes it to the right editor by validated file kind (audio/image/
  video), never by trusting a client-supplied MIME type. Image import goes
  through Qt's image codecs only (no code execution). Video "import" only
  ever stores a filename string in a TimelineClip -- no video is decoded.
  Audio import deliberately does **not** bypass the DAW's existing sample
  boundary: `DawWorkspace.add_track_with_sample()` still requires the path to
  resolve under the bundled `app/resources/samples` root via
  `resolve_sample_path()`, and raises `SecurityError` (surfaced as a console
  warning, not a crash) for any asset-workspace file outside it. Loading a
  user's own external WAV into the DAW is intentionally unimplemented until
  that gets its own reviewed, path-validated loading path.
- **Task F1 delivered**: that reviewed path now exists.
  `app/services/audio/sample_import.py` validates a user-selected `.wav` file
  (regular file, `.wav` suffix, size cap, real `wave.open()` parse) and
  *copies* it into `<workspace>/audio/custom/` -- never a live reference to
  the original. `resolve_custom_sample_path()` mirrors
  `resolve_sample_path()`'s exact traversal/suffix checks, scoped to that
  workspace directory instead of the bundled root, so the original bundled
  boundary is untouched. `DawTrack`/`DawWorkspace` expose these as a "My
  Samples" category that only appears once a project workspace is open
  (`DawWorkspace.set_workspace_path()`), and disappears again if the
  workspace is cleared. See `docs/THREAT_MODEL.md`'s "Custom DAW sample
  import" entry, written before this code, for the full reviewed threat
  model.
- **Task F3 (drag-and-drop)**: dropping an asset onto the DAW, Photo Editor,
  or Video Editor goes through the exact same validated import methods as
  double-clicking it in the Asset Manager -- there is no separate file-reading
  path for drops. The drag payload (`AssetListModel` in `asset_manager.py`,
  custom MIME type `application/x-ideas-asset`) carries only the asset's
  relative path as UTF-8 text; `decode_asset_mime_payload()` rejects invalid
  UTF-8 or an empty payload before any widget acts on it. Each target widget
  only emits an `asset_dropped` signal on a successful decode; routing (and
  all the existing per-kind validation) still happens centrally in
  `MainWindow._route_asset_activation()`.
- **Task F4 (Photo Editor canvas basics)**: zoom/pan is a local `QTransform`
  applied only to on-screen rendering and mouse-coordinate mapping -- it adds
  no new file I/O, execution, or network surface. `LayerStack.resize_canvas()`
  reuses the same `MAX_CANVAS_DIMENSION` bound `Layer.__init__` already
  enforced, so a resize can't be used to allocate an unbounded `QImage`.
  Undo/redo keyboard shortcuts call the same `undo()`/`redo()` methods the
  toolbar buttons already used.

## Fixed: Project State Sanitizer Silently Dropped Studio (and Engine) State

- `app.project.sanitize_project_state()` only ever whitelisted
  `ide_content`/`ide_filename`/`daw` -- any other top-level key passed to
  `save_project_file()`/`save_workspace()` was silently stripped before being
  written to disk. This meant the Engine's `get_state()` (Agent 1) was
  already being dropped on every save, and would have silently dropped Task
  F2's new `video_editor`/`photo_editor`/`asset_manager` keys too. Fixed by
  whitelisting all four as opaque dicts (each already produced and validated
  by its own owning widget's `get_state()`), with the same DAW-style
  track-count cap applied to `video_editor.tracks` that `daw.tracks` already
  had. No new attack surface: these are still just JSON dicts bounded by the
  project file's existing `MAX_PROJECT_FILE_BYTES` cap, the same backstop
  that already covered `daw`.

## Fixed: audio_player.py Audit Bypass

- `app/services/audio_player.py` previously imported `subprocess` via
  `importlib.import_module("subprocess")` specifically to dodge
  `scripts/security_audit.py`'s static import/call checks (the source
  comment literally said "to satisfy AST-level security audit"). This has
  been fixed: it now does a normal `import subprocess`, and
  `("app/services/audio_player.py", "subprocess")` /
  `("app/services/audio_player.py", "subprocess.Popen")` were added as
  reviewed entries in `ALLOWED_IMPORTS`/`ALLOWED_CALLS`, the same pattern
  used for `app/ide/git.py`, `app/ide/compiler.py`, `app/services/emulators.py`,
  `app/network/p2p.py`, and `app/ai/core.py`. Why this call site is safe: the
  command list is built from a fixed whitelist of three binary names
  (`paplay`/`aplay`/`afplay`) located via `shutil.which()`, never `shell=True`,
  and the only variable argument appended is a WAV path that always comes from
  `get_or_render_fx()`/`resolve_sample_path()` (bundled-samples-scoped,
  already validated) -- never raw user input. Verified the audit now actually
  gates this file: removing the whitelist entries makes `audit()` correctly
  flag both the import and the call again.

## Remaining Production Requirements

- Provide organization-controlled signing credentials in CI and release machines.
- Populate the reviewed wheelhouse with the pinned build dependencies.
- Add crash-safe logging that avoids secrets and source-code leakage.
- Add operating-system sandboxing where available.
- Populate `wheelhouse/` and `requirements-build.txt` before enabling release CI.
