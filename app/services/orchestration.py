# ==============================================================================
# Program: orchestration.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: Coordinates complex multi-service workflows across audio synthesis, game engine rendering, and video composition pipelines.
# ==============================================================================

# How this module fits: a tiny shared "whiteboard" so several workers (people or AI agents) editing the same workspace do not collide. It stores two things in
# <workspace>/cache/orchestration_state.json: file locks (which agent currently owns which workspace file) and feature statuses (PENDING, IN_PROGRESS, COMPLETED, BLOCKED)
# that let one agent check whether another's feature is finished before building on it. The audio, engine and video features can consult it to coordinate
# work. In the current code only tests/test_orchestration.py calls it; no UI uses it yet.
# State shape: {"locks": {relative_path: agent_id}, "features": {name: {"status", "assigned_agent", "reason"}}}.
# Limits (cooperative, not enforced): locks are advisory (nothing stops code that ignores them); every operation is a read-modify-write of one JSON file without a
# file lock, so two processes updating at the same instant can lose one update; the state file is not validated when loaded (any valid JSON is returned).
# Known bug (do not rely on a fresh state being independent): DEFAULT_STATE.copy() is a shallow copy, so the nested "locks" and "features" dictionaries are the same objects
# each time. Locking a file in a workspace that has no state file yet changes the shared default in memory, and the next workspace loaded without a state file sees that lock.
import os
import json
from typing import Dict, Any, Optional
from app.security import SecurityError
from app.storage.workspace import get_workspace_dir, is_safe_subpath

# Empty state used when no state file exists or it cannot be parsed. See the module notes about its shallow-copy hazard.
DEFAULT_STATE: Dict[str, Any] = {
    "locks": {},
    "features": {}
}

def load_orchestration_state(workspace_path: str) -> Dict[str, Any]:
    """Return the saved coordination state of a workspace, or an empty default state.

    A missing file gives the default state; a damaged JSON file also gives the default (silently), so a corrupt cache never blocks work, at the cost of forgetting the locks in it.
    Raises:
        SecurityError: not a directory, or the state path would escape the workspace.
    """
    if not os.path.isdir(workspace_path):
        raise SecurityError("Invalid workspace directory.")
        
    cache_dir = get_workspace_dir(workspace_path, "cache")
    state_file = os.path.join(cache_dir, "orchestration_state.json")
    
    if not is_safe_subpath(workspace_path, state_file):
        raise SecurityError("Path traversal attempt in orchestration state.")
        
    if not os.path.exists(state_file):
        return DEFAULT_STATE.copy()
        
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return DEFAULT_STATE.copy()

def save_orchestration_state(workspace_path: str, state: Dict[str, Any]) -> None:
    """Write the state to <workspace>/cache/orchestration_state.json (indented for readability). The cache folder must already exist (workspace initialisation creates it). Not atomic."""
    cache_dir = get_workspace_dir(workspace_path, "cache")
    state_file = os.path.join(cache_dir, "orchestration_state.json")
    
    if not is_safe_subpath(workspace_path, state_file):
        raise SecurityError("Path traversal attempt in orchestration state.")
        
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

def lock_file(workspace_path: str, agent_id: str, file_path: str) -> bool:
    """Try to lock a workspace file for `agent_id`; True if the agent now holds the lock, False if another agent holds it.

    The path is normalised relative to the workspace (so "a/../b.py" and "b.py" are the same lock) and must stay inside it. Locking a file the agent already holds succeeds (re-entrant).
    """
    # Normalize path relative to workspace

    # Normalise to a workspace-relative path so every spelling of the same file maps to the same key.
    rel_path = os.path.relpath(os.path.abspath(os.path.join(workspace_path, file_path)), workspace_path)
    full_path = os.path.abspath(os.path.join(workspace_path, rel_path))
    
    if not is_safe_subpath(workspace_path, full_path):
        raise SecurityError("Path traversal attempt during file lock.")
        
    state = load_orchestration_state(workspace_path)
    locks = state.setdefault("locks", {})
    
    current_lock = locks.get(rel_path)

    # Someone else holds it: report failure without changing anything (the caller decides whether to wait or pick other work).
    if current_lock and current_lock != agent_id:
        return False  # Locked by another agent
        
    locks[rel_path] = agent_id
    save_orchestration_state(workspace_path, state)
    return True

def unlock_file(workspace_path: str, agent_id: str, file_path: str) -> None:
    """Release the lock on a file, but only if `agent_id` is the holder; other agents' locks and unlocked files are left alone (no error either way)."""
    rel_path = os.path.relpath(os.path.abspath(os.path.join(workspace_path, file_path)), workspace_path)
    full_path = os.path.abspath(os.path.join(workspace_path, rel_path))
    
    if not is_safe_subpath(workspace_path, full_path):
        raise SecurityError("Path traversal attempt during file unlock.")
        
    state = load_orchestration_state(workspace_path)
    locks = state.setdefault("locks", {})
    
    if locks.get(rel_path) == agent_id:
        del locks[rel_path]
        save_orchestration_state(workspace_path, state)

def check_dependency(workspace_path: str, dependency_name: str) -> bool:
    """Return True only if the named feature exists and its status is COMPLETED (an unknown feature counts as not done)."""
    state = load_orchestration_state(workspace_path)
    features = state.setdefault("features", {})
    feature_info = features.get(dependency_name, {})
    return feature_info.get("status") == "COMPLETED"

# Record a feature's status. The whole entry is replaced each time (a BLOCKED status keeps `reason`, any other status clears it). status must be one of
# PENDING, IN_PROGRESS, COMPLETED, BLOCKED, otherwise SecurityError is raised. Anyone can overwrite any feature, there is no ownership check.
def update_feature_status(
    workspace_path: str, 
    feature_name: str, 
    status: str, 
    agent_id: str, 
    reason: Optional[str] = None
) -> None:
    if status not in {"PENDING", "IN_PROGRESS", "COMPLETED", "BLOCKED"}:
        raise SecurityError(f"Invalid feature status: {status}")
        
    state = load_orchestration_state(workspace_path)
    features = state.setdefault("features", {})
    
    features[feature_name] = {
        "status": status,
        "assigned_agent": agent_id,
        "reason": reason if status == "BLOCKED" else None
    }
    
    save_orchestration_state(workspace_path, state)
