# ==============================================================================
# Program: tests/test_orchestration.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: tests app/services/orchestration.py, the shared-state layer that lets several agents
# (developers or automation) coordinate edits: file locks plus a feature-status board stored in
# the workspace cache/ folder. Covers lock ownership, status transitions, and path safety.
# Run: python3 -m pytest tests/test_orchestration.py -q
# ==============================================================================

import os
import pytest
from app.services.orchestration import (
    lock_file, unlock_file, check_dependency, update_feature_status, load_orchestration_state
)
from app.security import SecurityError

# Scenario: two agents compete for one file. Pins the lock rules: exclusive, re-entrant for the
# owner, and releasable only through unlock by the owner, after which others may take it.
def test_file_locking(tmp_path):
    ws_path = str(tmp_path / "lock_ws")
    # Lock and status state are persisted in the workspace, and cache/ is part of the standard layout
    # (init_workspace creates it), so the skeleton is created by hand here.
    os.makedirs(os.path.join(ws_path, "cache"))
    
    # A first lock on a free file succeeds.
    # Agent 1 locks a file
    assert lock_file(ws_path, "agent_1", "app/application.py") is True
    
    # Contention: a different agent is refused (returns False rather than raising).
    # Agent 2 attempts to lock it and gets blocked
    assert lock_file(ws_path, "agent_2", "app/application.py") is False
    
    # Re-entrancy: the same owner re-locking must not deadlock itself.
    # Agent 1 locks it again (re-entrant is allowed)
    assert lock_file(ws_path, "agent_1", "app/application.py") is True
    
    # Agent 1 unlocks it
    unlock_file(ws_path, "agent_1", "app/application.py")
    
    # Proves unlock really released the file, not just flagged it.
    # Agent 2 can now lock it
    assert lock_file(ws_path, "agent_2", "app/application.py") is True

# Scenario: an unknown feature moves to IN_PROGRESS -> COMPLETED. Only COMPLETED satisfies
# check_dependency, so downstream work cannot start on a half-built dependency. A BLOCKED
# feature also stores the reason for humans to read.
def test_dependencies_and_features(tmp_path):
    ws_path = str(tmp_path / "lock_ws")
    os.makedirs(os.path.join(ws_path, "cache"))
    
    # An unknown feature must count as unmet, not as satisfied by default.
    # Initial status is not completed
    assert check_dependency(ws_path, "compiler_backend") is False
    
    # Update status to IN_PROGRESS
    update_feature_status(ws_path, "compiler_backend", "IN_PROGRESS", "agent_3")
    assert check_dependency(ws_path, "compiler_backend") is False
    
    # Update status to COMPLETED
    update_feature_status(ws_path, "compiler_backend", "COMPLETED", "agent_3")
    assert check_dependency(ws_path, "compiler_backend") is True
    
    # The optional reason is persisted; load_orchestration_state re-reads the saved state from disk, so this also checks the save/load round trip.
    # Block a feature
    update_feature_status(
        ws_path, "ecs_viewport", "BLOCKED", "agent_1", reason="Waiting for compiler_backend"
    )
    state = load_orchestration_state(ws_path)
    assert state["features"]["ecs_viewport"]["status"] == "BLOCKED"
    assert state["features"]["ecs_viewport"]["reason"] == "Waiting for compiler_backend"

# Scenario: lock a path outside the workspace. Lock names become file paths, so "../" must raise
# SecurityError instead of creating lock files elsewhere.
def test_traversal_protection(tmp_path):
    ws_path = str(tmp_path / "lock_ws")
    os.makedirs(os.path.join(ws_path, "cache"))
    
    with pytest.raises(SecurityError):
        lock_file(ws_path, "agent_1", "../outside.py")
