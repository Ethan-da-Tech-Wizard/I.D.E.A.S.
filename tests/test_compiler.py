import os
import pytest
from app.ide.compiler import compile_cpp, compile_rust
from app.security import SecurityError

def test_compiler_traversal_blocked(tmp_path):
    ws_path = str(tmp_path / "compiler_ws")
    os.makedirs(os.path.join(ws_path, "code"))
    os.makedirs(os.path.join(ws_path, "cache"))
    
    with pytest.raises(SecurityError):
        compile_cpp(ws_path, "../outside.cpp")

def test_compiler_nonexistent_file(tmp_path):
    ws_path = str(tmp_path / "compiler_ws")
    os.makedirs(os.path.join(ws_path, "code"))
    os.makedirs(os.path.join(ws_path, "cache"))
    
    with pytest.raises(SecurityError):
        compile_cpp(ws_path, "code/ghost.cpp")

def test_compiler_cpp_success(tmp_path):
    ws_path = str(tmp_path / "compiler_ws")
    os.makedirs(os.path.join(ws_path, "code"))
    os.makedirs(os.path.join(ws_path, "cache"))
    
    source = os.path.join(ws_path, "code", "hello.cpp")
    with open(source, "w") as f:
        f.write("#include <iostream>\nint main() { std::cout << \"hi\"; return 0; }\n")
        
    try:
        out_rel = compile_cpp(ws_path, "code/hello.cpp")
        assert out_rel == os.path.join("cache", "hello")
        assert os.path.isfile(os.path.join(ws_path, out_rel))
    except SecurityError as exc:
        # If g++ is not installed on test host, it's a valid path but raise not found
        assert "g++ compiler not found" in str(exc)
