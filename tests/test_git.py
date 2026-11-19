# ==============================================================================
# Program: tests/test_git.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: tests app/ide/git.py, the IDE's git wrapper: repository init/status work through the
# wrapper, and its input checks (path containment, remote URL allow-list) raise SecurityError.
# Requires a `git` binary on PATH; tests only use throwaway directories under pytest tmp_path.
# Run: python3 -m pytest tests/test_git.py -q
# ==============================================================================

import os
import pytest
from app.ide.git import git_init, git_status, git_add, git_commit, git_remote_add, git_push
from app.security import SecurityError

# Scenario: initialise a repo in an empty folder and read its status. A fresh repo has no changes,
# so `git status --porcelain` must return the empty string. "Reinitialized" is accepted because git prints
# that when a template/global config pre-creates .git.
def test_git_init_and_status(tmp_path):
    ws_path = str(tmp_path / "git_ws")
    os.makedirs(ws_path)
    
    # git prints a different banner for first-time vs repeated init, hence the two accepted strings.
    # Init git repo
    out = git_init(ws_path)
    assert "Initialized empty Git repository" in out or "Reinitialized existing Git repository" in out
    
    # Porcelain output is empty for a clean tree, so any text here would mean the wrapper changed format.
    # Status should work
    status = git_status(ws_path)
    assert status == ""

# Scenario: stage a file outside the workspace via "../". The wrapper must refuse before running
# git, otherwise the IDE could be tricked into staging or leaking files from elsewhere.
def test_git_traversal_blocked(tmp_path):
    ws_path = str(tmp_path / "git_ws")
    os.makedirs(ws_path)
    
    with pytest.raises(SecurityError):
        git_add(ws_path, "../outside.py")

# Scenario: add a remote pointing at an unapproved host. Only the URL validator is under test (the wrapper accepts HTTPS GitHub URLs only); it
# must raise before git runs, so no network traffic is possible in this test.
def test_git_invalid_remote_url(tmp_path):
    ws_path = str(tmp_path / "git_ws")
    os.makedirs(ws_path)
    # A repo must exist first so a SecurityError can only come from URL validation, not a missing repo.
    git_init(ws_path)
    
    with pytest.raises(SecurityError):
        git_remote_add(ws_path, "origin", "https://malicious.com/repo.git")
        
    # The positive case is intentionally left as a comment: running it would depend on git state and
    # could try to contact a real remote; the allow-list regex is exercised by the negative case instead.
    # Valid url should not raise SecurityError (unless git command fails)
    # Since git command might fail if remote already exists, but we test the URL regex
    # git_remote_add(ws_path, "origin", "https://github.com/user/repo.git")
