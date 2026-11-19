# ==============================================================================
# Program: security_audit.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: Utility script supporting build automation, security auditing, dependency verification, or asset generation.
# ==============================================================================
#
# How this fits the project
# -------------------------
# This is a fail-closed, purely static (AST based) policy check. It never imports or runs the code
# it inspects, so it is safe to run on untrusted changes. CI and scripts/security_gate.sh run it
# before every release build; a non-zero exit blocks the build. The product promise it enforces is
# "local-only": no network clients, no outbound reporting SDKs, no dynamic code execution, no
# embedded browser, and no hard-coded URLs anywhere under app/ or scripts/.
#
# Inputs / outputs
# ----------------
# Input : optional directory names relative to the repo root (default: app and scripts).
# Output: one "path:line: message" line per finding on stdout; exit code 0 when clean, 1 otherwise.
#
# Design decisions
# ----------------
# * Deny-lists plus an explicit allow-list. Every exception is a (file, name) pair, so adding a new
#   risky call to a new file fails until a reviewer consciously extends ALLOWED_CALLS/IMPORTS.
# * Matching is by dotted name, not by resolved import. `import subprocess as sp; sp.run(...)`
#   would slip past check_call; the blocked IMPORT check still catches the import itself, which is
#   why imports and calls are policed separately.
# * String constants are scanned too (docstrings included), because a literal URL or a marker word
#   is the cheapest way for outbound reporting to sneak in. Two policy scripts are exempt because
#   they must spell the forbidden words in order to define them.
#
# Known limits
# ------------
# getattr(), importlib and string-built names are not detected; this is a tripwire for honest
# mistakes, not a sandbox. Runtime confinement lives in app/security.py and run-linux-sandboxed.sh.
#
# Run / test:  python3 scripts/security_audit.py        (tests/test_security_audit.py covers it)

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from pathlib import Path


# Repo root, derived from this file so the audit works from any current directory.
ROOT = Path(__file__).resolve().parent.parent
# Only first-party code is scanned; wheelhouse/venv/tests are third-party or intentionally exempt.
DEFAULT_SCAN_DIRS = ("app", "scripts")

# Call names (dotted, as written in source) that execute code or open network connections,
# mapped to the reason shown in the finding. Per-file exceptions live in ALLOWED_CALLS below.
BLOCKED_CALLS = {
    "eval": "dynamic code execution",
    "exec": "dynamic code execution",
    "compile": "dynamic code compilation",
    "os.system": "shell execution",
    "subprocess.run": "process execution",
    "subprocess.Popen": "process execution",
    "subprocess.call": "process execution",
    "subprocess.check_call": "process execution",
    "subprocess.check_output": "process execution",
    "socket.socket": "network socket",
    "requests.get": "network client",
    "requests.post": "network client",
    "urllib.request.urlopen": "network client",
}

# Modules whose mere import is refused. Prefix matching applies, so "urllib.request" also
# blocks "urllib.request.foo". The last five are third-party reporting SDKs: the app must never ship them.
BLOCKED_IMPORTS = {
    "socket": "network socket",
    "requests": "network client",
    "urllib.request": "network client",
    "subprocess": "process execution",
    "sentry_sdk": "telemetry SDK",
    "posthog": "telemetry SDK",
    "analytics": "telemetry SDK",
    "mixpanel": "telemetry SDK",
    "amplitude": "telemetry SDK",
}

# Qt classes that would give the app a browser or network stack. PySide6 offers them through
# `from PySide6.QtX import Name`, which check_import cannot see, so visit_ImportFrom looks at names.
BLOCKED_QT_NAMES = {
    "QWebEngineView": "embedded browser",
    "QWebView": "embedded browser",
    "QNetworkAccessManager": "network client",
    "QNetworkRequest": "network client",
    "QTcpSocket": "network socket",
    "QUdpSocket": "network socket",
}

# Lower-cased substrings forbidden inside any string constant. A URL literal means the code could
# phone out; the marker words catch reporting endpoints and vendor names. Matching is deliberately
# crude (substring, case-insensitive) so that false positives get fixed by rewording, not by exceptions.
BLOCKED_STRING_FRAGMENTS = {
    "http://": "URL literal",
    "https://": "URL literal",
    "telemetry": "telemetry marker",
    "analytics": "telemetry marker",
    "sentry": "telemetry marker",
    "segment": "telemetry marker",
    "posthog": "telemetry marker",
    "mixpanel": "telemetry marker",
    "amplitude": "telemetry marker",
}

