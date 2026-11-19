# ==============================================================================
# Program: tests/__init__.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: marks tests/ as a package so pytest (configured with pythonpath ["."] in pyproject.toml) can
# import both `app.*` and `scripts.*` from the repo root without installing the project.
# Layout: one test_<module>.py per app/ module, plus integration files (test_*_integration.py).
# Run: python3 -m pytest tests -q        (the same command CI and scripts/security_gate.sh use)
# ==============================================================================

# Tests
