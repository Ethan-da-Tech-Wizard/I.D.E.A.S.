from __future__ import annotations

import ast
import json
import os
from pathlib import Path
from typing import Callable


MAX_OPEN_FILE_BYTES = 2 * 1024 * 1024
MAX_PROJECT_FILE_BYTES = 2 * 1024 * 1024
MAX_SCRIPT_STATEMENTS = 64
MAX_SAMPLE_NAME_LENGTH = 240
MAX_OUTPUT_FILE_BYTES = 200 * 1024 * 1024
ALLOWED_SAMPLE_SUFFIXES = {".wav"}


class SecurityError(ValueError):
    """Raised when user-controlled input crosses an application safety boundary."""


def read_text_file_safely(path: str, max_bytes: int = MAX_OPEN_FILE_BYTES) -> str:
    file_path = Path(path).expanduser().resolve(strict=True)
    if not file_path.is_file():
        raise SecurityError("Only regular files can be opened.")

    size = file_path.stat().st_size
    if size > max_bytes:
        raise SecurityError(f"File is too large to open safely ({size} bytes).")

    data = file_path.read_bytes()
    if b"\x00" in data:
        raise SecurityError("Binary files cannot be opened in the editor.")

    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SecurityError("Only UTF-8 text files can be opened.") from exc


def resolve_user_output_file(path: str, required_suffix: str, max_parent_depth: int = 1024) -> str:
    if not path:
        raise SecurityError("No output path selected.")
    output = Path(path).expanduser()
    if output.suffix.lower() != required_suffix.lower():
        output = output.with_suffix(required_suffix)
    parent = output.parent.resolve(strict=True)
    if not parent.is_dir():
        raise SecurityError("Output parent must be a directory.")
    if len(output.parts) > max_parent_depth:
        raise SecurityError("Output path is too deep.")
    resolved = parent / output.name
    if resolved.exists() and not resolved.is_file():
        raise SecurityError("Output path must be a regular file.")
    return str(resolved)


def write_text_file_safely(path: str, content: str, required_suffix: str, max_bytes: int) -> str:
    encoded = content.encode("utf-8")
    if len(encoded) > max_bytes:
        raise SecurityError("Output content is too large.")
    output_path = resolve_user_output_file(path, required_suffix)
    with open(output_path, "w", encoding="utf-8") as handle:
        handle.write(content)
    return output_path


def read_json_file_safely(path: str, max_bytes: int = MAX_PROJECT_FILE_BYTES) -> object:
    content = read_text_file_safely(path, max_bytes=max_bytes)
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise SecurityError("Project file is not valid JSON.") from exc


def validate_sample_name(sample_name: str) -> str:
    if not isinstance(sample_name, str):
        raise SecurityError("Sample name must be text.")
    if not sample_name or len(sample_name) > MAX_SAMPLE_NAME_LENGTH:
        raise SecurityError("Sample name is empty or too long.")
    if os.path.isabs(sample_name):
        raise SecurityError("Sample names must be relative paths.")
    parts = Path(sample_name).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise SecurityError("Sample name cannot contain path traversal.")
    if Path(sample_name).suffix.lower() not in ALLOWED_SAMPLE_SUFFIXES:
        raise SecurityError("Only bundled WAV samples can be played.")
    return sample_name


def resolve_sample_path(samples_root: str, sample_name: str) -> str:
    sample_name = validate_sample_name(sample_name)

    candidate = Path(samples_root).resolve(strict=True).joinpath(sample_name).resolve(strict=False)
    root = Path(samples_root).resolve(strict=True)
    if root != candidate and root not in candidate.parents:
        raise SecurityError("Sample path escapes the bundled sample directory.")
    if not candidate.is_file():
        raise SecurityError("Sample was not found.")
    return str(candidate)


def list_bundled_samples(samples_root: str) -> list[str]:
    root = Path(samples_root).resolve(strict=True)
    sample_names: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel_path = path.relative_to(root).as_posix()
        try:
            validate_sample_name(rel_path)
            resolve_sample_path(str(root), rel_path)
        except SecurityError:
            continue
        sample_names.append(rel_path)
    return sorted(sample_names)


class _SafeScriptValidator(ast.NodeVisitor):
    def __init__(self) -> None:
        self.calls: list[str] = []

    def visit_Module(self, node: ast.Module) -> None:
        if len(node.body) > MAX_SCRIPT_STATEMENTS:
            raise SecurityError("Script has too many statements.")
        self.generic_visit(node)

    def visit_Expr(self, node: ast.Expr) -> None:
        if not isinstance(node.value, ast.Call):
            raise SecurityError("Only play_sample('name.wav') calls are allowed.")
        self.visit(node.value)

    def visit_Call(self, node: ast.Call) -> None:
        if not isinstance(node.func, ast.Name) or node.func.id != "play_sample":
            raise SecurityError("Only play_sample('name.wav') calls are allowed.")
        if node.keywords or len(node.args) != 1:
            raise SecurityError("play_sample requires exactly one positional argument.")
        arg = node.args[0]
        if not isinstance(arg, ast.Constant) or not isinstance(arg.value, str):
            raise SecurityError("play_sample argument must be a string literal.")
        self.calls.append(validate_sample_name(arg.value))

    def generic_visit(self, node: ast.AST) -> None:
        blocked_nodes = (
            ast.Import,
            ast.ImportFrom,
            ast.Attribute,
            ast.Assign,
            ast.AnnAssign,
            ast.AugAssign,
            ast.Delete,
            ast.For,
            ast.While,
            ast.If,
            ast.With,
            ast.Try,
            ast.FunctionDef,
            ast.AsyncFunctionDef,
            ast.ClassDef,
            ast.Lambda,
            ast.ListComp,
            ast.SetComp,
            ast.DictComp,
            ast.GeneratorExp,
            ast.Await,
            ast.Yield,
            ast.YieldFrom,
            ast.Subscript,
        )
        if isinstance(node, blocked_nodes):
            raise SecurityError("This script feature is not allowed.")
        super().generic_visit(node)


def parse_safe_script(code: str) -> list[str]:
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        raise SecurityError(f"Script syntax error: {exc.msg}.") from exc

    validator = _SafeScriptValidator()
    validator.visit(tree)
    return validator.calls


def run_safe_script(code: str, play_sample: Callable[[str], None]) -> list[str]:
    sample_names = parse_safe_script(code)
    for sample_name in sample_names:
        play_sample(sample_name)
    return sample_names
