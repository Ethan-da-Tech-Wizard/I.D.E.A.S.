import os
import pytest
from app.services.orchestration import (
    lock_file, unlock_file, check_dependency, update_feature_status, load_orchestration_state
)
from app.security import SecurityError

def test_file_locking(tmp_path):
    ws_path = str(tmp_path / "lock_ws")
    os.makedirs(os.path.join(ws_path, "cache"))
    
    # Agent 1 locks a file
    assert lock_file(ws_path, "agent_1", "app/application.py") is True
    
    # Agent 2 attempts to lock it and gets blocked
    assert lock_file(ws_path, "agent_2", "app/application.py") is False
    
    # Agent 1 locks it again (re-entrant is allowed)
    assert lock_file(ws_path, "agent_1", "app/application.py") is True
    
    # Agent 1 unlocks it
    unlock_file(ws_path, "agent_1", "app/application.py")
    
    # Agent 2 can now lock it
    assert lock_file(ws_path, "agent_2", "app/application.py") is True

def test_dependencies_and_features(tmp_path):
    ws_path = str(tmp_path / "lock_ws")
    os.makedirs(os.path.join(ws_path, "cache"))
    
    # Initial status is not completed
    assert check_dependency(ws_path, "compiler_backend") is False
    
    # Update status to IN_PROGRESS
    update_feature_status(ws_path, "compiler_backend", "IN_PROGRESS", "agent_3")
    assert check_dependency(ws_path, "compiler_backend") is False
    
    # Update status to COMPLETED
    update_feature_status(ws_path, "compiler_backend", "COMPLETED", "agent_3")
    assert check_dependency(ws_path, "compiler_backend") is True
    
    # Block a feature
    update_feature_status(
        ws_path, "ecs_viewport", "BLOCKED", "agent_1", reason="Waiting for compiler_backend"
    )
    state = load_orchestration_state(ws_path)
    assert state["features"]["ecs_viewport"]["status"] == "BLOCKED"
    assert state["features"]["ecs_viewport"]["reason"] == "Waiting for compiler_backend"

def test_traversal_protection(tmp_path):
    ws_path = str(tmp_path / "lock_ws")
    os.makedirs(os.path.join(ws_path, "cache"))
    
    with pytest.raises(SecurityError):
        lock_file(ws_path, "agent_1", "../outside.py")
