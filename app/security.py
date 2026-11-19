# ==============================================================================
# Program: security.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: Enforces runtime security policies, sandbox path containment to prevent directory traversal vulnerabilities, safe text file reading, sample path resolution, and sanitized subprocess script execution.
# ==============================================================================

# How this module fits: every place where the app touches a user-chosen file or runs user-written script text goes through here first.
# app/application.py uses read_text_file_safely (opening files),
# resolve_sample_path and run_safe_script (the "Run code" button); app/project.py uses read_json_file_safely and write_text_file_safely (project
# load and save); app/ui/daw.py uses list_bundled_samples and resolve_sample_path; app/services/audio_export.py uses resolve_user_output_file;
# app/ide/keybindings.py and app/ui/search_panel.py use read_text_file_safely. Keeping these checks in one small module makes the security rules easy to review and test (tests/test_security.py,
# test_security_audit.py and test_project_security.py exercise them).
#
# Design rules: (1) fail closed: anything unexpected raises SecurityError (a ValueError subclass) and the caller shows a message;
# (2) user scripts are never executed with Python's exec(): they are parsed into a syntax tree, checked against a tiny whitelist, and only the
# two supported actions (play_sample, print) are carried out by the app itself; (3) hard size limits on everything read or written.
# Known limits: some filesystem problems raise ordinary OSError subclasses (FileNotFoundError from resolve(strict=True)) instead of SecurityError,
# so callers must handle both; the checks are not atomic with the later open() (a file swapped in between can still win, the classic time-of-check
# to time-of-use gap); a destination that is itself a symlink to a regular file is accepted by resolve_user_output_file and followed on write.
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
from typing import Callable


# Size limits in bytes. 2 MiB is far more than hand-written source or a project file needs, and small enough that reading a file fully into memory is harmless.
# MAX_OUTPUT_FILE_BYTES (200 MiB) is the ceiling for exported audio; MAX_SCRIPT_STATEMENTS bounds how long a user script can be; MAX_SAMPLE_NAME_LENGTH
# (240 characters) keeps names inside common filesystem limits (255 bytes) with room for a parent folder.
MAX_OPEN_FILE_BYTES = 2 * 1024 * 1024
MAX_PROJECT_FILE_BYTES = 2 * 1024 * 1024
MAX_SCRIPT_STATEMENTS = 64
MAX_SAMPLE_NAME_LENGTH = 240
MAX_OUTPUT_FILE_BYTES = 200 * 1024 * 1024

# Only WAV files can be played from the bundled sample library. A whitelist of one type is simpler and safer than trying to recognise bad ones.
ALLOWED_SAMPLE_SUFFIXES = {".wav"}


# Subclass of ValueError so code that only catches ValueError still handles it, while security-aware callers can catch this type alone.
class SecurityError(ValueError):
    """Raised when user-controlled input crosses an application safety boundary."""


def read_text_file_safely(path: str, max_bytes: int = MAX_OPEN_FILE_BYTES) -> str:
    """Read a UTF-8 text file the user chose, refusing anything unsafe to load into the editor.

    Args:
        path: file path (a leading ~ is expanded).
        max_bytes: largest allowed file size in bytes (default 2 MiB).

    Returns:
        The decoded text.

    Raises:
        SecurityError: not a regular file, too large, contains NUL bytes (binary), or not valid UTF-8.
        FileNotFoundError: the path does not exist (resolve(strict=True) is not converted).
    """
    # Resolve realpath strictly to prevent symlink traversal attacks escaping intended directories

    # resolve() follows symlinks and removes ".." parts, so the checks below apply to the real file. strict=True makes a missing path fail immediately.
    file_path = Path(path).expanduser().resolve(strict=True)

    # Reject directories and special files (devices, named pipes). Reading a pipe could block forever.
    if not file_path.is_file():
        raise SecurityError("Only regular files can be opened.")

    # Guard against memory exhaustion by enforcing explicit file size threshold (2 MiB limit)

    # Check the size before reading, so a huge file is never loaded into memory.
    size = file_path.stat().st_size
    if size > max_bytes:
        raise SecurityError(f"File is too large to open safely ({size} bytes).")

    # Read binary bytes and check for null bytes to mitigate C-string truncation attacks
    data = file_path.read_bytes()

    # A NUL byte almost never occurs in text and is the usual sign of a binary file (image, executable). Refusing it avoids showing garbage and avoids passing NULs on to C code that treats them as string ends.
    if b"\x00" in data:
        raise SecurityError("Binary files cannot be opened in the editor.")

    try:
        # Strict decoding: invalid bytes raise instead of being silently replaced, so the editor never saves back a file it has silently altered.
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SecurityError("Only UTF-8 text files can be opened.") from exc


