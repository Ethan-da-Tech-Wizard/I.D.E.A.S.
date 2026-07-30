from __future__ import annotations

from pathlib import Path

from scripts.hash_wheelhouse import parse_requirements, render_locked_requirements


def test_hash_wheelhouse_renders_hash_locked_requirements(tmp_path: Path) -> None:
    req = tmp_path / "requirements.in"
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    wheel = wheelhouse / "Example_Pkg-1.0.0-py3-none-any.whl"

    req.write_text("Example-Pkg==1.0.0\n", encoding="utf-8")
    wheel.write_bytes(b"reviewed wheel bytes")

    rendered = render_locked_requirements(parse_requirements(req), wheelhouse)

    assert "Example-Pkg==1.0.0" in rendered
    assert "--hash=sha256:" in rendered


def test_linux_install_uses_signature_verification_and_sandbox_launcher() -> None:
    install_script = Path("scripts/install-linux.sh").read_text(encoding="utf-8")

    assert "scripts/verify_release.sh" in install_script
    assert "launch-linux.sh" in install_script
    assert "run-linux-sandboxed.sh" in install_script


def test_linux_launcher_refuses_implicit_unsandboxed_execution() -> None:
    launcher = Path("scripts/launch-linux.sh").read_text(encoding="utf-8")

    assert "ID_APP_ALLOW_UNSANDBOXED" in launcher
    assert "Refusing unsandboxed launch" in launcher


def test_security_gate_runs_audit_policy_tests_and_shell_checks() -> None:
    gate = Path("scripts/security_gate.sh").read_text(encoding="utf-8")

    assert "scripts/security_audit.py" in gate
    assert "scripts/dependency_policy.py" in gate
    assert "pytest tests -q" in gate
    assert "compileall" in gate
    assert "bash -n scripts/*.sh" in gate


def test_manifest_does_not_reauthorize_arbitrary_execution() -> None:
    manifest = Path("THE_IDEAS_MANIFEST.md").read_text(encoding="utf-8")
    forbidden_phrases = [
        "QProcess async execution sandbox",
        "Subprocess Sandbox",
        "NEVER use the deprecated AST Validator",
        "complex logic, infinite loops",
    ]

    for phrase in forbidden_phrases:
        assert phrase not in manifest
    assert "Arbitrary Python, shell, subprocess, terminal, and host-command execution are forbidden" in manifest
    assert "SECURITY-FIRST COHESION" in manifest
