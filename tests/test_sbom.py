# ==============================================================================
# Program: tests/test_sbom.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: smoke test for scripts/generate_sbom.py: the in-memory document has the expected schema
# tag and lists known first-party files. It does not write dist/ or inspect package versions.
# Run: python3 -m pytest tests/test_sbom.py -q
# ==============================================================================

from __future__ import annotations

from scripts.generate_sbom import build_sbom


# Checks the schema id (consumers key off it) and that one app file and one script file are present
# with posix-style relative paths, proving both scanned trees are covered.
def test_sbom_contains_project_sources() -> None:
    sbom = build_sbom()
    files = {entry["path"] for entry in sbom["files"]}

    assert sbom["schema"] == "id-app-sbom-v1"
    assert "app/security.py" in files
    assert "scripts/security_audit.py" in files
