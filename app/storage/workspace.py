import os
import json
import shutil
from typing import Any
from app.security import SecurityError, read_json_file_safely, write_text_file_safely, MAX_PROJECT_FILE_BYTES

WORKSPACE_DIRS = ["code", "audio", "3d", "vfx", "cache"]

def is_safe_subpath(base: str, target: str) -> bool:
    base = os.path.abspath(base)
    target = os.path.abspath(target)
    return target.startswith(base + os.sep) or target == base

def init_workspace(path: str) -> None:
    if not os.path.exists(path):
        os.makedirs(path, exist_ok=True)
    
    for d in WORKSPACE_DIRS:
        dir_path = os.path.join(path, d)
        if not os.path.exists(dir_path):
            os.makedirs(dir_path, exist_ok=True)

def sanitize_workspace_state(raw_state: dict[str, Any]) -> dict[str, Any]:
    # Reuse sanitization from project.py or implement here
    from app.project import sanitize_project_state
    return sanitize_project_state(raw_state)

def save_workspace(path: str, state: dict[str, Any]) -> str:
    if not os.path.isdir(path):
        raise SecurityError("Workspace path must be a directory.")
    
    init_workspace(path)
    clean_state = sanitize_workspace_state(state)
    content = json.dumps(clean_state, indent=2, sort_keys=True)
    
    project_file_path = os.path.join(path, "project.ideas")
    if not is_safe_subpath(path, project_file_path):
        raise SecurityError("Path traversal detected.")
        
    return write_text_file_safely(project_file_path, content + "\n", ".ideas", MAX_PROJECT_FILE_BYTES)

def load_workspace(path: str) -> dict[str, Any]:
    if not os.path.isdir(path):
        raise SecurityError("Workspace path must be a directory.")
        
    project_file_path = os.path.join(path, "project.ideas")
    if not os.path.exists(project_file_path):
        raise SecurityError("No project.ideas found in workspace.")
        
    if not is_safe_subpath(path, project_file_path):
        raise SecurityError("Path traversal detected.")
        
    data = read_json_file_safely(project_file_path, max_bytes=MAX_PROJECT_FILE_BYTES)
    if not isinstance(data, dict):
        raise SecurityError("Project root must be an object.")
        
    return sanitize_workspace_state(data)

def get_workspace_dir(base_path: str, category: str) -> str:
    if category not in WORKSPACE_DIRS:
        raise SecurityError(f"Invalid workspace category: {category}")
        
    dir_path = os.path.join(base_path, category)
    if not is_safe_subpath(base_path, dir_path):
        raise SecurityError("Path traversal detected.")
        
    return dir_path
