# Production Signing Key

Do not create or store the production private key in this repository.

## Recommended Setup

1. Use a controlled release machine, HSM, YubiKey-backed GPG key, or CI secret
   storage owned by the organization.
2. Generate the key outside the repo:

   ```bash
   scripts/create_org_signing_key.sh /secure/off-repo/id-app-release.key
   ```

3. Store `/secure/off-repo/id-app-release.key` in the organization key vault.
4. Commit or publish only the public key, never the private key.
5. Set `ID_APP_SIGNING_KEY=/secure/off-repo/id-app-release.key` only on the
   release machine.
6. Build with:

   ```bash
   ID_APP_SIGNING_KEY=/secure/off-repo/id-app-release.key scripts/build-linux.sh
   ```

7. Verify artifacts before release:

   ```bash
   scripts/verify_release.sh dist/id_app dist/id_app.sbom.json
   ```

## Windows

Use an organization code-signing certificate. CI expects:

- `WINDOWS_SIGNING_CERT_BASE64`
- `WINDOWS_SIGNING_CERT_PASSWORD`

The workflow refuses non-PR release builds if those secrets are missing.

## Rotation

- Rotate immediately if the key is copied to an uncontrolled machine.
- Keep old public keys for historical artifact verification.
- Do not reuse test keys for production.
