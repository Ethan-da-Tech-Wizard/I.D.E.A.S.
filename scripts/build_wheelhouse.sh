#!/bin/bash
# ==============================================================================
# Program: build_wheelhouse.sh
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: Shell script automating operational deployment, release verification, dependency sandboxing, or build packaging.
# ==============================================================================
#
# Purpose: download the reviewed, binary-only dependency wheels for the release build and lock
# their hashes. Step 1 of the offline-reproducible toolchain; build-linux.sh and CI later install
# from wheelhouse/ with --require-hashes, so the network is only touched here, once.
# Inputs : requirements-build.in (exact pins).  Outputs: wheelhouse/*.whl and requirements-build.txt.
# Run    : scripts/build_wheelhouse.sh   (afterwards run scripts/dependency_policy.py to review).
# Known limit: needs network access and a `python` on PATH; it does not review the wheels itself.

# Strict mode: exit on any error (-e), on unset variables (-u) and on failures inside pipes.
# A release script that keeps going after a failed step could ship an unverified build.
set -euo pipefail
# New files default to owner-only (rw-------): keys, signatures and the install dir stay private.
umask 077

# Run from the repo root regardless of where the script was invoked, so relative paths below hold.
cd "$(dirname "$0")/.."

# Nothing to download without the pin list; fail with a clear message instead of a pip traceback.
if [ ! -f requirements-build.in ]; then
    echo "Missing requirements-build.in" >&2
    exit 1
fi

# Private folder: only the builder should be able to swap wheels before they are hashed.
install -d -m 700 wheelhouse
# --only-binary=:all: refuses sdists so no package can run arbitrary setup.py code while downloading.
python -m pip download --only-binary=:all: --dest wheelhouse -r requirements-build.in
# Record sha256 of each downloaded wheel into requirements-build.txt for hash-checked installs.
python scripts/hash_wheelhouse.py
