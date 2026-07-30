# Privacy and Telemetry Policy

NeonDAW is a local-first desktop app. It must not track, phone home, fingerprint,
or report user behavior.

## Enforced Rules

- No telemetry SDKs.
- No analytics SDKs.
- No HTTP clients.
- No raw sockets except those used by local or explicit P2P networking services under reviewed wrappers.
- No embedded browser or web view.
- No app-source URL literals.
- No Git clone, pull, fetch, or remote project import inside the app unless sandboxed and user-authorized.
- No crash reporting to third-party services.

## Allowed Local Behavior

- Read user-selected UTF-8 text files within safety limits.
- Read bundled WAV samples.
- Write the local SQLite database in the app data directory.
- Write explicit user-selected `.ideas` project files.
- Write explicit user-selected `.wav` exports.
- Use QtMultimedia locally for playback.
- Scan explicit user-selected asset folders locally for bounded filename and size
  metadata without decoding media or sending data anywhere.

## Network Position

The app code has no network feature. QtNetwork may be bundled only because
QtMultimedia imports it internally. Runtime network isolation is available on
Linux through `scripts/run-linux-sandboxed.sh`, which uses `bubblewrap` with
`--unshare-net`.

## Verification

Run:

```bash
python scripts/security_audit.py
python -m pytest tests -q
```
