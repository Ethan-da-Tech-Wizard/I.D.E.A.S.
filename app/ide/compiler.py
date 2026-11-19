# ==============================================================================
# Program: compiler.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: Executes build systems, language compilers, and linter toolchains asynchronously, capturing stdout/stderr streams and formatting error diagnostics for the problems view.
# ==============================================================================

# How this module fits: build helpers for compiling a single C++ or Rust file from the workspace's code/ folder into a binary in cache/. The header above talks about
# asynchronous builds and error formatting; the code is simpler: each function blocks until the compiler exits and raises SecurityError with the compiler's
# stderr text when the build fails. Nothing in app/ calls these functions yet (only tests/test_compiler.py does).
# Safety design: the compiler is started with an argument list (no shell), so file names cannot inject commands; the source must be under <workspace>/code and
# the output must land under <workspace>/cache (checked with is_safe_subpath); a missing compiler is reported as a clear error.
# Limits: no timeout (a compiler that hangs, or a source that triggers very long compilation, blocks the caller); the two functions repeat the same logic and
# differ only in the compiler and its flags; compiled code is not executed here.
import subprocess
import os
from typing import Tuple
from app.security import SecurityError
from app.storage.workspace import is_safe_subpath

def compile_cpp(workspace_path: str, source_rel_path: str) -> str:
    """Compile a C++ file with `g++ -O3` and return the binary's path relative to the workspace (cache/<name>).

    Args:
        workspace_path: workspace folder.
        source_rel_path: source file relative to the workspace; must be inside code/.

    Raises:
        SecurityError: source outside code/, source missing, output path escaping cache/, g++ not installed, or compile errors (message contains g++'s stderr).
    """
    # Ensure source file is within the code/ directory in the workspace

    # Absolute path of the source; checked against code/ next so "../" cannot point at other files.
    source_full = os.path.abspath(os.path.join(workspace_path, source_rel_path))
    code_dir = os.path.abspath(os.path.join(workspace_path, "code"))
    
    if not is_safe_subpath(code_dir, source_full):
        raise SecurityError("Source file must be inside the code/ folder of the workspace.")
        
    if not os.path.isfile(source_full):
        raise SecurityError("Source file does not exist.")
        
    # Output file will be placed in cache/ directory

    # Output name = source file name without extension. Two sources with the same base name in different sub-folders would overwrite each other's binary.
    base_name = os.path.splitext(os.path.basename(source_rel_path))[0]
    out_rel = os.path.join("cache", base_name)
    out_full = os.path.abspath(os.path.join(workspace_path, out_rel))
    
    cache_dir = os.path.abspath(os.path.join(workspace_path, "cache"))

    # Defensive second check on the output path (the name comes from basename, so this should always pass).
    if not is_safe_subpath(cache_dir, out_full):
        raise SecurityError("Output binary path traversal detected.")

    try:
        res = subprocess.run(
            # -O3 asks for heavy optimisation (slower to compile, faster to run). The command is a list, never a shell string.
            ["g++", "-O3", source_full, "-o", out_full],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False
        )

        # A failed compile is reported through SecurityError (the IDE shows its text) even though it is not a security problem; callers catch that one type.
        if res.returncode != 0:
            raise SecurityError(f"Compilation failed:\n{res.stderr}")
        return out_rel

    # raised by subprocess when the executable is not on PATH.
    except FileNotFoundError:
        raise SecurityError("g++ compiler not found on the host system.")

def compile_rust(workspace_path: str, source_rel_path: str) -> str:
    """Compile a Rust file with `rustc -O` and return the binary's path relative to the workspace (cache/<name>); same rules and errors as compile_cpp."""
    source_full = os.path.abspath(os.path.join(workspace_path, source_rel_path))
    code_dir = os.path.abspath(os.path.join(workspace_path, "code"))
    
    if not is_safe_subpath(code_dir, source_full):
        raise SecurityError("Source file must be inside the code/ folder of the workspace.")
        
    if not os.path.isfile(source_full):
        raise SecurityError("Source file does not exist.")
        
    base_name = os.path.splitext(os.path.basename(source_rel_path))[0]
    out_rel = os.path.join("cache", base_name)
    out_full = os.path.abspath(os.path.join(workspace_path, out_rel))
    
    cache_dir = os.path.abspath(os.path.join(workspace_path, "cache"))
    if not is_safe_subpath(cache_dir, out_full):
        raise SecurityError("Output binary path traversal detected.")

    try:
        res = subprocess.run(
            ["rustc", "-O", source_full, "-o", out_full],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False
        )
        if res.returncode != 0:
            raise SecurityError(f"Compilation failed:\n{res.stderr}")
        return out_rel
    except FileNotFoundError:
        raise SecurityError("rustc compiler not found on the host system.")
