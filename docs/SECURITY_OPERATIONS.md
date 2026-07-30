# Security Operations Loop

Use this loop to keep the project aligned with high-end local security.

## Every Feature Branch

1. Read `THE_IDEAS_MANIFEST.md`.
2. Fill out `docs/FEATURE_SECURITY_REVIEW.md`.
3. Update `docs/THREAT_MODEL.md` if a trust boundary changes.
4. Update `docs/PRIVACY.md` if data flow changes.
5. Add negative tests before positive polish work.
6. Run the full local gate:

   ```bash
   scripts/security_gate.sh
   ```

## Every Release

1. Build from the reviewed wheelhouse.
2. Generate an SBOM.
3. Sign executable and SBOM.
4. Verify signatures.
5. Install through `scripts/install-linux.sh` so the sandbox launcher is used.

## Monthly Review

- Rebuild wheelhouse from reviewed pins.
- Review release dependencies.
- Review PyInstaller warnings.
- Re-run sandbox smoke tests.
- Confirm no network/telemetry exceptions were added.

## Immediate Stop Conditions

Stop feature work and review security if any change adds:

- `subprocess`, `QProcess`, shell execution, dynamic imports, `eval`, or `exec`.
- Network clients, sockets, URLs, telemetry SDKs, or embedded browsers.
- New media decoders or emulator/VM cores.
- Writes outside app data or explicit user-selected export/project paths.
