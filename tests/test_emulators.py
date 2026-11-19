# ==============================================================================
# Program: tests/test_emulators.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: tests app/services/emulators.py, the wrapper that launches an external game emulator and
# manages its save states. The safety rules under test: every path (emulator binary, ROM, save
# folder) must stay inside the workspace; memory access is allowed only for PIDs of emulators this
# app started; save-state slots are plain file copies. No real emulator is run: the "emulator" is a
# two-line shell script or a `sleep` process.
# Run: python3 -m pytest tests/test_emulators.py -q     (POSIX or Windows)
# ==============================================================================

import os
import sys
import pytest
import shutil
import time
from app.security import SecurityError
from app.services.emulators import (
    EmulatorConfig, EmulatorProcess, MemoryHackAPI, SaveStateManager, ACTIVE_EMULATOR_PIDS
)

# Scenario: build three configs, each with exactly one path outside the workspace (binary, ROM,
# save folder). start() must refuse each with "escapes workspace" before launching anything, so a
# malicious project file cannot make the app execute or overwrite files elsewhere. The other two
# paths in each case are valid, which isolates the check being exercised.
def test_config_and_path_validation(tmp_path):
    ws_path = str(tmp_path / "workspace")
    outside_path = str(tmp_path / "outside")
    os.makedirs(ws_path, exist_ok=True)
    os.makedirs(outside_path, exist_ok=True)

    # The fake emulator is an executable script placed in a sibling "outside" folder.
    # 1. Executable outside workspace
    exec_path = os.path.join(outside_path, "emulator")
    with open(exec_path, "w") as f:
        f.write("#!/bin/sh\nexit 0\n")
    os.chmod(exec_path, 0o755)

    rom_path = os.path.join(ws_path, "game.rom")
    with open(rom_path, "wb") as f:
        f.write(b"\x00\x00")

    save_dir = os.path.join(ws_path, "saves")
    os.makedirs(save_dir, exist_ok=True)

    # Positional fields: display name, binary, ROM, save directory.
    config = EmulatorConfig("mGBA", exec_path, rom_path, save_dir)
    proc = EmulatorProcess(ws_path)

    with pytest.raises(SecurityError, match="escapes workspace"):
        proc.start(config)

    # Now the binary is a valid in-workspace copy; only the ROM path is bad.
    # 2. ROM outside workspace
    safe_exec = os.path.join(ws_path, "emulator")
    shutil.copy(exec_path, safe_exec)
    os.chmod(safe_exec, 0o755)

    bad_rom = os.path.join(outside_path, "game.rom")
    with open(bad_rom, "wb") as f:
        f.write(b"\x00\x00")

    config = EmulatorConfig("mGBA", safe_exec, bad_rom, save_dir)
    with pytest.raises(SecurityError, match="escapes workspace"):
        proc.start(config)

    # Binary and ROM are valid again; only the save folder is bad.
    # 3. Save dir outside workspace
    bad_save_dir = os.path.join(outside_path, "saves")
    config = EmulatorConfig("mGBA", safe_exec, rom_path, bad_save_dir)
    with pytest.raises(SecurityError, match="escapes workspace"):
        proc.start(config)

# A binary path inside the workspace that does not exist yields a clear "does not exist" error
# instead of an OS-level FileNotFoundError.
def test_missing_binary(tmp_path):
    ws_path = str(tmp_path / "workspace")
    os.makedirs(ws_path, exist_ok=True)

    safe_exec = os.path.join(ws_path, "emulator_missing")
    rom_path = os.path.join(ws_path, "game.rom")
    with open(rom_path, "wb") as f:
        f.write(b"\x00\x00")

    save_dir = os.path.join(ws_path, "saves")
    os.makedirs(save_dir, exist_ok=True)

    config = EmulatorConfig("mGBA", safe_exec, rom_path, save_dir)
    proc = EmulatorProcess(ws_path)

    with pytest.raises(SecurityError, match="does not exist"):
        proc.start(config)

# Scenario: slot-based save states are copies of the emulator's save file. Saving slot 1 writes
# slot_1.state with identical content; list_states reports [1]; after the live save changes,
# load_state(1) restores the old content (rollback). Constructing the manager with a save folder
# outside the workspace must fail like the emulator paths do.
def test_save_state_manager(tmp_path):
    ws_path = str(tmp_path / "workspace")
    os.makedirs(ws_path, exist_ok=True)

    save_dir = os.path.join(ws_path, "saves")
    os.makedirs(save_dir, exist_ok=True)

    emu_save = os.path.join(ws_path, "current.sav")
    with open(emu_save, "w") as f:
        f.write("save_data_v1")

    mgr = SaveStateManager(ws_path, save_dir, emu_save)

    # Test saving state
    mgr.save_state(1)
    slot_1_path = os.path.join(save_dir, "slot_1.state")
    assert os.path.exists(slot_1_path)
    with open(slot_1_path, "r") as f:
        assert f.read() == "save_data_v1"

    # Test listing states
    assert mgr.list_states() == [1]

    # Overwrite the live save so the restore step has something visible to undo.
    # Modify main save and test load (restore)
    with open(emu_save, "w") as f:
        f.write("save_data_v2")

    mgr.load_state(1)
    with open(emu_save, "r") as f:
        assert f.read() == "save_data_v1"

    # Path traversal block tests (save dir / emu_save path outside workspace)
    outside_path = str(tmp_path / "outside")
    with pytest.raises(SecurityError, match="escapes workspace"):
        SaveStateManager(ws_path, outside_path, emu_save)

# MemoryHackAPI reads/writes another process's memory (used for cheats/inspection), so it is gated
# by ACTIVE_EMULATOR_PIDS. On unsupported platforms it must raise NotImplementedError. On supported
# ones: an unregistered PID is refused with SecurityError; once registered, the call may fail with a
# normal OSError (address 0 is unreadable) but must NOT raise SecurityError.
def test_memory_hack_api():
    # Platform gate: only Linux, Windows and macOS have an implementation.
    if sys.platform not in {"linux", "win32", "darwin"}:
        api = MemoryHackAPI(99999)
        with pytest.raises(NotImplementedError):
            api.read_memory(0x0, 4)
        with pytest.raises(NotImplementedError):
            api.write_memory(0x0, b"\x00")
        return

    # On supported platforms, test reading/writing memory of a mock process we started
    # A harmless child process (sleep) stands in for an emulator so there is a real PID to test with.
    import subprocess
    if sys.platform == "win32":
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        proc = subprocess.Popen(["sleep", "10"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    pid = proc.pid
    
    # The process exists but this app never registered it, so access must be denied.
    # Unauthorized PID check
    api = MemoryHackAPI(pid)
    with pytest.raises(SecurityError, match="not a registered emulator process"):
        api.read_memory(0x0, 4)

    # Registration is what the real EmulatorProcess does after launching its child.
    # Register the PID so it's authorized
    ACTIVE_EMULATOR_PIDS.add(pid)
    
    # Try to read/write memory
    # Virtual address 0x0 is normally not readable/writable
    # We expect OSError or permission error, but not SecurityError
    # The OS (not our policy) is expected to reject address 0 with OSError; permission errors are fine.
    try:
        api.read_memory(0x0, 4)
    except OSError:
        pass  # Expected on invalid address
    except SecurityError:
        pytest.fail("SecurityError raised on authorized PID")

    # Always unregister and stop the child so no stray process or PID entry leaks into other tests.
    # Clean up
    ACTIVE_EMULATOR_PIDS.remove(pid)
    proc.terminate()
    proc.wait()
