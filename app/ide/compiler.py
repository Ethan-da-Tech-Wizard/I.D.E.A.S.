import subprocess
import os
from typing import Tuple
from app.security import SecurityError
from app.storage.workspace import is_safe_subpath

def compile_cpp(workspace_path: str, source_rel_path: str) -> str:
    # Ensure source file is within the code/ directory in the workspace
    source_full = os.path.abspath(os.path.join(workspace_path, source_rel_path))
    code_dir = os.path.abspath(os.path.join(workspace_path, "code"))
    
    if not is_safe_subpath(code_dir, source_full):
        raise SecurityError("Source file must be inside the code/ folder of the workspace.")
        
    if not os.path.isfile(source_full):
        raise SecurityError("Source file does not exist.")
        
    # Output file will be placed in cache/ directory
    base_name = os.path.splitext(os.path.basename(source_rel_path))[0]
    out_rel = os.path.join("cache", base_name)
    out_full = os.path.abspath(os.path.join(workspace_path, out_rel))
    
    cache_dir = os.path.abspath(os.path.join(workspace_path, "cache"))
    if not is_safe_subpath(cache_dir, out_full):
        raise SecurityError("Output binary path traversal detected.")

    try:
        res = subprocess.run(
            ["g++", "-O3", source_full, "-o", out_full],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False
        )
        if res.returncode != 0:
            raise SecurityError(f"Compilation failed:\n{res.stderr}")
        return out_rel
    except FileNotFoundError:
        raise SecurityError("g++ compiler not found on the host system.")

def compile_rust(workspace_path: str, source_rel_path: str) -> str:
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