def resolve_user_output_file(path: str, required_suffix: str, max_parent_depth: int = 1024) -> str:
    """Validate a save-as destination and return the absolute path to write.

    Args:
        path: destination chosen by the user (a leading ~ is expanded).
        required_suffix: extension the file must have, such as ".wav" or ".json"; it is appended or replaced when different.
        max_parent_depth: largest allowed number of path components.

    Returns:
        The resolved destination path as a string. The file is not created here.

    Raises:
        SecurityError: empty path, parent not a directory, path too deep, or the destination exists and is not a regular file.
        FileNotFoundError: the parent folder does not exist.
    """
    if not path:
        raise SecurityError("No output path selected.")

    # Do not resolve the file itself yet: it may not exist. Only its parent folder is resolved below.
    output = Path(path).expanduser()
    if output.suffix.lower() != required_suffix.lower():
        # Force the expected extension so an export meant to be a ".wav" cannot be written under another name (for example ".exe").
        output = output.with_suffix(required_suffix)

    # The parent must exist; resolving it expands ".." and symlinks in the folder part of the path. The final name is re-attached afterwards (parent / output.name) so a name like "../x" cannot reach outside the checked parent.
    parent = output.parent.resolve(strict=True)
    if not parent.is_dir():
        raise SecurityError("Output parent must be a directory.")

    # Defensive limit on absurdly deep paths; it also bounds the work done by later filesystem calls.
    if len(output.parts) > max_parent_depth:
        raise SecurityError("Output path is too deep.")
    resolved = parent / output.name

    # Never overwrite a directory or device. An existing regular file is allowed (the file dialog already asked the user to confirm overwriting).
    if resolved.exists() and not resolved.is_file():
        raise SecurityError("Output path must be a regular file.")
    return str(resolved)


def write_text_file_safely(path: str, content: str, required_suffix: str, max_bytes: int) -> str:
    """Write `content` as UTF-8 to a validated destination and return the path used.

    Args:
        path, required_suffix: see resolve_user_output_file.
        content: text to save.
        max_bytes: largest allowed size of the encoded text.

    Raises:
        SecurityError: content too large or destination invalid.
    The size is checked before any file is touched, so a refused write leaves the disk unchanged. The write itself truncates the file first and
    is not atomic: a crash mid-write could leave a partial file.
    """
    encoded = content.encode("utf-8")
    if len(encoded) > max_bytes:
        raise SecurityError("Output content is too large.")
    output_path = resolve_user_output_file(path, required_suffix)
    with open(output_path, "w", encoding="utf-8") as handle:
        handle.write(content)
    return output_path


def read_json_file_safely(path: str, max_bytes: int = MAX_PROJECT_FILE_BYTES) -> object:
    """Read and parse a JSON project file, applying all the checks of read_text_file_safely first.

    Raises:
        SecurityError: unsafe file (see above) or invalid JSON. The returned value is not shape-checked here; callers such as app/project.py validate its contents.
    """
    content = read_text_file_safely(path, max_bytes=max_bytes)
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise SecurityError("Project file is not valid JSON.") from exc


def validate_sample_name(sample_name: str) -> str:
    """Check that `sample_name` is a safe relative WAV path inside the sample library; return it unchanged if so.

    Rejects: non-text, empty or longer than 240 characters, absolute paths, any "." or ".." component (path traversal) and any extension other than .wav.
    Raises:
        SecurityError: with a message saying which rule failed.
    """
    if not isinstance(sample_name, str):
        raise SecurityError("Sample name must be text.")
    if not sample_name or len(sample_name) > MAX_SAMPLE_NAME_LENGTH:
        raise SecurityError("Sample name is empty or too long.")
    if os.path.isabs(sample_name):
        raise SecurityError("Sample names must be relative paths.")

    # Looking at components (not the raw string) catches "a/../b" and "./a" while still allowing dots inside ordinary names such as "kick.v2.wav".
    parts = Path(sample_name).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise SecurityError("Sample name cannot contain path traversal.")
    if Path(sample_name).suffix.lower() not in ALLOWED_SAMPLE_SUFFIXES:
        raise SecurityError("Only bundled WAV samples can be played.")
    return sample_name


def resolve_sample_path(samples_root: str, sample_name: str) -> str:
    """Turn a sample name into the absolute path of an existing file inside `samples_root`, or raise.

    This is the second line of defence after validate_sample_name: even a name that passed the text checks is resolved and then
    must still lie under the root, which also catches a symlink inside the library that points elsewhere.
    Raises:
        SecurityError: invalid name, path escaping the root, or file missing.
    """
    sample_name = validate_sample_name(sample_name)

    # resolve(strict=False) because the file may legitimately be absent (that case gets its own message below); symlinks that exist are still followed.
    candidate = Path(samples_root).resolve(strict=True).joinpath(sample_name).resolve(strict=False)
    root = Path(samples_root).resolve(strict=True)

    # Containment test: the candidate must be the root or a descendant. Comparing resolved paths component by component avoids the classic prefix bug where "/samples2" would pass a plain startswith("/samples") check.
    if root != candidate and root not in candidate.parents:
        raise SecurityError("Sample path escapes the bundled sample directory.")
    if not candidate.is_file():
        raise SecurityError("Sample was not found.")
    return str(candidate)


