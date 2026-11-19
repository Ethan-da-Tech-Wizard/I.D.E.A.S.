# ==============================================================================
# Program: hash_wheelhouse.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: Utility script supporting build automation, security auditing, dependency verification, or asset generation.
# ==============================================================================
#
# How this fits the project
# -------------------------
# Turns the human-edited requirements-build.in (exact `name==version` pins) into
# requirements-build.txt, where every pin carries `--hash=sha256:` values of the wheels that
# were downloaded into wheelhouse/ and reviewed. pip install --require-hashes then refuses any
# wheel that differs by even one byte, which is the tamper check for the release toolchain.
# Called by build_wheelhouse.sh after dependency_policy.py approves the package list.
#
# Inputs / outputs
# ----------------
# Input : --input (.in file), --wheelhouse (folder of *.whl), --output (.txt file); all
#         relative to the repo root.
# Output: the locked requirements file; its path on stdout. Exit 1 with a message on stderr if
#         the wheelhouse is missing, a pin is not exact, or a pinned wheel is not present.
#
# Design decisions
# ----------------
# * Only exact pins are accepted; a range would make the hash set ambiguous.
# * Several wheels per pin (different platforms) are all hashed, so one lockfile serves
#   Windows, macOS and Linux installs.
# * Missing wheels are collected and reported together, not one per run.
#
# Known limits: does not verify wheel signatures, only fingerprints what is on disk, so the
# wheelhouse must be reviewed before this is run.
#
# Run:  python3 scripts/hash_wheelhouse.py

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path


# Repo root, derived from this file so relative CLI paths are stable.
ROOT = Path(__file__).resolve().parent.parent
# name==version, stopping the version at whitespace or a comment. Group 1 = name, group 2 = version.
REQ_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)==([^\s#]+)")


def normalize_name(name: str) -> str:
    """Normalise a distribution name to lowercase with underscores (the form wheel filenames use).

    Note dependency_policy.normalize uses dashes; each script stays internally consistent.
    """

    return re.sub(r"[-_.]+", "_", name).lower()


def sha256_file(path: Path) -> str:
    """Return the lowercase hex SHA-256 of a file, streamed in 1 MiB chunks to bound memory."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_requirements(path: Path) -> list[tuple[str, str]]:
    """Read exact pins from a requirements file as (name, version) tuples.

    Raises:
        ValueError: A non-blank, non-comment line is not an `==` pin (ranges are refused
            because they cannot be matched to one reviewed wheel).
        FileNotFoundError: The file does not exist.
    """

    requirements: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        # Remove trailing comments before judging whether anything is left on the line.
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        match = REQ_RE.match(line)
        # Fail loudly: silently skipping an unpinned line would leave it out of the hash lock.
        if not match:
            raise ValueError(f"Only exact pins are allowed in {path}: {line}")
        requirements.append((match.group(1), match.group(2)))
    return requirements


def matching_wheels(wheelhouse: Path, name: str, version: str) -> list[Path]:
    """Find every wheel in the folder whose distribution and version equal the pin.

    Returns:
        Paths sorted by filename; more than one means per-platform builds of the same release.
    """

    normalized = normalize_name(name)
    matches = []
    for wheel in sorted(wheelhouse.glob("*.whl")):
        parts = wheel.name.split("-")
        # Not a well-formed wheel name ("dist-version-..."); ignore rather than crash.
        if len(parts) < 2:
            continue
        wheel_distribution = normalize_name(parts[0])
        wheel_version = parts[1]
        if wheel_distribution == normalized and wheel_version == version:
            matches.append(wheel)
    return matches


def render_locked_requirements(requirements: list[tuple[str, str]], wheelhouse: Path) -> str:
    """Build the text of the hash-locked requirements file.

    Args:
        requirements: (name, version) pins from parse_requirements().
        wheelhouse: Folder holding the reviewed wheels.

    Returns:
        File content ending in a newline: a generated-file banner, then each pin followed by one
        `--hash=sha256:` continuation line per matching wheel.

    Raises:
        FileNotFoundError: One or more pins have no wheel; all missing pins are listed at once.
    """

    lines = [
        "# Generated by scripts/hash_wheelhouse.py.",
        "# Do not edit by hand; review wheel contents, then regenerate.",
    ]
    missing: list[str] = []
    for name, version in requirements:
        wheels = matching_wheels(wheelhouse, name, version)
        # Defer the error so the developer sees the complete list of missing wheels in one run.
        if not wheels:
            missing.append(f"{name}=={version}")
            continue
        lines.append(f"{name}=={version} \\")
        # pip requirement-file syntax: every line except the last ends in a backslash continuation.
        for index, wheel in enumerate(wheels):
            suffix = " \\" if index < len(wheels) - 1 else ""
            lines.append(f"    --hash=sha256:{sha256_file(wheel)}{suffix}")
    if missing:
        raise FileNotFoundError("Missing reviewed wheels for: " + ", ".join(missing))
    return "\n".join(lines) + "\n"


def main() -> int:
    """CLI entry point; returns 0 on success, 1 after printing the reason to stderr."""

    parser = argparse.ArgumentParser(description="Generate hash-pinned requirements from a reviewed wheelhouse.")
    parser.add_argument("--input", default="requirements-build.in")
    parser.add_argument("--wheelhouse", default="wheelhouse")
    parser.add_argument("--output", default="requirements-build.txt")
    args = parser.parse_args()

    input_path = (ROOT / args.input).resolve()
    wheelhouse = (ROOT / args.wheelhouse).resolve()
    output_path = (ROOT / args.output).resolve()

    # Fail early with a clear message instead of producing a lockfile with no hashes.
    if not wheelhouse.is_dir():
        print(f"Missing wheelhouse: {wheelhouse}", file=sys.stderr)
        return 1

    try:
        locked = render_locked_requirements(parse_requirements(input_path), wheelhouse)
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    output_path.write_text(locked, encoding="utf-8")
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
