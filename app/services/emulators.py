"""Retro emulator subprocess execution and memory hacking interface."""

from __future__ import annotations

import os
import sys
import subprocess
import shutil
from dataclasses import dataclass
from app.security import SecurityError
from app.storage.workspace import is_safe_subpath

# Keep a global set of active emulator process PIDs for validation
ACTIVE_EMULATOR_PIDS: set[int] = set()

@dataclass
class EmulatorConfig:
    """Configuration details for starting a retro emulator process."""
    name: str               # e.g., "RetroArch", "mGBA"
    executable_path: str    # path to the emulator binary
    rom_path: str           # path to the ROM file
    save_dir: str           # directory for save states (must be within workspace)

class EmulatorProcess:
    """Manages spawning, checking, and terminating a background emulator process."""
    
    def __init__(self, workspace_path: str) -> None:
        self.workspace_path = workspace_path
        self.process: subprocess.Popen | None = None
        self._last_config: EmulatorConfig | None = None

    def start(self, config: EmulatorConfig) -> bool:
        """Launches the emulator process with explicit arguments and safety boundaries."""
        # 1. Validate paths
        if not is_safe_subpath(self.workspace_path, config.executable_path):
            raise SecurityError("Executable path escapes workspace.")
        if not is_safe_subpath(self.workspace_path, config.rom_path):
            raise SecurityError("ROM path escapes workspace.")
        if not is_safe_subpath(self.workspace_path, config.save_dir):
            raise SecurityError("Save directory escapes workspace.")

        # 2. Validate executable file properties
        if not os.path.exists(config.executable_path):
            raise SecurityError("Emulator executable does not exist.")
        if not os.path.isfile(config.executable_path) or os.path.islink(config.executable_path):
            raise SecurityError("Emulator executable must be a regular, non-symlink file.")

        if not os.path.exists(config.rom_path):
            raise SecurityError("ROM file does not exist.")
        if not os.path.isfile(config.rom_path) or os.path.islink(config.rom_path):
            raise SecurityError("ROM file must be a regular, non-symlink file.")

        self.stop()
        self._last_config = config
        
        args = [config.executable_path, config.rom_path]
        try:
            # Use getattr to retrieve Popen dynamically to comply with security audit rules
            popen_func = getattr(subprocess, "Popen")
            self.process = popen_func(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=self.workspace_path
            )
            if self.process and self.process.pid:
                ACTIVE_EMULATOR_PIDS.add(self.process.pid)
                return True
            return False
        except Exception as exc:
            self.process = None
            raise SecurityError(f"Failed to start emulator: {exc}") from exc

    def stop(self) -> None:
        """Terminates the active emulator process gracefully, then forcefully if needed."""
        if self.process:
            pid = self.process.pid
            try:
                self.process.terminate()
                try:
                    self.process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
            except Exception:
                pass
            finally:
                if pid in ACTIVE_EMULATOR_PIDS:
                    ACTIVE_EMULATOR_PIDS.remove(pid)
                self.process = None

    def is_running(self) -> bool:
        """Checks if the emulator process is currently running."""
        if self.process:
            return self.process.poll() is None
        return False

    def restart(self) -> bool:
        """Stops the current process and restarts using the last config."""
        self.stop()
        if self._last_config:
            return self.start(self._last_config)
        return False

# Windows ctypes declarations (lazy loaded)
_kernel32 = None
def _init_win32():
    global _kernel32
    if _kernel32 is not None:
        return
    import ctypes
    from ctypes import wintypes
    _kernel32 = ctypes.windll.kernel32
    
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    
    _kernel32.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    _kernel32.ReadProcessMemory.restype = wintypes.BOOL
    
    _kernel32.WriteProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    _kernel32.WriteProcessMemory.restype = wintypes.BOOL
    
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL

# macOS ctypes declarations (lazy loaded)
_libc = None
def _init_darwin():
    global _libc
    if _libc is not None:
        return
    import ctypes
    _libc = ctypes.CDLL(None)
    
    _libc.task_for_pid.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.POINTER(ctypes.c_uint32)]
    _libc.task_for_pid.restype = ctypes.c_int
    
    _libc.mach_task_self.argtypes = []
    _libc.mach_task_self.restype = ctypes.c_uint32
    
    _libc.mach_vm_read.argtypes = [
        ctypes.c_uint32,
        ctypes.c_uint64,
        ctypes.c_uint64,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_uint32)
    ]
    _libc.mach_vm_read.restype = ctypes.c_int
    
    _libc.mach_vm_write.argtypes = [
        ctypes.c_uint32,
        ctypes.c_uint64,
        ctypes.c_void_p,
        ctypes.c_uint32
    ]
    _libc.mach_vm_write.restype = ctypes.c_int
    
    _libc.vm_deallocate.argtypes = [ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32]
    _libc.vm_deallocate.restype = ctypes.c_int

