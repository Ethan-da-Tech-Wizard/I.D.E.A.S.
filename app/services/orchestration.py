import os
import json
from typing import Dict, Any, Optional
from app.security import SecurityError
from app.storage.workspace import get_workspace_dir, is_safe_subpath

DEFAULT_STATE: Dict[str, Any] = {
    "locks": {},
    "features": {}
}

def load_orchestration_state(workspace_path: str) -> Dict[str, Any]:
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
    cache_dir = get_workspace_dir(workspace_path, "cache")
    state_file = os.path.join(cache_dir, "orchestration_state.json")
    
    if not is_safe_subpath(workspace_path, state_file):
        raise SecurityError("Path traversal attempt in orchestration state.")
        
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

def lock_file(workspace_path: str, agent_id: str, file_path: str) -> bool:
    # Normalize path relative to workspace
    rel_path = os.path.relpath(os.path.abspath(os.path.join(workspace_path, file_path)), workspace_path)
    full_path = os.path.abspath(os.path.join(workspace_path, rel_path))
    
    if not is_safe_subpath(workspace_path, full_path):
        raise SecurityError("Path traversal attempt during file lock.")
        
    state = load_orchestration_state(workspace_path)
    locks = state.setdefault("locks", {})
    
    current_lock = locks.get(rel_path)
    if current_lock and current_lock != agent_id:
        return False  # Locked by another agent
        
    locks[rel_path] = agent_id
    save_orchestration_state(workspace_path, state)
    return True

def unlock_file(workspace_path: str, agent_id: str, file_path: str) -> None:
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
    state = load_orchestration_state(workspace_path)
    features = state.setdefault("features", {})
    feature_info = features.get(dependency_name, {})
    return feature_info.get("status") == "COMPLETED"

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
