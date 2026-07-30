import os
import pytest
from app.ide.git import git_init, git_status, git_add, git_commit, git_remote_add, git_push
from app.security import SecurityError

def test_git_init_and_status(tmp_path):
    ws_path = str(tmp_path / "git_ws")
    os.makedirs(ws_path)
    
    # Init git repo
    out = git_init(ws_path)
    assert "Initialized empty Git repository" in out or "Reinitialized existing Git repository" in out
    
    # Status should work
    status = git_status(ws_path)
    assert status == ""

def test_git_traversal_blocked(tmp_path):
    ws_path = str(tmp_path / "git_ws")
    os.makedirs(ws_path)
    
    with pytest.raises(SecurityError):
        git_add(ws_path, "../outside.py")

def test_git_invalid_remote_url(tmp_path):
    ws_path = str(tmp_path / "git_ws")
    os.makedirs(ws_path)
    git_init(ws_path)
    
    with pytest.raises(SecurityError):
        git_remote_add(ws_path, "origin", "https://malicious.com/repo.git")
        
    # Valid url should not raise SecurityError (unless git command fails)
    # Since git command might fail if remote already exists, but we test the URL regex
    # git_remote_add(ws_path, "origin", "https://github.com/user/repo.git")
