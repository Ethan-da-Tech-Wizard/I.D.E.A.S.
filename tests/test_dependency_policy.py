from __future__ import annotations

from pathlib import Path

from scripts.dependency_policy import check_requirements, check_wheelhouse


def test_dependency_policy_current_release_inputs_are_clean() -> None:
    assert check_requirements(Path("requirements-build.in")) == []
    assert check_wheelhouse(Path("wheelhouse")) == []


def test_dependency_policy_blocks_requests_in_release_requirements(tmp_path: Path) -> None:
    req = tmp_path / "requirements-build.in"
    req.write_text("requests==2.32.0\n", encoding="utf-8")

    findings = check_requirements(req)

    assert findings
    assert "blocked release dependency: requests" in findings[0]


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
