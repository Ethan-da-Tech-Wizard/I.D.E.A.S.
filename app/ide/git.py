import subprocess
import os
import re
from typing import List, Tuple
from app.security import SecurityError
from app.storage.workspace import is_safe_subpath

# Validate remote URLs to match common Git schemes safely
GIT_URL_REGEX = re.compile(r'^http' + r's://github\.com/[\w\-]+/[\w\-]+(\.git)?$')

def run_git_command(workspace_path: str, args: List[str]) -> Tuple[int, str, str]:
    if not os.path.isdir(workspace_path):
        raise SecurityError("Invalid workspace path.")

    # Prevent arguments that could escape the command (e.g. semicolon, shell injection)
    # We execute subprocess with shell=False, but it is good to be strict.
    for arg in args:
        if ';' in arg or '|' in arg or '&' in arg or '`' in arg:
            raise SecurityError("Dangerous characters detected in Git argument.")

    # Ensure git command is executed within workspace directory
    try:
        res = subprocess.run(
            ["git"] + args,
            cwd=workspace_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False
        )
        return res.returncode, res.stdout, res.stderr
    except FileNotFoundError:
        raise SecurityError("Git executable not found on the host system.")

def git_init(workspace_path: str) -> str:
    code, out, err = run_git_command(workspace_path, ["init"])
    if code != 0:
        raise SecurityError(f"Git init failed: {err}")
    return out.strip()

def git_status(workspace_path: str) -> str:
    code, out, err = run_git_command(workspace_path, ["status", "--porcelain"])
    if code != 0:
        raise SecurityError(f"Git status failed: {err}")
    return out

def git_add(workspace_path: str, file_path: str) -> str:
    # Ensure the file is inside the workspace to prevent adding host files
    full_path = os.path.join(workspace_path, file_path)
    if not is_safe_subpath(workspace_path, full_path):
        raise SecurityError("Path traversal attempt in git add.")
        
    code, out, err = run_git_command(workspace_path, ["add", file_path])
    if code != 0:
        raise SecurityError(f"Git add failed: {err}")
    return out

def git_commit(workspace_path: str, message: str) -> str:
    if not message:
        raise SecurityError("Commit message cannot be empty.")
    # Safe validation of message (simple length constraint)
    if len(message) > 200:
        raise SecurityError("Commit message is too long.")
        
    code, out, err = run_git_command(workspace_path, ["commit", "-m", message])
    if code != 0:
        raise SecurityError(f"Git commit failed: {err}")
    return out

def git_remote_add(workspace_path: str, name: str, url: str) -> str:
    if not re.match(r'^\w+$', name):
        raise SecurityError("Invalid remote name.")
    if not GIT_URL_REGEX.match(url):
        raise SecurityError("Invalid remote URL. Only HTTPS GitHub URLs are allowed for safety.")
        
    code, out, err = run_git_command(workspace_path, ["remote", "add", name, url])
    if code != 0:
        raise SecurityError(f"Git remote add failed: {err}")
    return out

def git_push(workspace_path: str, remote: str, branch: str) -> str:
    if not re.match(r'^\w+$', remote):
        raise SecurityError("Invalid remote name.")
    if not re.match(r'^[\w\-]+$', branch):
        raise SecurityError("Invalid branch name.")
        
    code, out, err = run_git_command(workspace_path, ["push", remote, branch])
    if code != 0:
        raise SecurityError(f"Git push failed: {err}")
    return out

def git_get_branch(workspace_path: str) -> str:
    try:
        code, out, err = run_git_command(workspace_path, ["branch", "--show-current"])
        if code == 0 and out.strip():
            return out.strip()
        # Fallback for older git versions
        code, out, err = run_git_command(workspace_path, ["symbolic-ref", "--short", "HEAD"])
        if code == 0 and out.strip():
            return out.strip()
        return "Detached HEAD"
    except Exception:
        return ""

def git_get_file_diff(workspace_path: str, file_path: str) -> dict:
    full_path = os.path.join(workspace_path, file_path)
    if not is_safe_subpath(workspace_path, full_path):
        return {}
    if not os.path.exists(full_path):
        return {}

    code, out, err = run_git_command(workspace_path, ["diff", "-U0", "--", file_path])
    if code != 0:
        return {}

    diff_status = {}
    lines = out.splitlines()
    for line in lines:
        if line.startswith("@@"):
            match = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
            if match:
                old_start = int(match.group(1))
                old_len = int(match.group(2)) if match.group(2) is not None else 1
                new_start = int(match.group(3))
                new_len = int(match.group(4)) if match.group(4) is not None else 1

                if old_len == 0:
                    for l in range(new_start, new_start + new_len):
                        diff_status[l] = "added"
                elif new_len == 0:
                    diff_status[new_start] = "deleted"
                else:
                    for l in range(new_start, new_start + new_len):
                        diff_status[l] = "modified"
    return diff_status


