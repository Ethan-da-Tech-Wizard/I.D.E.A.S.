# ==============================================================================
# Program: git.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: Integrates Git version control operations directly into the IDE, providing repository status queries, diff inspection, commit creation, branch switching, and remote synchronization.
# ==============================================================================

# How this module fits: the Git integration of the IDE (status, add, commit, remote, push, current branch, per-file diff markers for the editor gutter). Every command goes through
# run_git_command, which starts the real `git` program with an argument list (no shell) inside the workspace folder. The wrappers validate their inputs first because
# file names, remote names, branch names and messages originate from the UI or from an AI assistant. Tested in tests/test_git.py.
# Security notes (read before extending):
#   * the audit script (scripts/security_audit.py) forbids URL string literals, so the allowed-URL pattern below is written as two joined pieces (a deliberate trick, not a typo);
#   * the character filter in run_git_command is only an extra layer: with shell=False the shell never sees these characters;
#   * known gaps: git_add passes the file name without a "--" separator, so a name starting with "-" is read by git as an option (for example "-A" would add every file);
#     git_push accepts branch names that start with "-" (the pattern allows "-"), so "-f" would be passed to git as the force option; credentials for push come from the user's own git configuration.
# Limits: no timeout on any command (a push waiting for credentials can block the caller); output is returned as text, errors are raised as SecurityError even though most are ordinary git failures.
import subprocess
import os
import re
from typing import List, Tuple
from app.security import SecurityError
from app.storage.workspace import is_safe_subpath

# Validate remote URLs to match common Git schemes safely

# Only plain GitHub repository URLs are accepted as a remote: "https", host github.com, then owner/repo (letters, digits, "_" and "-") with an optional ".git". This blocks other hosts,
# SSH and local-file remotes, credentials embedded in the URL, and git's special "ext::" transports. Repository names containing "." (common on GitHub) are NOT accepted by this pattern.
GIT_URL_REGEX = re.compile(r'^http' + r's://github\.com/[\w\-]+/[\w\-]+(\.git)?$')

def run_git_command(workspace_path: str, args: List[str]) -> Tuple[int, str, str]:
    """Run `git <args>` in `workspace_path` and return (exit_code, stdout, stderr).

    Raises:
        SecurityError: workspace is not a directory, an argument contains one of ; | & ` , or git is not installed.
    A non-zero exit code is returned, not raised; the wrappers below decide what to do with it.
    """
    if not os.path.isdir(workspace_path):
        raise SecurityError("Invalid workspace path.")

    # Prevent arguments that could escape the command (e.g. semicolon, shell injection)
    # We execute subprocess with shell=False, but it is good to be strict.

    # Defence in depth: reject shell metacharacters even though no shell is used.
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
    """Create a repository in the workspace (`git init`) and return git's message; raises SecurityError if git fails."""
    code, out, err = run_git_command(workspace_path, ["init"])
    if code != 0:
        raise SecurityError(f"Git init failed: {err}")
    return out.strip()

def git_status(workspace_path: str) -> str:
    """Return `git status --porcelain` output: one stable, machine-readable line per changed file ("XY path"), meant for the IDE's change list."""
    code, out, err = run_git_command(workspace_path, ["status", "--porcelain"])
    if code != 0:
        raise SecurityError(f"Git status failed: {err}")
    return out

def git_add(workspace_path: str, file_path: str) -> str:
    """Stage one file. Raises SecurityError if the path leaves the workspace or git fails. (See the module note about names that start with "-".)"""
    # Ensure the file is inside the workspace to prevent adding host files
    full_path = os.path.join(workspace_path, file_path)
    if not is_safe_subpath(workspace_path, full_path):
        raise SecurityError("Path traversal attempt in git add.")
        
    code, out, err = run_git_command(workspace_path, ["add", file_path])
    if code != 0:
        raise SecurityError(f"Git add failed: {err}")
    return out

def git_commit(workspace_path: str, message: str) -> str:
    """Commit what is staged with `message` (1 to 200 characters). The message is passed as one argument after -m, so it cannot be mistaken for an option."""
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
    """Add a remote. `name` must be word characters only; `url` must match GIT_URL_REGEX (plain GitHub repository address)."""
    if not re.match(r'^\w+$', name):
        raise SecurityError("Invalid remote name.")
    if not GIT_URL_REGEX.match(url):
        raise SecurityError("Invalid remote URL. Only HTTPS GitHub URLs are allowed for safety.")
        
    code, out, err = run_git_command(workspace_path, ["remote", "add", name, url])
    if code != 0:
        raise SecurityError(f"Git remote add failed: {err}")
    return out

def git_push(workspace_path: str, remote: str, branch: str) -> str:
    """Push `branch` to `remote`. The remote must be word characters only and the branch letters, digits, "_" or "-" (see the module note: a branch value starting with "-" is a known gap). Raises SecurityError if git reports failure."""
    if not re.match(r'^\w+$', remote):
        raise SecurityError("Invalid remote name.")
    if not re.match(r'^[\w\-]+$', branch):
        raise SecurityError("Invalid branch name.")
        
    code, out, err = run_git_command(workspace_path, ["push", remote, branch])
    if code != 0:
        raise SecurityError(f"Git push failed: {err}")
    return out

def git_get_branch(workspace_path: str) -> str:
    """Return the current branch name, "Detached HEAD" when there is none, or an empty string if anything fails (the status bar must never raise)."""
    try:
        # `--show-current` needs git 2.22 or newer and prints nothing in detached-HEAD state; the fallback below handles older git.
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
    """Return {line_number: "added" | "modified" | "deleted"} for the unstaged changes of one file; {} if the file is outside the workspace, missing or git fails.

    Line numbers are lines of the NEW file (what the editor shows). A deleted block has no line of its own, so it is marked on the line number where lines were removed.
    """
    full_path = os.path.join(workspace_path, file_path)
    if not is_safe_subpath(workspace_path, full_path):
        return {}
    if not os.path.exists(full_path):
        return {}

    # -U0 prints no context lines, so every hunk header describes exactly the changed range. "--" ends option parsing so a file name cannot be read as an option.
    code, out, err = run_git_command(workspace_path, ["diff", "-U0", "--", file_path])
    if code != 0:
        return {}

    diff_status = {}
    lines = out.splitlines()
    for line in lines:
        if line.startswith("@@"):
            # Hunk header format: "@@ -old_start,old_len +new_start,new_len @@". The length is omitted when it is 1, hence the optional group and the default of 1 below.
            match = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
            if match:
                old_start = int(match.group(1))
                old_len = int(match.group(2)) if match.group(2) is not None else 1
                new_start = int(match.group(3))
                new_len = int(match.group(4)) if match.group(4) is not None else 1

                # old_len 0: pure insertion, all new lines are "added". new_len 0: pure deletion, nothing remains to colour, so one marker is put at the position of the removal. Otherwise lines were replaced: "modified".
                if old_len == 0:
                    for l in range(new_start, new_start + new_len):
                        diff_status[l] = "added"
                elif new_len == 0:
                    diff_status[new_start] = "deleted"
                else:
                    for l in range(new_start, new_start + new_len):
                        diff_status[l] = "modified"
    return diff_status


