# ==============================================================================
# Program: workspace.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: Serializes and restores workspace layout configurations, open editor documents, cursor coordinates, and dock panel arrangements between user sessions.
# ==============================================================================

# How this module fits: a "workspace" is a folder the user opens as a project. It holds the project file (project.ideas, JSON: layout, open documents, DAW state)
# and fixed sub-folders for different kinds of content. app/application.py calls save_workspace / load_workspace for Save and Open project;
# app/services/audio/sample_import.py and app/services/orchestration.py call get_workspace_dir to find where to put imported audio and cache files;
# app/ai/core.py and app/ide/compiler.py use is_safe_subpath to keep file operations inside the workspace.
# Security role: this is the application's path-containment helper. is_safe_subpath compares absolute paths as text; it does NOT resolve symlinks, so a symlink
# inside a workspace that points elsewhere is not detected (app/security.py resolves symlinks for the file-level checks that matter most).
import os
import json
import shutil
from typing import Any
from app.security import SecurityError, read_json_file_safely, write_text_file_safely, MAX_PROJECT_FILE_BYTES

# Fixed sub-folders created in every workspace: source code, audio samples/exports, 3D scene assets, visual-effects assets, and a cache for build outputs and AI proposals. Only these names are accepted by get_workspace_dir.
WORKSPACE_DIRS = ["code", "audio", "3d", "vfx", "cache"]

def is_safe_subpath(base: str, target: str) -> bool:
    """Return True if `target` is `base` itself or lies somewhere below it.

    Both paths are made absolute (which also removes ".." parts) and compared as strings; the separator is appended to `base` so that "/work/ab" is not accepted as being inside "/work/a".
    It does not touch the filesystem and does not follow symlinks.
    """
    base = os.path.abspath(base)
    target = os.path.abspath(target)
    return target.startswith(base + os.sep) or target == base

def init_workspace(path: str) -> None:
    """Create the workspace folder and its standard sub-folders if they are missing (safe to call repeatedly; existing content is never touched)."""
    if not os.path.exists(path):
        os.makedirs(path, exist_ok=True)
    
    for d in WORKSPACE_DIRS:
        dir_path = os.path.join(path, d)
        if not os.path.exists(dir_path):
            os.makedirs(dir_path, exist_ok=True)

def sanitize_workspace_state(raw_state: dict[str, Any]) -> dict[str, Any]:
    """Clean a project state dictionary using the same rules as project files (see app/project.py: unknown keys dropped, values type-checked and clamped).

    The import is inside the function to avoid a circular import, because app/project.py imports from this package as well.
    """
    # Reuse sanitization from project.py or implement here
    from app.project import sanitize_project_state
    return sanitize_project_state(raw_state)

def save_workspace(path: str, state: dict[str, Any]) -> str:
    """Write `state` to <path>/project.ideas and return the file path.

    The state is sanitised first, serialised with sorted keys and indentation (so the file diffs cleanly in version control), size-checked and written through
    write_text_file_safely, which also enforces the .ideas extension.
    Raises:
        SecurityError: `path` is not an existing directory, the target escapes the folder, or content is too large.
    """
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
    """Read <path>/project.ideas and return the cleaned state.

    Raises:
        SecurityError: not a directory, no project file, bad JSON, file too large, or the top-level JSON value is not an object.
    Whatever the file contains is sanitised before it is returned, so a hand-edited or hostile project file cannot inject unexpected values into the app.
    """
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
    """Return the path of one standard sub-folder of the workspace.

    Args:
        category: one of WORKSPACE_DIRS. Any other name raises SecurityError, which stops callers from building paths from user text.
    """
    if category not in WORKSPACE_DIRS:
        raise SecurityError(f"Invalid workspace category: {category}")
        
    dir_path = os.path.join(base_path, category)
    if not is_safe_subpath(base_path, dir_path):
        raise SecurityError("Path traversal detected.")
        
    return dir_path
