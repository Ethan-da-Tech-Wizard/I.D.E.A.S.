from __future__ import annotations

import tempfile
from pathlib import Path

from scripts.security_audit import audit


def test_security_audit_current_tree_is_clean() -> None:
    assert audit(("app", "scripts")) == []


def test_security_audit_flags_dynamic_execution() -> None:
    with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
        path = Path(temp_dir)
        scan_dir = path.relative_to(Path.cwd()).as_posix()
        bad = path / "bad.py"
        bad.write_text("eval('1 + 1')\n", encoding="utf-8")

        findings = audit((scan_dir,))

    assert len(findings) == 1
    assert "blocked call: eval" in findings[0].message


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


def test_security_audit_flags_subprocess_imports() -> None:
    with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
        path = Path(temp_dir)
        scan_dir = path.relative_to(Path.cwd()).as_posix()
        bad = path / "bad.py"
        bad.write_text("import subprocess\n", encoding="utf-8")

        findings = audit((scan_dir,))

    assert len(findings) == 1
    assert "blocked import: subprocess" in findings[0].message
