#!/bin/bash
# ==============================================================================
# Program: create_org_signing_key.sh
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: Shell script automating operational deployment, release verification, dependency sandboxing, or build packaging.
# ==============================================================================
#
# Purpose: one-time creation of the organisation's release signing key pair (RSA 4096). The
# private key signs releases (sign_release.sh when ID_APP_SIGNING_KEY is set); the public key
# travels with each artifact so verify_release.sh can check it.
# Usage  : scripts/create_org_signing_key.sh /absolute/off-repo/path/id-app-release.key
# Outputs: the private key (mode 600) and <key>.pub.pem (mode 644).
# Safety : refuses relative paths, paths inside this repository, and existing files, so a key can
# never be committed by accident or silently overwritten.
# Known limit: the key is unencrypted on disk; protect the machine and folder accordingly.

# Strict mode: exit on any error (-e), on unset variables (-u) and on failures inside pipes.
# A release script that keeps going after a failed step could ship an unverified build.
set -euo pipefail
# New files default to owner-only (rw-------): keys, signatures and the install dir stay private.
umask 077

# Run from the repo root regardless of where the script was invoked, so relative paths below hold.
cd "$(dirname "$0")/.."

# Exactly one argument: the key path. No default, so the location is always a deliberate choice.
if [ $# -ne 1 ]; then
    echo "Usage: scripts/create_org_signing_key.sh /secure/off-repo/id-app-release.key" >&2
    exit 1
fi

KEY_PATH="$1"
# An absolute path is required so the "outside the repo" check below cannot be fooled by a
# relative path that resolves into the checkout later.
case "$KEY_PATH" in
    /*) ;;
    *)
        echo "Signing key path must be absolute and outside this repository." >&2
        exit 1
        ;;
esac

# -P resolves symlinks, so a link pointing back into the repo is still detected as inside it.
REPO_ROOT="$(pwd -P)"
KEY_DIR="$(dirname "$KEY_PATH")"
mkdir -p "$KEY_DIR"
# The folder must exist before it can be resolved to a real path.
KEY_DIR_REAL="$(cd "$KEY_DIR" && pwd -P)"

# Match the repo root itself or anything beneath it.
case "$KEY_DIR_REAL" in
    "$REPO_ROOT"|"$REPO_ROOT"/*)
        echo "Refusing to create a production signing key inside the repository." >&2
        exit 1
        ;;
esac

# Overwriting a key would orphan every release signed with the old one.
if [ -e "$KEY_PATH" ]; then
    echo "Refusing to overwrite existing key: $KEY_PATH" >&2
    exit 1
fi

# 4096-bit RSA: slower to sign than 2048 but a release is signed rarely, and it ages better.
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:4096 -out "$KEY_PATH"
# Belt and braces on top of umask: private key readable by the owner only.
chmod 600 "$KEY_PATH"
# Derive the public half; this is the file that is distributed alongside releases.
openssl pkey -in "$KEY_PATH" -pubout -out "$KEY_PATH.pub.pem"
chmod 644 "$KEY_PATH.pub.pem"

# Final hint so the operator knows how to point the signing script at the new key.
echo "Private key: $KEY_PATH"
echo "Public key:  $KEY_PATH.pub.pem"
echo "Set ID_APP_SIGNING_KEY=$KEY_PATH on the controlled release machine."
