from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCAN_DIRS = ("app", "scripts")

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

BLOCKED_QT_NAMES = {
    "QWebEngineView": "embedded browser",
    "QWebView": "embedded browser",
    "QNetworkAccessManager": "network client",
    "QNetworkRequest": "network client",
    "QTcpSocket": "network socket",
    "QUdpSocket": "network socket",
}

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
    path: Path
    line: int
    message: str

    def render(self) -> str:
        rel = self.path.relative_to(ROOT)
        return f"{rel}:{self.line}: {self.message}"


def dotted_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = dotted_name(node.value)
        if parent:
            return f"{parent}.{node.attr}"
    return None


class AuditVisitor(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.rel_path = path.relative_to(ROOT).as_posix()
        self.findings: list[Finding] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.check_import(alias.name, node.lineno)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        self.check_import(module, node.lineno)
        for alias in node.names:
            name = alias.name
            if name in BLOCKED_QT_NAMES:
                self.findings.append(Finding(self.path, node.lineno, BLOCKED_QT_NAMES[name]))

    def visit_Call(self, node: ast.Call) -> None:
        name = dotted_name(node.func)
        if name:
            self.check_call(name, node.lineno)
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str):
            self.check_string(node.value, node.lineno)

    def check_import(self, module: str, line: int) -> None:
        for blocked, reason in BLOCKED_IMPORTS.items():
            if module == blocked or module.startswith(f"{blocked}."):
                if (self.rel_path, module) not in ALLOWED_IMPORTS:
                    self.findings.append(Finding(self.path, line, f"blocked import: {module} ({reason})"))

    def check_call(self, name: str, line: int) -> None:
        if (self.rel_path, name) in ALLOWED_CALLS:
            return
        for blocked, reason in BLOCKED_CALLS.items():
            if name == blocked:
                self.findings.append(Finding(self.path, line, f"blocked call: {name} ({reason})"))

    def check_string(self, value: str, line: int) -> None:
        if self.rel_path in {"scripts/security_audit.py", "scripts/dependency_policy.py"}:
            return
        lowered = value.lower()
        for fragment, reason in BLOCKED_STRING_FRAGMENTS.items():
            if fragment in lowered:
                self.findings.append(Finding(self.path, line, f"blocked string: {fragment} ({reason})"))


def iter_python_files(scan_dirs: tuple[str, ...]) -> list[Path]:
    files: list[Path] = []
    for scan_dir in scan_dirs:
        base = ROOT / scan_dir
        if base.exists():
            files.extend(sorted(base.rglob("*.py")))
    return files


def audit(scan_dirs: tuple[str, ...] = DEFAULT_SCAN_DIRS) -> list[Finding]:
    findings: list[Finding] = []
    for path in iter_python_files(scan_dirs):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            findings.append(Finding(path, exc.lineno or 1, f"syntax error: {exc.msg}"))
            continue
        visitor = AuditVisitor(path)
        visitor.visit(tree)
        findings.extend(visitor.findings)
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail-closed security audit for risky Python APIs.")
    parser.add_argument("scan_dirs", nargs="*", default=DEFAULT_SCAN_DIRS)
    args = parser.parse_args()

    findings = audit(tuple(args.scan_dirs))
    for finding in findings:
        print(finding.render())
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