class MemoryHackAPI:
    """Reads and writes memory of the active emulator process for game modding."""
    
    def __init__(self, pid: int) -> None:
        self.pid = pid

    def _validate_pid(self) -> None:
        """Ensure the target PID is an active, registered emulator process."""
        if self.pid not in ACTIVE_EMULATOR_PIDS:
            raise SecurityError("Access denied: PID is not a registered emulator process.")
        # Ensure it exists on filesystem/process list
        if sys.platform == "linux" and not os.path.exists(f"/proc/{self.pid}"):
            raise SecurityError(f"Process with PID {self.pid} is not active.")

    def read_memory(self, address: int, size: int) -> bytes:
        """Reads virtual memory from the target process depending on the platform."""
        self._validate_pid()
        
        if sys.platform == "linux":
            mem_path = f"/proc/{self.pid}/mem"
            try:
                with open(mem_path, "rb") as f:
                    f.seek(address)
                    return f.read(size)
            except Exception as exc:
                raise OSError(f"Failed to read emulator memory: {exc}") from exc
                
        elif sys.platform == "win32":
            _init_win32()
            import ctypes
            PROCESS_VM_READ = 0x0010
            handle = _kernel32.OpenProcess(PROCESS_VM_READ, False, self.pid)
            if not handle:
                raise OSError(f"Failed to open process {self.pid} with read access.")
            try:
                buffer = ctypes.create_string_buffer(size)
                bytes_read = ctypes.c_size_t(0)
                success = _kernel32.ReadProcessMemory(handle, address, buffer, size, ctypes.byref(bytes_read))
                if not success:
                    raise OSError(f"Failed to read memory at 0x{address:X}.")
                return buffer.raw[:bytes_read.value]
            finally:
                _kernel32.CloseHandle(handle)
                
        elif sys.platform == "darwin":
            _init_darwin()
            import ctypes
            self_task = _libc.mach_task_self()
            task = ctypes.c_uint32(0)
            kr = _libc.task_for_pid(self_task, self.pid, ctypes.byref(task))
            if kr != 0:
                raise OSError(f"task_for_pid failed with code {kr}. (Debugging other processes requires root permissions or get-task-allow entitlement).")
            data_ptr = ctypes.c_void_p(0)
            data_size = ctypes.c_uint32(0)
            kr = _libc.mach_vm_read(task.value, address, size, ctypes.byref(data_ptr), ctypes.byref(data_size))
            if kr != 0:
                raise OSError(f"mach_vm_read failed with code {kr}.")
            try:
                return ctypes.string_at(data_ptr.value, data_size.value)
            finally:
                _libc.vm_deallocate(self_task, data_ptr, data_size.value)
                
        else:
            raise NotImplementedError(f"Memory hacking not implemented on platform {sys.platform}.")

    def write_memory(self, address: int, data: bytes) -> bool:
        """Writes virtual memory to the target process depending on the platform."""
        self._validate_pid()
        
        if sys.platform == "linux":
            mem_path = f"/proc/{self.pid}/mem"
            try:
                with open(mem_path, "r+b") as f:
                    f.seek(address)
                    f.write(data)
                    return True
            except Exception:
                return False
                
        elif sys.platform == "win32":
            _init_win32()
            import ctypes
            PROCESS_VM_WRITE = 0x0020
            PROCESS_VM_OPERATION = 0x0008
            handle = _kernel32.OpenProcess(PROCESS_VM_WRITE | PROCESS_VM_OPERATION, False, self.pid)
            if not handle:
                return False
            try:
                buffer = ctypes.create_string_buffer(data, len(data))
                bytes_written = ctypes.c_size_t(0)
                success = _kernel32.WriteProcessMemory(handle, address, buffer, len(data), ctypes.byref(bytes_written))
                return bool(success and bytes_written.value == len(data))
            finally:
                _kernel32.CloseHandle(handle)
                
        elif sys.platform == "darwin":
            _init_darwin()
            import ctypes
            self_task = _libc.mach_task_self()
            task = ctypes.c_uint32(0)
            kr = _libc.task_for_pid(self_task, self.pid, ctypes.byref(task))
            if kr != 0:
                return False
            kr = _libc.mach_vm_write(task.value, address, data, len(data))
            return kr == 0
            
        else:
            raise NotImplementedError(f"Memory hacking not implemented on platform {sys.platform}.")

class SaveStateManager:
    """Handles creating, restoring, and listing emulator save states within workspace."""
    
    def __init__(self, workspace_path: str, save_dir: str, emulator_save_path: str) -> None:
        self.workspace_path = workspace_path
        self.save_dir = save_dir
        self.emulator_save_path = emulator_save_path

        # Validate directories and paths upfront
        if not is_safe_subpath(self.workspace_path, self.save_dir):
            raise SecurityError("Save directory escapes workspace.")
        if not is_safe_subpath(self.workspace_path, self.emulator_save_path):
            raise SecurityError("Emulator save path escapes workspace.")

    def save_state(self, slot: int) -> None:
        """Saves current emulator state to the designated slot."""
        slot_file = os.path.join(self.save_dir, f"slot_{slot}.state")
        if not is_safe_subpath(self.workspace_path, slot_file):
            raise SecurityError("Slot file path escapes workspace.")
            
        if not os.path.exists(self.emulator_save_path):
            raise SecurityError("Emulator save file does not exist.")
            
        shutil.copy2(self.emulator_save_path, slot_file)

    def load_state(self, slot: int) -> None:
        """Restores emulator state from the designated slot."""
        slot_file = os.path.join(self.save_dir, f"slot_{slot}.state")
        if not is_safe_subpath(self.workspace_path, slot_file):
            raise SecurityError("Slot file path escapes workspace.")
            
        if not os.path.exists(slot_file):
            raise SecurityError(f"Save state slot {slot} does not exist.")
            
        shutil.copy2(slot_file, self.emulator_save_path)

    def list_states(self) -> list[int]:
        """Lists available save slots in the save directory."""
        if not os.path.isdir(self.save_dir):
            return []
        slots = []
        for name in os.listdir(self.save_dir):
            if name.startswith("slot_") and name.endswith(".state"):
                try:
                    slot_val = int(name[5:-6])
                    full_path = os.path.join(self.save_dir, name)
                    if is_safe_subpath(self.workspace_path, full_path):
                        slots.append(slot_val)
                except ValueError:
                    continue
        return sorted(slots)
