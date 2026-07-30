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

## Residual Risks

- Qt and platform audio decoders remain native-code dependencies.
- Code signing and installer verification need real signing credentials.
- Offline wheel hashes must be populated before release CI can produce artifacts.
- OS-level sandboxing must be configured per distribution/installer target.