# Reviewed exceptions: (repo-relative posix path, call name). Each one is a real need, e.g. the
# git/compiler wrappers launch child processes, p2p opens the LAN-only peer socket, the terminal
# panel is an intentional evaluator, and generate_synth_samples writes WAV files with wave.open.
ALLOWED_CALLS = {
    ("app/main.py", "sys.exit"),
    ("app/application.py", "QMediaPlayer"),
    ("app/ui/daw.py", "QSoundEffect"),
    ("scripts/generate_synth_samples.py", "wave.open"),
    ("app/ide/git.py", "subprocess.run"),
    ("app/ide/git.py", "subprocess.Popen"),
    ("app/ide/compiler.py", "subprocess.run"),
    ("app/ide/compiler.py", "subprocess.Popen"),
    ("app/network/p2p.py", "socket.socket"),
    ("app/ai/core.py", "socket.socket"),
    ("app/ai/core.py", "subprocess.run"),
    ("app/ai/core.py", "subprocess.Popen"),
    ("app/services/emulators.py", "subprocess.run"),
    ("app/services/audio_player.py", "subprocess.Popen"),
    ("app/ui/terminal.py", "compile"),
    ("app/ui/terminal.py", "eval"),
    ("app/ui/terminal.py", "exec"),
}

# Reviewed import exceptions, same (path, module) shape. These mirror the call exceptions above:
# you cannot call subprocess.run in a file that is not also allowed to import subprocess.
ALLOWED_IMPORTS = {
    ("scripts/security_audit.py", "argparse"),
    ("app/ide/git.py", "subprocess"),
    ("app/ide/compiler.py", "subprocess"),
    ("app/network/p2p.py", "socket"),
    ("app/ai/core.py", "socket"),
    ("app/ai/core.py", "subprocess"),
    ("app/services/emulators.py", "subprocess"),
    ("app/services/audio_player.py", "subprocess"),
}


@dataclass(frozen=True)
class Finding:
    """One policy violation: where it is and a human-readable reason.

    Frozen so findings can be collected, sorted or de-duplicated without accidental mutation.
    """

    path: Path
    line: int
    message: str

    def render(self) -> str:
        """Format as `relative/path.py:LINE: message`, the shape editors and CI logs make clickable."""

        # Relative paths keep CI output identical across machines (no absolute checkout prefix).
        rel = self.path.relative_to(ROOT)
        return f"{rel}:{self.line}: {self.message}"


def dotted_name(node: ast.AST) -> str | None:
    """Flatten a Name/Attribute chain such as `os.path.join` into a dotted string.

    Args:
        node: Any AST node, typically the `func` of an ast.Call.

    Returns:
        The dotted name, or None when the chain bottoms out in something that is not a plain
        name (a call result, subscript, etc.), which the audit cannot classify and so ignores.
    """

    # A bare identifier ends the recursion.
    if isinstance(node, ast.Name):
        return node.id
    # For attributes, resolve the left-hand side first and then append this attribute.
    if isinstance(node, ast.Attribute):
        parent = dotted_name(node.value)
        if parent:
            return f"{parent}.{node.attr}"
    return None


