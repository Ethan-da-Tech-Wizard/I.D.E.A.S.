from __future__ import annotations

import argparse
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

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

BLOCKED_RELEASE_REQUIREMENTS = {
    "requests",
    "urllib3",
    "sentry-sdk",
    "posthog",
    "mixpanel",
    "amplitude-analytics",
}

REQ_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)==")


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_requirement_names(path: Path) -> list[str]:
    names: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("--hash"):
            continue
        match = REQ_RE.match(line)
        if match:
            names.append(normalize(match.group(1)))
    return names


def check_requirements(path: Path) -> list[str]:
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
    findings: list[str] = []
    blocked = {normalize(name) for name in BLOCKED_RELEASE_REQUIREMENTS}
    allowed = {normalize(name) for name in ALLOWED_RELEASE_REQUIREMENTS}
    if not path.exists():
        return findings
    for wheel in sorted(path.glob("*.whl")):
        name = normalize(wheel.name.split("-", 1)[0])
        if name in blocked:
            findings.append(f"wheelhouse: blocked wheel present: {wheel.name}")
        if name not in allowed:
            findings.append(f"wheelhouse: wheel requires security review: {wheel.name}")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="Enforce reviewed release dependency policy.")
    parser.add_argument("--requirements", default="requirements-build.in")
    parser.add_argument("--wheelhouse", default="wheelhouse")
    args = parser.parse_args()

    findings = []
    findings.extend(check_requirements(ROOT / args.requirements))
    findings.extend(check_wheelhouse(ROOT / args.wheelhouse))
    for finding in findings:
        print(finding)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
