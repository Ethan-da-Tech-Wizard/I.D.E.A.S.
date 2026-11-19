# ==============================================================================
# Program: tests/test_security_audit.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: tests scripts/security_audit.py. One test asserts the real app/ and scripts/ trees are
# clean (the actual CI gate); the others write a tiny offending file into a temporary folder
# inside the repo and assert each rule fires exactly as documented.
# Why the folder lives under the CWD: audit() computes paths relative to the repo root, so a temp
# dir outside it would raise ValueError in Finding.render / relative_to.
# Run: python3 -m pytest tests/test_security_audit.py -q
# ==============================================================================

from __future__ import annotations

import tempfile
from pathlib import Path

from scripts.security_audit import audit


# The real gate: any new risky call, URL literal or marker word anywhere in app/ or scripts/ fails here.
def test_security_audit_current_tree_is_clean() -> None:
    assert audit(("app", "scripts")) == []


# Rule: eval is a blocked call. Exactly one finding is expected (no duplicate reporting).
def test_security_audit_flags_dynamic_execution() -> None:
    # The temp dir is created inside the project so relative_to(ROOT) works; it is removed on exit.
    with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
        path = Path(temp_dir)
        scan_dir = path.relative_to(Path.cwd()).as_posix()
        bad = path / "bad.py"
        bad.write_text("eval('1 + 1')\n", encoding="utf-8")

        findings = audit((scan_dir,))

    assert len(findings) == 1
    assert "blocked call: eval" in findings[0].message


# Rule: string constants are scanned. One literal triggers both the URL rule and the marker-word
# rule, so both reasons must be reported. (The domain ends in .invalid, which can never resolve.)
def test_security_audit_flags_telemetry_url_literals() -> None:
    with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
        path = Path(temp_dir)
        scan_dir = path.relative_to(Path.cwd()).as_posix()
        bad = path / "bad.py"
        bad.write_text("endpoint = 'https://telemetry.example.invalid'\n", encoding="utf-8")

        findings = audit((scan_dir,))

    assert findings
    assert any("URL literal" in finding.message for finding in findings)
    assert any("telemetry marker" in finding.message for finding in findings)


# Rule: importing subprocess is blocked in files without an allow-list entry; exactly one finding.
def test_security_audit_flags_subprocess_imports() -> None:
    with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
        path = Path(temp_dir)
        scan_dir = path.relative_to(Path.cwd()).as_posix()
        bad = path / "bad.py"
        bad.write_text("import subprocess\n", encoding="utf-8")

        findings = audit((scan_dir,))

    assert len(findings) == 1
    assert "blocked import: subprocess" in findings[0].message