class AuditVisitor(ast.NodeVisitor):
    """Walks one module's AST and accumulates Findings in `self.findings`.

    One visitor instance per file; audit() creates and discards them. Only the visit_* hooks
    for the node kinds we police are defined, everything else uses the default traversal.
    """

    def __init__(self, path: Path) -> None:
        """Remember the file and precompute its repo-relative posix path for allow-list lookups.

        Args:
            path: Absolute path of the module being audited; must live under ROOT.
        """

        # posix form so the allow-list keys ("app/ide/git.py") match on Windows runners too.
        self.path = path
        self.rel_path = path.relative_to(ROOT).as_posix()
        self.findings: list[Finding] = []

    def visit_Import(self, node: ast.Import) -> None:
        """Check each module in a plain `import a, b` statement."""

        for alias in node.names:
            self.check_import(alias.name, node.lineno)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """Check the source module and, separately, each imported name against the Qt deny-list."""

        # Relative imports (`from . import x`) have module None; "" matches nothing.
        module = node.module or ""
        self.check_import(module, node.lineno)
        # Names are checked individually because Qt classes are imported from broad modules
        # (PySide6.QtNetwork) that we do not want to ban wholesale.
        for alias in node.names:
            name = alias.name
            if name in BLOCKED_QT_NAMES:
                self.findings.append(Finding(self.path, node.lineno, BLOCKED_QT_NAMES[name]))

    def visit_Call(self, node: ast.Call) -> None:
        """Classify each call by its dotted name, then keep descending into its arguments."""

        name = dotted_name(node.func)
        if name:
            self.check_call(name, node.lineno)
        # generic_visit is required here: nested calls such as f(eval(x)) live in the arguments.
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        """Scan every string literal, including docstrings (they are Constant nodes too)."""

        if isinstance(node.value, str):
            self.check_string(node.value, node.lineno)

    def check_import(self, module: str, line: int) -> None:
        """Record a finding if `module` equals or sits under a blocked package and is not allow-listed.

        Args:
            module: Dotted module name from the import statement.
            line: 1-based source line for the report.
        """

        # Prefix match with a trailing dot so "socketserver" is not mistaken for "socket".
        for blocked, reason in BLOCKED_IMPORTS.items():
            if module == blocked or module.startswith(f"{blocked}."):
                if (self.rel_path, module) not in ALLOWED_IMPORTS:
                    self.findings.append(Finding(self.path, line, f"blocked import: {module} ({reason})"))

    def check_call(self, name: str, line: int) -> None:
        """Record a finding if the call name is denied and this file has no reviewed exception.

        Args:
            name: Dotted callee name from dotted_name().
            line: 1-based source line for the report.
        """

        # Allow-list first: the exception is per (file, name), never global.
        if (self.rel_path, name) in ALLOWED_CALLS:
            return
        for blocked, reason in BLOCKED_CALLS.items():
            if name == blocked:
                self.findings.append(Finding(self.path, line, f"blocked call: {name} ({reason})"))

    def check_string(self, value: str, line: int) -> None:
        """Flag a string constant that contains a forbidden fragment (URL or marker word).

        Args:
            value: The literal's text.
            line: 1-based source line for the report.
        """

        # The two policy scripts must contain the forbidden words to define them, so they are
        # exempt from this check only (their calls and imports are still audited).
        if self.rel_path in {"scripts/security_audit.py", "scripts/dependency_policy.py"}:
            return
        # Case-insensitive so "Sentry" or "HTTP://" cannot dodge the list.
        lowered = value.lower()
        for fragment, reason in BLOCKED_STRING_FRAGMENTS.items():
            if fragment in lowered:
                self.findings.append(Finding(self.path, line, f"blocked string: {fragment} ({reason})"))


def iter_python_files(scan_dirs: tuple[str, ...]) -> list[Path]:
    """Collect every *.py under the given repo-relative directories, sorted for stable output.

    Missing directories are skipped silently so the tool also works on partial checkouts.
    """

    files: list[Path] = []
    for scan_dir in scan_dirs:
        base = ROOT / scan_dir
        if base.exists():
            files.extend(sorted(base.rglob("*.py")))
    return files


def audit(scan_dirs: tuple[str, ...] = DEFAULT_SCAN_DIRS) -> list[Finding]:
    """Run the whole audit and return all findings (empty list means pass).

    Args:
        scan_dirs: Repo-relative directories to scan; tests pass a temp-style tree here.

    Returns:
        Findings in file order. A file that does not parse yields a "syntax error" finding
        instead of crashing, so a broken file can never silently pass the gate.
    """

    findings: list[Finding] = []
    for path in iter_python_files(scan_dirs):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        # Fail closed: an unparsable file cannot be vouched for, so report it and move on.
        except SyntaxError as exc:
            findings.append(Finding(path, exc.lineno or 1, f"syntax error: {exc.msg}"))
            continue
        visitor = AuditVisitor(path)
        visitor.visit(tree)
        findings.extend(visitor.findings)
    return findings


def main() -> int:
    """CLI entry point: print findings and return the process exit code (0 clean, 1 findings)."""

    parser = argparse.ArgumentParser(description="Fail-closed security audit for risky Python APIs.")
    parser.add_argument("scan_dirs", nargs="*", default=DEFAULT_SCAN_DIRS)
    args = parser.parse_args()

    # argparse yields a list; audit wants a tuple so the default constant and CLI input match.
    findings = audit(tuple(args.scan_dirs))
    for finding in findings:
        print(finding.render())
    # Non-zero is what makes CI and security_gate.sh stop the build.
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
