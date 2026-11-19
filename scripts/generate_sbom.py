# ==============================================================================
# Program: generate_sbom.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: Utility script supporting build automation, security auditing, dependency verification, or asset generation.
# ==============================================================================
#
# How this fits the project
# -------------------------
# Produces a Software Bill of Materials for a release: which Python packages the build
# environment had installed, and a SHA-256 fingerprint of every first-party file. The JSON is
# written locally (dist/id_app.sbom.json by default) and shipped/signed with the release so a
# user or auditor can compare what was built against what was reviewed. Nothing is uploaded.
#
# Inputs / outputs
# ----------------
# Input : --output path, relative to the repo root (default dist/id_app.sbom.json).
# Output: a JSON file (sorted keys, 2-space indent, so diffs between releases are readable) and
#         the written path on stdout. Exit 0 on success, 1 if the path escapes the project.
#
# Design decisions
# ----------------
# * Standard library only (importlib.metadata, hashlib) so it runs inside the locked-down build
#   venv without adding a dependency that policy would have to review.
# * The schema is a tiny private format ("id-app-sbom-v1"), not CycloneDX/SPDX; bump the
#   version string if the shape changes so verifiers can tell.
# * Output is confined to the project directory to stop a bad --output from overwriting files
#   elsewhere on the build machine.
#
# Known limits: lists whatever is installed in the *current* interpreter (not only declared
# dependencies), and does not hash the installed packages' files.
#
# Run / test:  python3 scripts/generate_sbom.py        (tests/test_sbom.py covers it)

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import sys
from pathlib import Path


# Repo root, derived from this file so the SBOM paths are the same from any working directory.
ROOT = Path(__file__).resolve().parent.parent


def sha256_file(path: Path) -> str:
    """Return the lowercase hex SHA-256 of a file, reading it in 1 MiB chunks.

    Chunking keeps memory flat even for large bundled assets.
    """

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def installed_packages() -> list[dict[str, str]]:
    """List every installed distribution as {type, name, version}, sorted case-insensitively.

    Sorting makes the SBOM deterministic, so two builds of the same environment diff cleanly.
    """

    packages: list[dict[str, str]] = []
    for dist in sorted(importlib.metadata.distributions(), key=lambda d: d.metadata["Name"].lower()):
        name = dist.metadata["Name"]
        version = dist.version
        packages.append({"type": "python-package", "name": name, "version": version})
    return packages


def source_files() -> list[dict[str, str]]:
    """Fingerprint every file under app/, scripts/ and tests/ as {type, path, sha256}.

    Paths are repo-relative posix strings so the document is identical on Windows and Linux.
    """

    files: list[dict[str, str]] = []
    for base_name in ("app", "scripts", "tests"):
        base = ROOT / base_name
        # Skip absent trees (e.g. a release checkout that strips tests/) instead of failing.
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            # __pycache__ is machine-local bytecode that would make the SBOM non-reproducible.
            if path.is_file() and "__pycache__" not in path.parts:
                files.append(
                    {
                        "type": "source-file",
                        "path": path.relative_to(ROOT).as_posix(),
                        "sha256": sha256_file(path),
                    }
                )
    return files


def build_sbom() -> dict[str, object]:
    """Assemble the full SBOM document (schema tag, interpreter version, packages, files)."""

    return {
        "schema": "id-app-sbom-v1",
        "python": sys.version.split()[0],
        "packages": installed_packages(),
        "files": source_files(),
    }


def main() -> int:
    """CLI entry point; returns the exit code (0 written, 1 refused)."""

    parser = argparse.ArgumentParser(description="Generate a local JSON SBOM for the release environment.")
    parser.add_argument("--output", default="dist/id_app.sbom.json")
    args = parser.parse_args()

    # resolve() collapses ".." and symlinks so the containment test below cannot be fooled.
    output = (ROOT / args.output).resolve()
    root = ROOT.resolve()
    if root != output and root not in output.parents:
        print(f"Refusing to write SBOM outside project: {output}", file=sys.stderr)
        return 1

    # dist/ usually does not exist on a fresh checkout.
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(build_sbom(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
