import subprocess
import os
import json
import shutil
from typing import Tuple, Dict, Any
from app.security import SecurityError
from app.storage.workspace import is_safe_subpath, get_workspace_dir

def validate_ai_proposal(workspace_path: str, source_rel_path: str, proposed_code: str) -> Dict[str, Any]:
    # Ensure source file is within the workspace
    source_full = os.path.abspath(os.path.join(workspace_path, source_rel_path))
    if not is_safe_subpath(workspace_path, source_full):
        raise SecurityError("Source file path traversal detected.")
        
    if not os.path.isfile(source_full):
        raise SecurityError("Target source file does not exist.")

    # Create backup
    backup_path = source_full + ".bak"
    shutil.copy2(source_full, backup_path)
    
    test_passed = False
    audit_passed = False
    test_output = ""
    audit_output = ""
    
    try:
        # Write proposed code to the actual file temporarily
        with open(source_full, "w", encoding="utf-8") as f:
            f.write(proposed_code)
            
        # Determine the root of the project (parent of app/)
        # We need to run pytest and security_audit from this root directory
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        
        # Determine pytest executable path (prefer .venv/bin/pytest or fallback to pytest)
        pytest_cmd = os.path.join(project_root, ".venv", "bin", "pytest")
        if not os.path.exists(pytest_cmd):
            pytest_cmd = "pytest"
            
        # Determine python executable path for audit script
        python_cmd = os.path.join(project_root, ".venv", "bin", "python")
        if not os.path.exists(python_cmd):
            python_cmd = "python"
            
        # 1. Run tests (excluding test_ai_core.py to prevent infinite recursion)
        env = os.environ.copy()
        env["AI_VALIDATION_ACTIVE"] = "1"
        test_res = subprocess.run(
            [pytest_cmd, "tests/", "--ignore=tests/test_ai_core.py", "-q"],
            cwd=project_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True
        )
        test_passed = (test_res.returncode == 0)
        test_output = test_res.stdout + "\n" + test_res.stderr
        
        # 2. Run security audit
        audit_res = subprocess.run(
            [python_cmd, "scripts/security_audit.py"],
            cwd=project_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        audit_passed = (audit_res.returncode == 0)
        audit_output = audit_res.stdout + "\n" + audit_res.stderr
        
    finally:
        # Restore backup
        if os.path.exists(backup_path):
            shutil.copy2(backup_path, source_full)
            os.remove(backup_path)
            
    success = test_passed and audit_passed
    return {
        "success": success,
        "test_passed": test_passed,
        "audit_passed": audit_passed,
        "test_output": test_output.strip(),
        "audit_output": audit_output.strip()
    }

def submit_ai_proposal(workspace_path: str, source_rel_path: str, proposed_code: str, summary: str) -> Dict[str, Any]:
    validation = validate_ai_proposal(workspace_path, source_rel_path, proposed_code)
    
    if not validation["success"]:
        return {
            "submitted": False,
            "errors": {
                "test_passed": validation["test_passed"],
                "audit_passed": validation["audit_passed"],
                "test_output": validation["test_output"],
                "audit_output": validation["audit_output"]
            }
        }
        
    cache_dir = get_workspace_dir(workspace_path, "cache")
    proposal_path = os.path.join(cache_dir, "ai_proposal.json")
    
    proposal_data = {
        "source_file": source_rel_path,
        "proposed_code": proposed_code,
        "summary": summary,
        "validation_status": "passed"
    }
    
    with open(proposal_path, "w", encoding="utf-8") as f:
        json.dump(proposal_data, f, indent=2)
        
    return {
        "submitted": True,
        "path": proposal_path
    }
