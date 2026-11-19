# ==============================================================================
# Program: tests/test_release_tooling.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: keeps the release toolchain honest. Checks the hash-lock renderer works on a fake wheel
# and then asserts, by reading the shell scripts and manifest as text, that key safety wiring cannot
# be removed unnoticed (signature verification at install, sandboxed launch, the security gate
# steps, and the "no arbitrary execution" statement in THE_IDEAS_MANIFEST.md).
# Run from the repo root (paths are relative): python3 -m pytest tests/test_release_tooling.py -q
# ==============================================================================

from __future__ import annotations

from pathlib import Path

from scripts.hash_wheelhouse import parse_requirements, render_locked_requirements


# Scenario: one pin and one matching fake wheel (any bytes will hash). The output must contain the
# pin and a sha256 hash line. Note the wheel is "Example_Pkg" while the pin is "Example-Pkg",
# which also proves name normalisation matches dash/underscore spellings.
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


# Text-level guard: install-linux.sh must call verify_release.sh before installing and must install
# both launchers. Cheap to run (no shell executed) yet catches accidental deletions.
def test_linux_install_uses_signature_verification_and_sandbox_launcher() -> None:
    install_script = Path("scripts/install-linux.sh").read_text(encoding="utf-8")

    assert "scripts/verify_release.sh" in install_script
    assert "launch-linux.sh" in install_script
    assert "run-linux-sandboxed.sh" in install_script


# Text-level guard: the opt-in variable and the refusal message must stay in launch-linux.sh, so
# running without a sandbox can never become the silent default.
def test_linux_launcher_refuses_implicit_unsandboxed_execution() -> None:
    launcher = Path("scripts/launch-linux.sh").read_text(encoding="utf-8")

    assert "ID_APP_ALLOW_UNSANDBOXED" in launcher
    assert "Refusing unsandboxed launch" in launcher


# Text-level guard: every gate step (audit, policy, tests, compileall, shell syntax check) must stay.
def test_security_gate_runs_audit_policy_tests_and_shell_checks() -> None:
    gate = Path("scripts/security_gate.sh").read_text(encoding="utf-8")

    assert "scripts/security_audit.py" in gate
    assert "scripts/dependency_policy.py" in gate
    assert "pytest tests -q" in gate
    assert "compileall" in gate
    assert "bash -n scripts/*.sh" in gate


# Policy guard on documentation: older drafts of the manifest described subprocess/terminal
# sandboxes. Those phrases must not return, and the explicit prohibition plus the
# "SECURITY-FIRST COHESION" heading must remain, so docs cannot contradict the audit tool.
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
