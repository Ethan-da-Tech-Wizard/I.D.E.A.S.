# Release Security Checklist

Release builds are fail-closed until every item below is satisfied.

## Required Inputs

- Populate `requirements-build.txt` with exact versions and hashes.
- Place reviewed wheels in `wheelhouse/`.
- Use `scripts/build_wheelhouse.sh` only in a reviewed dependency intake step.
- Run `scripts/security_gate.sh`.
- Run `python scripts/generate_sbom.py`.
- Build from a clean working tree.
- Sign the executable and SBOM with organization-controlled keys.
- Verify the signature on a clean machine before distribution.
- Follow `docs/PRODUCTION_SIGNING.md` for key generation and storage.

## Signing

- Linux/local signing uses `scripts/sign_release.sh`.
- Set `ID_APP_SIGNING_KEY=/path/to/private.pem` for OpenSSL detached signatures.
- Without `ID_APP_SIGNING_KEY`, `scripts/sign_release.sh` uses detached GPG signatures.
- Verification uses `scripts/verify_release.sh`.
- Windows CI requires `WINDOWS_SIGNING_CERT_BASE64` and
  `WINDOWS_SIGNING_CERT_PASSWORD` secrets, then runs `signtool verify`.

## Disallowed Release Behavior

- No live `pip install` from the internet.
- No unsigned release artifacts.
- No generated artifact upload when tests or audit fail.
- No release from a developer machine with unknown local changes.
- Release builds exclude unused Qt DBus, QML/Quick, and WebEngine modules.
- QtNetwork remains packaged because QtMultimedia requires it internally.
- Linux install verifies executable and SBOM signatures before copying.
- Linux install makes the sandbox launcher the normal desktop/bin entrypoint.

## Linux Runtime Sandboxing

Use `scripts/run-linux-sandboxed.sh` on systems with `bubblewrap` available. The
launcher unshares the network namespace, mounts a private `/tmp`, binds only the
installed executable and app data, and sets `ID_APP_DATA_DIR`.

`scripts/launch-linux.sh` refuses unsandboxed launch unless
`ID_APP_ALLOW_UNSANDBOXED=1` is set explicitly.
