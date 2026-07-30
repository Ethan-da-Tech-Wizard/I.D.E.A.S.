from __future__ import annotations

from pathlib import Path

from scripts.dependency_policy import check_requirements, check_wheelhouse


def test_dependency_policy_current_release_inputs_are_clean() -> None:
    assert check_requirements(Path("requirements-build.in")) == []
    assert check_wheelhouse(Path("wheelhouse")) == []


def test_dependency_policy_blocks_numpy_in_release_requirements(tmp_path: Path) -> None:
    req = tmp_path / "requirements-build.in"
    req.write_text("numpy==2.5.1\n", encoding="utf-8")

    findings = check_requirements(req)

    assert findings
    assert "blocked release dependency: numpy" in findings[0]
