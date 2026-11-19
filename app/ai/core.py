# ==============================================================================
# Program: core.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: Provides AI assistant integration and local language model query interfaces for intelligent code completion, automated refactoring suggestions, and generative creative workflows.
# ==============================================================================

# How this module fits: a gatekeeper for code changes proposed by an AI assistant. An AI-written replacement for a source file is only accepted if, with the new code
# temporarily in place, the project's whole test suite passes AND the repository's security audit (scripts/security_audit.py) passes. Only a proposal that passes is saved
# to <workspace>/cache/ai_proposal.json for a human to review; nothing is ever applied to the real file automatically.
# Status: no code in app/ calls these functions yet (only tests/test_ai_core.py does); the UI for AI proposals is not wired in.
#
# Mechanics and hazards (read before changing):
#   * validate_ai_proposal really overwrites the source file for the duration of the run (the original is copied to <file>.bak and restored in `finally`). If the
#     process is killed in the middle (power loss, SIGKILL) the proposed code stays in the file and the .bak file is left behind: restore from the .bak by hand.
#   * it runs pytest and the audit as child processes with cwd set to the repository root, so it only works from a source checkout (not a packaged build) and
#     runs the repository's own tests, not a project's.
#   * the pytest run excludes tests/test_ai_core.py and sets AI_VALIDATION_ACTIVE=1, which that test file checks to skip itself, because running it inside the validation would recurse forever.
#   * no timeout is set on either child process, so a hanging test hangs the caller.
import subprocess
import os
import json
import shutil
from typing import Tuple, Dict, Any
from app.security import SecurityError
from app.storage.workspace import is_safe_subpath, get_workspace_dir

def validate_ai_proposal(workspace_path: str, source_rel_path: str, proposed_code: str) -> Dict[str, Any]:
    """Try `proposed_code` in place of one workspace file and report whether tests and the security audit still pass.

    Args:
        workspace_path: workspace folder.
        source_rel_path: file to replace, relative to the workspace.
        proposed_code: full new file contents.

    Returns:
        dict with success (both checks passed), test_passed, audit_passed and the captured output of each (test_output, audit_output).

    Raises:
        SecurityError: the file is outside the workspace or does not exist.
    The original file is always restored afterwards (see the module notes for the one exception).
    """
    # Ensure source file is within the workspace

    # Resolve the target and make sure it is inside the workspace (blocks "../" in the relative path); only existing files may be replaced.
    source_full = os.path.abspath(os.path.join(workspace_path, source_rel_path))
    if not is_safe_subpath(workspace_path, source_full):
        raise SecurityError("Source file path traversal detected.")
        
    if not os.path.isfile(source_full):
        raise SecurityError("Target source file does not exist.")

    # Create backup
    backup_path = source_full + ".bak"

    # Backup before touching anything; copy2 keeps timestamps and permissions so the restore is exact.
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

        # Repository root: this file is app/ai/core.py, so two levels up.
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        
        # Determine pytest executable path (prefer .venv/bin/pytest or fallback to pytest)

        # Prefer the project's virtual environment (the right Python and dependencies); otherwise use whatever "pytest" is on PATH. The ".venv/bin" layout is Linux/macOS (on Windows it would be Scripts).
        pytest_cmd = os.path.join(project_root, ".venv", "bin", "pytest")
        if not os.path.exists(pytest_cmd):
            pytest_cmd = "pytest"
            
        # Determine python executable path for audit script
        python_cmd = os.path.join(project_root, ".venv", "bin", "python")
        if not os.path.exists(python_cmd):
            python_cmd = "python"
            
        # 1. Run tests (excluding test_ai_core.py to prevent infinite recursion)
        env = os.environ.copy()

        # Marker read by tests/test_ai_core.py so that test skips itself in this nested run.
        env["AI_VALIDATION_ACTIVE"] = "1"

        # Run the test suite quietly (-q), capturing both output streams. A non-zero exit code means at least one failure or error.
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

        # Second gate: the static security audit (forbidden calls, imports and strings). It runs even when tests failed so the caller gets both reports at once.
        audit_res = subprocess.run(
            [python_cmd, "scripts/security_audit.py"],
            cwd=project_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        audit_passed = (audit_res.returncode == 0)
        audit_output = audit_res.stdout + "\n" + audit_res.stderr
        
    # Always restore, even if a check raised, so a failed proposal never leaves modified code behind.
    finally:
        # Restore backup
        if os.path.exists(backup_path):
            shutil.copy2(backup_path, source_full)
            os.remove(backup_path)
            
    # A proposal is acceptable only if both gates passed.
    success = test_passed and audit_passed
    return {
        "success": success,
        "test_passed": test_passed,
        "audit_passed": audit_passed,
        "test_output": test_output.strip(),
        "audit_output": audit_output.strip()
    }

def submit_ai_proposal(workspace_path: str, source_rel_path: str, proposed_code: str, summary: str) -> Dict[str, Any]:
    """Validate a proposal and, if it passes, save it for human review.

    Args:
        summary: short explanation of the change, stored with the proposal.

    Returns:
        {"submitted": False, "errors": {...}} with the reports when validation fails; {"submitted": True, "path": <json file>} when saved.
    The proposal file holds the source name, the full proposed code, the summary and validation_status "passed". Saving does not change the source file.
    """
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
        
    # Proposals live in the workspace's cache folder (safe to delete; nothing depends on it) and the previous proposal file is overwritten.
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
