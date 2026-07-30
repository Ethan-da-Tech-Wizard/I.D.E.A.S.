from __future__ import annotations

from scripts.generate_sbom import build_sbom


def test_sbom_contains_project_sources() -> None:
    sbom = build_sbom()
    files = {entry["path"] for entry in sbom["files"]}

    assert sbom["schema"] == "id-app-sbom-v1"
    assert "app/security.py" in files
    assert "scripts/security_audit.py" in files
