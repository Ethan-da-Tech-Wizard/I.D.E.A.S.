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
- Sandboxed execution of C++/Rust compilers and emulator engines is allowed only under reviewed app/ide/compiler.py and app/services/emulators.py wrappers, isolating execution to the workspace.
- Linux install verifies signatures before copying artifacts and uses the
  sandbox launcher as the default entrypoint.
- `scripts/dependency_policy.py` keeps release dependencies reviewed and blocks
  high-risk unused packages from returning quietly.

## Remaining Production Requirements

- Provide organization-controlled signing credentials in CI and release machines.
- Populate the reviewed wheelhouse with the pinned build dependencies.
- Add crash-safe logging that avoids secrets and source-code leakage.
- Add operating-system sandboxing where available.
- Populate `wheelhouse/` and `requirements-build.txt` before enabling release CI.
