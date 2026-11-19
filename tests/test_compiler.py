# ==============================================================================
# Program: tests/test_compiler.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: pins the safety contract of app/ide/compiler.py: source files must live inside the workspace
# code/ folder, and a successful build writes its binary to cache/. The compiler itself is an optional
# host tool, so the success test tolerates its absence instead of failing on bare CI machines.
# Run: python3 -m pytest tests/test_compiler.py -q
# ==============================================================================

import os
import pytest
from app.ide.compiler import compile_cpp, compile_rust
from app.security import SecurityError

# Scenario: the caller asks to compile a path that climbs out of the workspace ("../outside.cpp").
# Why it matters: the compile request comes from UI/AI-driven input, so a traversal here could make
# the app run a toolchain on arbitrary files. Expect SecurityError before any process starts.
def test_compiler_traversal_blocked(tmp_path):
    """Parent-directory paths must be rejected with SecurityError."""
    ws_path = str(tmp_path / "compiler_ws")
    # Minimal workspace skeleton: compile_cpp expects both code/ (sources) and cache/ (outputs).
    os.makedirs(os.path.join(ws_path, "code"))
    os.makedirs(os.path.join(ws_path, "cache"))
    
    with pytest.raises(SecurityError):
        compile_cpp(ws_path, "../outside.cpp")

# Scenario: a well-formed in-workspace path whose file does not exist. The compiler wrapper must
# report it as a SecurityError ("Source file does not exist") before launching g++, not as a build failure.
def test_compiler_nonexistent_file(tmp_path):
    """A missing source file raises SecurityError instead of invoking the compiler."""
    ws_path = str(tmp_path / "compiler_ws")
    os.makedirs(os.path.join(ws_path, "code"))
    os.makedirs(os.path.join(ws_path, "cache"))
    
    with pytest.raises(SecurityError):
        compile_cpp(ws_path, "code/ghost.cpp")

# Scenario: a real hello-world C++ file. Checks the output location contract (cache/<stem>, returned
# relative to the workspace) rather than the program's behaviour.
def test_compiler_cpp_success(tmp_path):
    """A valid C++ file compiles to cache/<stem> and the relative output path is returned."""
    ws_path = str(tmp_path / "compiler_ws")
    os.makedirs(os.path.join(ws_path, "code"))
    os.makedirs(os.path.join(ws_path, "cache"))
    
    # Smallest valid program; no external includes beyond the standard library.
    source = os.path.join(ws_path, "code", "hello.cpp")
    with open(source, "w") as f:
        f.write("#include <iostream>\nint main() { std::cout << \"hi\"; return 0; }\n")
        
    # The compiler is a host dependency, so the test must pass on machines without g++.
    try:
        # Return value is workspace-relative so callers can join it onto the workspace root themselves.
        out_rel = compile_cpp(ws_path, "code/hello.cpp")
        assert out_rel == os.path.join("cache", "hello")
        assert os.path.isfile(os.path.join(ws_path, out_rel))
    # Missing g++ is reported as SecurityError with a fixed message; any other SecurityError text would
    # indicate a real regression, so the message is asserted.
    except SecurityError as exc:
        # If g++ is not installed on test host, it's a valid path but raise not found
        assert "g++ compiler not found" in str(exc)
