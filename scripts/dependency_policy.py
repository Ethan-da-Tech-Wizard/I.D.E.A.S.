# ==============================================================================
# Program: dependency_policy.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: Utility script supporting build automation, security auditing, dependency verification, or asset generation.
# ==============================================================================
#
# How this fits the project
# -------------------------
# Supply-chain gate for release builds. Every package that ends up in the shipped app must be on
# a reviewed allow-list, and a short deny-list names packages that are never acceptable because
# they are network clients or outbound reporting SDKs. build_wheelhouse.sh / CI run this against
# requirements-build.in and the downloaded wheelhouse/ folder; hash_wheelhouse.py then pins hashes.
#
# Inputs / outputs
# ----------------
# Input : --requirements (pip requirements file with `name==version` pins, default
#         requirements-build.in) and --wheelhouse (folder of *.whl files, default wheelhouse).
# Output: one finding per line on stdout; exit 0 when clean, 1 otherwise.
#
# Design decisions
# ----------------
# * Fail closed: an unknown package is a finding ("requires security review"), not a warning, so
#   adding a dependency forces a conscious edit of ALLOWED_RELEASE_REQUIREMENTS in a reviewed PR.
# * Both the requirements file and the actual wheels are checked, because a transitive dependency
#   only shows up in the wheelhouse, never in the hand-written requirements file.
#
# Known limits: only `name==version` lines are read; unpinned or URL requirements are skipped,
# which is acceptable because the release flow requires hash-pinned input anyway.
#
# Run / test:  python3 scripts/dependency_policy.py     (tests/test_dependency_policy.py covers it)

from __future__ import annotations

import argparse
import re
from pathlib import Path


# Repo root, derived from this file so relative --requirements/--wheelhouse paths resolve the same
# way from any working directory.
ROOT = Path(__file__).resolve().parent.parent

# Reviewed packages permitted in a release. Names are compared after normalize(), so spelling
# (underscore vs dash, case) in this set does not matter. Grouped: build/test tooling, the Qt
# stack, then the audio stack that needed an explicit exception (see the comment inside).
ALLOWED_RELEASE_REQUIREMENTS = {
    "altgraph",
    "iniconfig",
    "packaging",
    "pluggy",
    "pygments",
    "pyside6",
    "pyside6_addons",
    "pyside6_essentials",
    "pyinstaller",
    "pyinstaller-hooks-contrib",
    "pytest",
    "setuptools",
    "shiboken6",
    # Added for the DAW pillar's live audio engine -- see
    # docs/DAW_VISION_SPEC.md's Security Architecture section and the
    # FEATURE_SECURITY_REVIEW.md "Live Audio Engine" entry. Local-only, no
    # network, no telemetry; required for real-time audio DSP, which a
    # 2026-08-01 profiling pass showed pure-Python per-sample loops cannot
    # hit budget for. This is the reviewed exception the original blanket
    # block never recorded a reason for.
    "numpy",
    "scipy",
    "sounddevice",
    "cffi",       # sounddevice's PortAudio binding dependency
    "pycparser",  # cffi's dependency
}

# Never-allowed packages: general HTTP clients and reporting SDKs. These are checked before the
# allow-list so the message says "blocked" rather than the weaker "needs review".
BLOCKED_RELEASE_REQUIREMENTS = {
    "requests",
    "urllib3",
    "sentry-sdk",
    "posthog",
    "mixpanel",
    "amplitude-analytics",
}

# Captures the distribution name from a pinned requirement such as "numpy==1.26.4 \"; anything
# that is not an `==` pin (comments, options, URLs) does not match and is ignored.
REQ_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)==")


def normalize(name: str) -> str:
    """Canonicalise a distribution name per PEP 503 so "PySide6_Addons" == "pyside6-addons".

    Wheel filenames use underscores while requirements files often use dashes; comparing raw
    strings would produce false "unreviewed" findings.
    """

    return re.sub(r"[-_.]+", "-", name).lower()


def parse_requirement_names(path: Path) -> list[str]:
    """Extract normalised package names from a pinned requirements file.

    Args:
        path: A requirements file; read as UTF-8.

    Returns:
        Names in file order. Blank lines, comments and `--hash=` continuation lines are skipped.

    Raises:
        FileNotFoundError: If the file is missing (surfaced to CI rather than treated as clean).
    """

    names: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        # Strip trailing comments first so "numpy==1.0  # why" parses and comment-only lines vanish.
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("--hash"):
            continue
        match = REQ_RE.match(line)
        if match:
            names.append(normalize(match.group(1)))
    return names


def check_requirements(path: Path) -> list[str]:
    """Compare each requirement against the deny-list and allow-list.

    Returns:
        Human-readable findings. A blocked name yields two lines (blocked, and not allow-listed),
        which is intentional: both facts are true and the first line carries the real reason.
    """

    # Normalise both sets once, outside the loop, instead of per requirement.
    findings: list[str] = []
    allowed = {normalize(name) for name in ALLOWED_RELEASE_REQUIREMENTS}
    blocked = {normalize(name) for name in BLOCKED_RELEASE_REQUIREMENTS}
    for name in parse_requirement_names(path):
        if name in blocked:
            findings.append(f"{path.name}: blocked release dependency: {name}")
        if name not in allowed:
            findings.append(f"{path.name}: dependency requires security review: {name}")
    return findings


def check_wheelhouse(path: Path) -> list[str]:
    """Apply the same policy to the *.whl files actually downloaded for the release.

    This catches transitive dependencies that never appear in the requirements file.

    Returns:
        Findings; empty if the folder does not exist yet (nothing has been downloaded to judge).
    """

    findings: list[str] = []
    blocked = {normalize(name) for name in BLOCKED_RELEASE_REQUIREMENTS}
    allowed = {normalize(name) for name in ALLOWED_RELEASE_REQUIREMENTS}
    # A missing wheelhouse is not a violation: the requirements check still runs before it is built.
    if not path.exists():
        return findings
    for wheel in sorted(path.glob("*.whl")):
        # Wheel filenames are "{distribution}-{version}-...whl"; the distribution is the first dash field.
        name = normalize(wheel.name.split("-", 1)[0])
        if name in blocked:
            findings.append(f"wheelhouse: blocked wheel present: {wheel.name}")
        if name not in allowed:
            findings.append(f"wheelhouse: wheel requires security review: {wheel.name}")
    return findings


def main() -> int:
    """CLI entry point: run both checks, print findings, return 0 (clean) or 1 (violations)."""

    parser = argparse.ArgumentParser(description="Enforce reviewed release dependency policy.")
    parser.add_argument("--requirements", default="requirements-build.in")
    parser.add_argument("--wheelhouse", default="wheelhouse")
    args = parser.parse_args()

    # Paths are joined onto ROOT so the defaults work no matter where CI starts the script.
    findings = []
    findings.extend(check_requirements(ROOT / args.requirements))
    findings.extend(check_wheelhouse(ROOT / args.wheelhouse))
    for finding in findings:
        print(finding)
    # Non-zero exit is what stops the release job.
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
