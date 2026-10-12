import os
import sys
import pytest
import shutil
import time
from app.security import SecurityError
from app.services.emulators import (
    EmulatorConfig, EmulatorProcess, MemoryHackAPI, SaveStateManager, ACTIVE_EMULATOR_PIDS
)

def test_config_and_path_validation(tmp_path):
    ws_path = str(tmp_path / "workspace")
    outside_path = str(tmp_path / "outside")
    os.makedirs(ws_path, exist_ok=True)
    os.makedirs(outside_path, exist_ok=True)

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

    config = EmulatorConfig("mGBA", exec_path, rom_path, save_dir)
    proc = EmulatorProcess(ws_path)

    with pytest.raises(SecurityError, match="escapes workspace"):
        proc.start(config)

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

    # 3. Save dir outside workspace
    bad_save_dir = os.path.join(outside_path, "saves")
    config = EmulatorConfig("mGBA", safe_exec, rom_path, bad_save_dir)
    with pytest.raises(SecurityError, match="escapes workspace"):
        proc.start(config)

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

def test_memory_hack_api():
    if sys.platform not in {"linux", "win32", "darwin"}:
        api = MemoryHackAPI(99999)
        with pytest.raises(NotImplementedError):
            api.read_memory(0x0, 4)
        with pytest.raises(NotImplementedError):
            api.write_memory(0x0, b"\x00")
        return

    # On supported platforms, test reading/writing memory of a mock process we started
    import subprocess
    if sys.platform == "win32":
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        proc = subprocess.Popen(["sleep", "10"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    pid = proc.pid
    
    # Unauthorized PID check
    api = MemoryHackAPI(pid)
    with pytest.raises(SecurityError, match="not a registered emulator process"):
        api.read_memory(0x0, 4)

    # Register the PID so it's authorized
    ACTIVE_EMULATOR_PIDS.add(pid)
    
    # Try to read/write memory
    # Virtual address 0x0 is normally not readable/writable
    # We expect OSError or permission error, but not SecurityError
    try:
        api.read_memory(0x0, 4)
    except OSError:
        pass  # Expected on invalid address
    except SecurityError:
        pytest.fail("SecurityError raised on authorized PID")

    # Clean up
    ACTIVE_EMULATOR_PIDS.remove(pid)
    proc.terminate()
    proc.wait()
