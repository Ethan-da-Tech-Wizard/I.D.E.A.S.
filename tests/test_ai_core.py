# ==============================================================================
# Program: tests/test_ai_core.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: tests app/ai/core.py, the gate that accepts an AI-proposed file replacement only if the
# whole test suite AND the security audit still pass with the new code in place. Covers the pass
# path, the audit-failure path (proposal containing eval) and the proposal-file write on submit.
# HAZARD: these tests use the REAL repository (app/project.py) as the workspace. validate_ai_proposal
# temporarily overwrites that file (backup in project.py.bak, restored in a finally block) and runs
# the full suite in a child process. Do not run them while someone else is editing app/project.py,
# and if the run is killed, restore app/project.py from the .bak file by hand. They are slow
# because they run pytest recursively.
# Run: python3 -m pytest tests/test_ai_core.py -q
# ==============================================================================

import os
import pytest
import json
from app.ai.core import validate_ai_proposal, submit_ai_proposal
from app.security import SecurityError

# Recursion guard. validate_ai_proposal sets AI_VALIDATION_ACTIVE=1 for its nested pytest run; without
# this skip, the nested run would execute these tests again, which would start another nested run,
# and so on forever. (core.py also excludes this file by path; the skip is the second safety net.)
if os.environ.get("AI_VALIDATION_ACTIVE"):
    pytest.skip("Skip recursive AI core tests inside validation subprocess", allow_module_level=True)

# Scenario: append a harmless comment to a real source file. Expectation: both gates pass, so a
# benign change is not rejected (guards against the gate becoming too strict or broken).
def test_validate_ai_proposal_success():
    """A comment-only change to app/project.py passes both tests and audit."""
    # Setup workspace
    # The repository root is the "workspace" because the validator runs the repository's own tests.
    app_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    project_py_path = os.path.join(app_root, "app", "project.py")
    
    with open(project_py_path, "r", encoding="utf-8") as f:
        original_content = f.read()
        
    # A comment cannot change behaviour, so any failure here would point at the gate, not the code.
    proposed_content = original_content + "\n# AI Core safe optimization test\n"
    
    # validate_ai_proposal restores the original file afterwards; the repo is unchanged when it returns.
    # Run validation on real path
    res = validate_ai_proposal(app_root, "app/project.py", proposed_content)
    assert res["success"] is True
    assert res["test_passed"] is True
    assert res["audit_passed"] is True

# Scenario: the proposal adds a call to eval, which scripts/security_audit.py blocks. The overall
# result must be a failure with audit_passed False: this is the central safety property, an AI
# cannot sneak in dynamic code execution even if the tests still pass.
def test_validate_ai_proposal_audit_failure():
    """A proposal containing eval() is rejected by the security audit."""
    app_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    project_py_path = os.path.join(app_root, "app", "project.py")
    
    with open(project_py_path, "r", encoding="utf-8") as f:
        original_content = f.read()
        
    # eval is on the audit deny-list; the code is never executed by the test, only scanned.
    proposed_content = original_content + "\n# AI Core test\neval('1+1')\n"
    
    res = validate_ai_proposal(app_root, "app/project.py", proposed_content)
    assert res["success"] is False
    assert res["audit_passed"] is False

# Scenario: a valid proposal is submitted. It must be validated, then written to
# <workspace>/cache/ai_proposal.json with the source name, the code and a "passed" status, so a
# human can review it later. Nothing is applied to the real file.
def test_submit_ai_proposal_success(tmp_path):
    """Submitting a passing proposal writes cache/ai_proposal.json describing it."""
    app_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    project_py_path = os.path.join(app_root, "app", "project.py")
    
    with open(project_py_path, "r", encoding="utf-8") as f:
        original_content = f.read()
        
    proposed_content = original_content + "\n# AI Core submit test\n"
    
    # The repo root doubles as the workspace, so the proposal lands in the repository's own cache/ folder.
    # (tmp_path is unused; the file is cleaned up in the finally block instead.)
    # We will submit the proposal on app_root so validation passes,
    # which writes to app_root/cache/ai_proposal.json.
    # We will clean it up afterwards.
    proposal_dir = os.path.join(app_root, "cache")
    proposal_file = os.path.join(proposal_dir, "ai_proposal.json")
    
    # Ensure cache directory exists
    # cache/ may not exist on a fresh checkout; the writer expects it.
    os.makedirs(proposal_dir, exist_ok=True)
    
    try:
        res = submit_ai_proposal(app_root, "app/project.py", proposed_content, "Optimized project.py with a safe comment")
        assert res["submitted"] is True
        assert os.path.exists(res["path"])
        assert res["path"] == proposal_file
        
        with open(res["path"], "r", encoding="utf-8") as f:
            proposal = json.load(f)
            assert proposal["source_file"] == "app/project.py"
            assert proposal["proposed_code"] == proposed_content
            assert proposal["validation_status"] == "passed"
    # Always remove the proposal file, even when an assertion fails, so reruns start clean and the
    # repo is not left with a stray artifact.
    finally:
        # Clean up
        if os.path.exists(proposal_file):
            os.remove(proposal_file)

