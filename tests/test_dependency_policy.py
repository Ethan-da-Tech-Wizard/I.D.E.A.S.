# ==============================================================================
# Program: tests/test_dependency_policy.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: tests scripts/dependency_policy.py, the release supply-chain gate. Covers: the committed
# requirements/wheelhouse are clean, a network client is blocked, and the reviewed audio stack is
# allowed. Uses tmp_path files so no real requirements are modified.
# Run: python3 -m pytest tests/test_dependency_policy.py -q
# ==============================================================================

from __future__ import annotations

from pathlib import Path

from scripts.dependency_policy import check_requirements, check_wheelhouse


# Guards the repository itself: if someone adds an unreviewed pin to requirements-build.in or drops
# an unknown wheel into wheelhouse/, this fails in CI before a release is built. A missing
# wheelhouse is treated as clean by check_wheelhouse, so this also passes on fresh clones.
def test_dependency_policy_current_release_inputs_are_clean() -> None:
    assert check_requirements(Path("requirements-build.in")) == []
    assert check_wheelhouse(Path("wheelhouse")) == []


# Scenario: a requirements file pinning `requests`. It must be reported with the "blocked" wording
# (first finding), proving the deny-list is checked before the generic "needs review" rule.
def test_dependency_policy_blocks_requests_in_release_requirements(tmp_path: Path) -> None:
    # Written to tmp_path so the real requirements-build.in is never touched.
    req = tmp_path / "requirements-build.in"
    req.write_text("requests==2.32.0\n", encoding="utf-8")

    findings = check_requirements(req)

    assert findings
    assert "blocked release dependency: requests" in findings[0]


# Regression guard for the DAW audio engine: numpy/scipy/sounddevice (and cffi/pycparser, which
# sounddevice needs) were once blocked wholesale. Removing them from the allow-list would break
# the release build, so the exact pins used by the project are asserted clean.
def test_dependency_policy_allows_the_reviewed_audio_engine_dependencies(tmp_path: Path) -> None:
    """numpy/scipy were blocked before the DAW pillar's live audio engine
    needed them -- see docs/DAW_VISION_SPEC.md and the
    FEATURE_SECURITY_REVIEW.md "Live Audio Engine" entry for why the
    exception exists. sounddevice pulls in cffi/pycparser."""
    req = tmp_path / "requirements-build.in"
    req.write_text(
        "numpy==2.5.1\nscipy==1.18.0\nsounddevice==0.5.5\ncffi==2.1.0\npycparser==3.0\n",
        encoding="utf-8",
    )

    assert check_requirements(req) == []
