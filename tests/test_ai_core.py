import os
import pytest
import json
from app.ai.core import validate_ai_proposal, submit_ai_proposal
from app.security import SecurityError

if os.environ.get("AI_VALIDATION_ACTIVE"):
    pytest.skip("Skip recursive AI core tests inside validation subprocess", allow_module_level=True)

def test_validate_ai_proposal_success():
    # Setup workspace
    app_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    project_py_path = os.path.join(app_root, "app", "project.py")
    
    with open(project_py_path, "r", encoding="utf-8") as f:
        original_content = f.read()
        
    proposed_content = original_content + "\n# AI Core safe optimization test\n"
    
    # Run validation on real path
    res = validate_ai_proposal(app_root, "app/project.py", proposed_content)
    assert res["success"] is True
    assert res["test_passed"] is True
    assert res["audit_passed"] is True

def test_validate_ai_proposal_audit_failure():
    app_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    project_py_path = os.path.join(app_root, "app", "project.py")
    
    with open(project_py_path, "r", encoding="utf-8") as f:
        original_content = f.read()
        
    proposed_content = original_content + "\n# AI Core test\neval('1+1')\n"
    
    res = validate_ai_proposal(app_root, "app/project.py", proposed_content)
    assert res["success"] is False
    assert res["audit_passed"] is False

def test_submit_ai_proposal_success(tmp_path):
    app_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    project_py_path = os.path.join(app_root, "app", "project.py")
    
    with open(project_py_path, "r", encoding="utf-8") as f:
        original_content = f.read()
        
    proposed_content = original_content + "\n# AI Core submit test\n"
    
    # We will submit the proposal on app_root so validation passes,
    # which writes to app_root/cache/ai_proposal.json.
    # We will clean it up afterwards.
    proposal_dir = os.path.join(app_root, "cache")
    proposal_file = os.path.join(proposal_dir, "ai_proposal.json")
    
    # Ensure cache directory exists
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
    finally:
        # Clean up
        if os.path.exists(proposal_file):
            os.remove(proposal_file)