def list_bundled_samples(samples_root: str) -> list[str]:
    """Return the sorted POSIX-style relative names of all valid samples below `samples_root`.

    Every candidate must pass the same two checks used when a sample is played, so the list never offers a file that play_sample would refuse.
    Files that fail validation are skipped silently.
    """
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


# Walks the syntax tree of a user script and records what it asks for. It does not run anything. Accepted script (at most 64 statements):
#     play_sample("name.wav")
#     print("text")
# Everything else is rejected: imports, variables, loops, conditions, attribute access, function or class definitions, comprehensions, subscripts, etc.
# Results: `calls` lists the sample names (kept for older callers) and `actions` the ordered ("play" | "print", argument) pairs to carry out.
class _SafeScriptValidator(ast.NodeVisitor):
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.actions: list[tuple[str, str]] = []

    # Statement budget: a longer script is refused outright, so a script cannot queue unbounded work.
    def visit_Module(self, node: ast.Module) -> None:
        if len(node.body) > MAX_SCRIPT_STATEMENTS:
            raise SecurityError("Script has too many statements.")
        self.generic_visit(node)

    # A statement that is only an expression must be a call. A bare string or number (including a docstring) is refused so that every accepted line has a visible effect.
    def visit_Expr(self, node: ast.Expr) -> None:
        if not isinstance(node.value, ast.Call):
            raise SecurityError("Only play_sample('name.wav') and print('text') calls are allowed.")
        self.visit(node.value)

    # Call rules: plain function name (so no obj.method() or chained calls), one of the two allowed names, exactly one positional argument, and that argument must be a string literal.
    # String literals only means no variables, f-strings or calls can smuggle other code in; play_sample names are additionally validated as safe sample paths here.
    # This method does not recurse into the arguments, which is safe because they were just checked to be constants.
    def visit_Call(self, node: ast.Call) -> None:
        if not isinstance(node.func, ast.Name):
            raise SecurityError("Only simple function calls are allowed.")
        func_name = node.func.id
        if func_name not in {"play_sample", "print"}:
            raise SecurityError("Only play_sample('name.wav') and print('text') calls are allowed.")
        if node.keywords or len(node.args) != 1:
            raise SecurityError("Functions require exactly one positional argument.")
        arg = node.args[0]
        if not isinstance(arg, ast.Constant) or not isinstance(arg.value, str):
            raise SecurityError("Function argument must be a string literal.")
        
        if func_name == "play_sample":
            val = validate_sample_name(arg.value)
            self.calls.append(val)
            self.actions.append(("play", val))
        elif func_name == "print":
            self.actions.append(("print", arg.value))

    # Deny list applied to every node that has no dedicated visit_ method. Anything on the list ends validation with an error. Together with the strict call rules above, the effect is a whitelist: the only statement shape that can carry a result is a validated call.
    # Statements that remain allowed but have no effect (for example `pass`) are harmless because the script is never executed.
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
    """Validate `code` and return the list of sample names it would play; nothing is executed.

    Raises:
        SecurityError: syntax error or a disallowed construct. In the current code only the tests call this function (the app calls run_safe_script directly); it is the way to check a script without performing it.
    """
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        raise SecurityError(f"Script syntax error: {exc.msg}.") from exc

    validator = _SafeScriptValidator()
    validator.visit(tree)
    return validator.calls


def run_safe_script(code: str, play_sample: Callable[[str], None], print_func: Callable[[str], None] | None = None) -> list[str]:
    """Validate `code`, then perform its actions in order through the callbacks the caller supplies; return the sample names played.

    Args:
        code: script text.
        play_sample: called with each validated sample name (the caller maps it to audio).
        print_func: called with each text to print; if None, print lines are ignored.

    Because the whole script is validated before the first action runs, a script with an error on its last line does nothing at all (no half-run).
    The code string itself is never passed to exec() or eval().
    """
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        raise SecurityError(f"Script syntax error: {exc.msg}.") from exc

    validator = _SafeScriptValidator()
    validator.visit(tree)
    
    for action_type, arg in validator.actions:
        if action_type == "play":
            play_sample(arg)
        elif action_type == "print" and print_func is not None:
            print_func(arg)
            
    return validator.calls
