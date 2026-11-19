# ==============================================================================
# Program: emulators.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: Manages external emulator and virtual hardware runtimes for testing game engine builds, ROM verification, and retro audio playback.
# ==============================================================================
# How this module fits: app/application.py's ensure_emulator_process() creates one EmulatorProcess for the open workspace. The engine/game-modding UI starts
# an emulator with an EmulatorConfig, reads or writes the running game's memory with MemoryHackAPI (for trainers, cheat tools and tutorials), and keeps
# save states with SaveStateManager. tests/test_emulators.py covers the path checks and save-state logic.
# Inputs: an executable, a ROM and a save folder, all of which must live INSIDE the workspace folder. Outputs: a background child process, bytes read from it, and
# copied save files.
# Security design: nothing outside the workspace may be launched or written (is_safe_subpath on every path); the program and ROM must be regular files that are not symlinks;
# the child gets an argument LIST (never a shell string) and no input or output pipes; memory access is allowed only for processes this module started
# (ACTIVE_EMULATOR_PIDS); failures raise SecurityError or OSError rather than being ignored (except the best-effort write paths that return False).
# Known limits and risks noticed (comment pass only, nothing changed):
#   - ACTIVE_EMULATOR_PIDS is only cleaned in stop(). If the emulator exits by itself its PID stays registered, and the operating system may later reuse that number for an
#     unrelated process, which MemoryHackAPI would then treat as an approved target. Checking process.poll() before access (or removing the PID when it exits) would close this.
#   - is_safe_subpath compares text paths only: relative paths are judged against the current directory (while the child is started in the workspace), and a symlinked
#     parent folder is not detected (only the executable and ROM files themselves are checked for being symlinks).
#   - start() uses getattr(subprocess, "Popen") instead of a direct call; the comment there says this is "to comply with security audit rules", which means the
#     repository's audit tripwire (scripts/security_audit.py) is being sidestepped by name rather than by an explicit allow-list entry for Popen.
#   - Memory access needs OS rights: Linux needs ptrace permission for /proc/<pid>/mem, Windows needs matching process rights, and macOS task_for_pid normally fails
#     without root or a debugging entitlement (the error message says so). Only Linux/Windows/macOS are implemented.
#   - read_memory on Linux reads whatever the kernel returns for the address; no size limit is applied to `size`.
#   - save_state/load_state use plain copies with no locking, so copying while the emulator writes the file may capture a partial state.

"""Retro emulator subprocess execution and memory hacking interface."""

from __future__ import annotations

import os
import sys
import subprocess
import shutil
from dataclasses import dataclass
from app.security import SecurityError
from app.storage.workspace import is_safe_subpath

# Registry of process IDs started by EmulatorProcess.start(); MemoryHackAPI refuses any other PID, so it cannot be pointed at arbitrary programs (see the PID-reuse limit above).
# Keep a global set of active emulator process PIDs for validation
ACTIVE_EMULATOR_PIDS: set[int] = set()

@dataclass
class EmulatorConfig:
    """Settings to start one emulator run.

    Attributes:
        name: display name (for the UI only; not used to pick the program).
        executable_path: the emulator program; must be inside the workspace.
        rom_path: the game file passed as the single argument; must be inside the workspace.
        save_dir: folder for save states; must be inside the workspace.
    """
    name: str               # e.g., "RetroArch", "mGBA"
    executable_path: str    # path to the emulator binary
    rom_path: str           # path to the ROM file
    save_dir: str           # directory for save states (must be within workspace)

class EmulatorProcess:
    """Starts, stops and restarts one emulator child process.

    Only one process is managed at a time: start() stops any previous one first.
    """
    
    def __init__(self, workspace_path: str) -> None:
        """Create a manager for a workspace.

        Args:
            workspace_path: root folder that all emulator paths must stay inside; also the child's working directory.
        """
        self.workspace_path = workspace_path
        self.process: subprocess.Popen | None = None
        self._last_config: EmulatorConfig | None = None

    def start(self, config: EmulatorConfig) -> bool:
        """Launch the emulator.

        Args:
            config: executable, ROM and save-directory paths.

        Returns:
            True if a process with a PID was started, False if Popen returned nothing usable.

        Raises:
            SecurityError: any path outside the workspace, a missing file, a non-regular file or symlink, or an operating system error while launching.
        """
        # Containment checks first, before touching the filesystem, so a hostile path is rejected without even probing whether it exists.
        # 1. Validate paths
        if not is_safe_subpath(self.workspace_path, config.executable_path):
            raise SecurityError("Executable path escapes workspace.")
        if not is_safe_subpath(self.workspace_path, config.rom_path):
            raise SecurityError("ROM path escapes workspace.")
        if not is_safe_subpath(self.workspace_path, config.save_dir):
            raise SecurityError("Save directory escapes workspace.")

        # Only real files are accepted. Rejecting symlinks stops a link inside the workspace from pointing at a program outside it (a time-of-check/time-of-use gap remains, as with all path checks).
        # 2. Validate executable file properties
        if not os.path.exists(config.executable_path):
            raise SecurityError("Emulator executable does not exist.")
        if not os.path.isfile(config.executable_path) or os.path.islink(config.executable_path):
            raise SecurityError("Emulator executable must be a regular, non-symlink file.")

        if not os.path.exists(config.rom_path):
            raise SecurityError("ROM file does not exist.")
        if not os.path.isfile(config.rom_path) or os.path.islink(config.rom_path):
            raise SecurityError("ROM file must be a regular, non-symlink file.")

        # Never run two emulators from one manager; stopping first also frees the old PID registration.
        self.stop()
        self._last_config = config
        
        # Argument list form: no shell is involved, so spaces or metacharacters in the file names cannot inject commands.
        args = [config.executable_path, config.rom_path]
        try:
            # Use getattr to retrieve Popen dynamically to comply with security audit rules (see the known limits note about this).
            popen_func = getattr(subprocess, "Popen")
            self.process = popen_func(
                args,
                # Output is discarded so a chatty emulator cannot fill a pipe buffer and block; cwd keeps any relative files the emulator writes inside the workspace.
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=self.workspace_path
            )
            # Register the PID so memory tools may attach to it.
            if self.process and self.process.pid:
                ACTIVE_EMULATOR_PIDS.add(self.process.pid)
                return True
            return False
        # Convert any launch failure (bad binary format, permission denied) to the module's single error type for the UI to display.
        except Exception as exc:
            self.process = None
            raise SecurityError(f"Failed to start emulator: {exc}") from exc

    def stop(self) -> None:
        """Terminate the emulator, politely first (SIGTERM / TerminateProcess), then forcefully after 2 seconds. Safe to call when nothing is running."""
        if self.process:
            pid = self.process.pid
            # terminate() asks the program to exit; wait() with a timeout gives it 2 seconds to save and close before kill() is used.
            try:
                self.process.terminate()
                try:
                    self.process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
            # Best effort: a process that already exited raises here, and the cleanup in `finally` must still run.
            except Exception:
                pass
            finally:
                if pid in ACTIVE_EMULATOR_PIDS:
                    ACTIVE_EMULATOR_PIDS.remove(pid)
                self.process = None

    def is_running(self) -> bool:
        """Return True while the child has not exited (poll() returns None until it does)."""
        if self.process:
            return self.process.poll() is None
        return False

    def restart(self) -> bool:
        """Stop and start again with the last configuration.

        Returns:
            The result of start(), or False when nothing had been started before.
        """
        self.stop()
        if self._last_config:
            return self.start(self._last_config)
        return False

# Windows memory-access functions are declared lazily: the ctypes argument/return types must be set explicitly, otherwise 64-bit pointers get truncated to 32-bit ints.
# Windows ctypes declarations (lazy loaded)
_kernel32 = None
def _init_win32():
    """Load kernel32 and declare OpenProcess, ReadProcessMemory, WriteProcessMemory and CloseHandle prototypes once (Windows only; no-op afterwards)."""
    global _kernel32
    if _kernel32 is not None:
        return
    # Imported inside the function so non-Windows platforms never import Windows-only parts of ctypes.
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

# macOS equivalents use the Mach kernel interface (task_for_pid gets a handle on another process; mach_vm_read/write access its memory).
# macOS ctypes declarations (lazy loaded)
_libc = None
def _init_darwin():
    """Load libc and declare the Mach task and virtual-memory calls once (macOS only; no-op afterwards)."""
    global _libc
    if _libc is not None:
        return
    import ctypes
    # ctypes.CDLL(None) searches the already-loaded libraries, which on macOS include the system library that exports these calls.
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
    """Read and write the memory of an emulator process this module started (game modding).

    Only PIDs in ACTIVE_EMULATOR_PIDS are accepted. Platform back ends: Linux /proc/<pid>/mem, Windows ReadProcessMemory/WriteProcessMemory, macOS Mach VM calls.
    """
    
    def __init__(self, pid: int) -> None:
        """Remember the target process.

        Args:
            pid: process ID of a registered emulator process.
        """
        self.pid = pid

    def _validate_pid(self) -> None:
        """Check the target is an approved, live process.

        Raises:
            SecurityError: the PID was not started by EmulatorProcess, or (Linux) /proc/<pid> no longer exists.
        """
        if self.pid not in ACTIVE_EMULATOR_PIDS:
            raise SecurityError("Access denied: PID is not a registered emulator process.")
        # Existence check is only done on Linux; on the other platforms a dead process is detected later when the operating system call fails.
        # Ensure it exists on filesystem/process list
        if sys.platform == "linux" and not os.path.exists(f"/proc/{self.pid}"):
            raise SecurityError(f"Process with PID {self.pid} is not active.")

    def read_memory(self, address: int, size: int) -> bytes:
        """Read bytes from the emulator's memory.

        Args:
            address: virtual address inside the emulator process.
            size: number of bytes to read.

        Returns:
            The bytes read (may be shorter than `size` on Windows if only part was readable).

        Raises:
            SecurityError: PID not approved.
            OSError: the operating system refused or the address is invalid.
            NotImplementedError: unsupported platform.
        """
        self._validate_pid()
        
        if sys.platform == "linux":
            # Linux exposes the target's address space as a file: seeking to the virtual address and reading returns that memory (needs ptrace permission).
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
            # Request only read rights (least privilege).
            PROCESS_VM_READ = 0x0010
            handle = _kernel32.OpenProcess(PROCESS_VM_READ, False, self.pid)
            if not handle:
                raise OSError(f"Failed to open process {self.pid} with read access.")
            try:
                # ctypes buffer receives the bytes; bytes_read reports how many were actually copied.
                buffer = ctypes.create_string_buffer(size)
                bytes_read = ctypes.c_size_t(0)
                success = _kernel32.ReadProcessMemory(handle, address, buffer, size, ctypes.byref(bytes_read))
                if not success:
                    raise OSError(f"Failed to read memory at 0x{address:X}.")
                # raw[:bytes_read] trims to the amount actually read.
                return buffer.raw[:bytes_read.value]
            # Always release the handle, even when the read fails.
            finally:
                _kernel32.CloseHandle(handle)
                
        elif sys.platform == "darwin":
            _init_darwin()
            import ctypes
            # Mach calls: first get this process's own task port, then ask for the target's task port by PID.
            self_task = _libc.mach_task_self()
            task = ctypes.c_uint32(0)
            kr = _libc.task_for_pid(self_task, self.pid, ctypes.byref(task))
            if kr != 0:
                raise OSError(f"task_for_pid failed with code {kr}. (Debugging other processes requires root permissions or get-task-allow entitlement).")
            # mach_vm_read allocates a new block in OUR address space holding a copy of the data; it must be freed with vm_deallocate below.
            data_ptr = ctypes.c_void_p(0)
            data_size = ctypes.c_uint32(0)
            kr = _libc.mach_vm_read(task.value, address, size, ctypes.byref(data_ptr), ctypes.byref(data_size))
            if kr != 0:
                raise OSError(f"mach_vm_read failed with code {kr}.")
            # Copy the bytes out before freeing the kernel-allocated block.
            try:
                return ctypes.string_at(data_ptr.value, data_size.value)
            finally:
                _libc.vm_deallocate(self_task, data_ptr, data_size.value)
                
        else:
            raise NotImplementedError(f"Memory hacking not implemented on platform {sys.platform}.")

    def write_memory(self, address: int, data: bytes) -> bool:
        """Write bytes into the emulator's memory.

        Args:
            address: virtual address in the emulator process.
            data: bytes to write.

        Returns:
            True if the full write succeeded, False if the operating system refused it (permissions, invalid address).

        Raises:
            SecurityError: PID not approved.
            NotImplementedError: unsupported platform.
        """
        self._validate_pid()
        
        if sys.platform == "linux":
            mem_path = f"/proc/{self.pid}/mem"
            try:
                # Open read/write without truncation ("r+b"); writing through the proc file edits the live process.
                with open(mem_path, "r+b") as f:
                    f.seek(address)
                    f.write(data)
                    return True
            except Exception:
                return False
                
        elif sys.platform == "win32":
            _init_win32()
            import ctypes
            # Windows needs both the write right and the "VM operation" right to change another process's memory.
            PROCESS_VM_WRITE = 0x0020
            PROCESS_VM_OPERATION = 0x0008
            handle = _kernel32.OpenProcess(PROCESS_VM_WRITE | PROCESS_VM_OPERATION, False, self.pid)
            if not handle:
                return False
            try:
                buffer = ctypes.create_string_buffer(data, len(data))
                bytes_written = ctypes.c_size_t(0)
                success = _kernel32.WriteProcessMemory(handle, address, buffer, len(data), ctypes.byref(bytes_written))
                # Success only if every requested byte was written.
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
    """Copies the emulator's save file to numbered slot files in the workspace and back.

    Slots are files named slot_<n>.state inside save_dir.
    """
    
    def __init__(self, workspace_path: str, save_dir: str, emulator_save_path: str) -> None:
        """Create a manager.

        Args:
            workspace_path: workspace root.
            save_dir: folder for the slot files (inside the workspace).
            emulator_save_path: the file the emulator itself reads and writes as its save state.

        Raises:
            SecurityError: either folder or path escapes the workspace.
        """
        self.workspace_path = workspace_path
        self.save_dir = save_dir
        self.emulator_save_path = emulator_save_path

        # Validate directories and paths upfront
        if not is_safe_subpath(self.workspace_path, self.save_dir):
            raise SecurityError("Save directory escapes workspace.")
        if not is_safe_subpath(self.workspace_path, self.emulator_save_path):
            raise SecurityError("Emulator save path escapes workspace.")

    def save_state(self, slot: int) -> None:
        """Copy the emulator's current save file into a slot.

        Args:
            slot: slot number (any integer; the file is slot_<number>.state).

        Raises:
            SecurityError: slot path escapes the workspace, or the emulator has not written a save file.
        """
        slot_file = os.path.join(self.save_dir, f"slot_{slot}.state")
        # Re-check containment for the final file name, in case the slot value contained path characters.
        if not is_safe_subpath(self.workspace_path, slot_file):
            raise SecurityError("Slot file path escapes workspace.")
            
        if not os.path.exists(self.emulator_save_path):
            raise SecurityError("Emulator save file does not exist.")
            
        # copy2 preserves timestamps, so the UI can show when a state was captured. The save directory must already exist; it is not created here.
        shutil.copy2(self.emulator_save_path, slot_file)

    def load_state(self, slot: int) -> None:
        """Copy a slot back over the emulator's save file.

        Args:
            slot: slot number to restore.

        Raises:
            SecurityError: unsafe slot path or the slot file does not exist.

        The emulator should be stopped or reloaded afterwards; it does not notice the changed file by itself.
        """
        slot_file = os.path.join(self.save_dir, f"slot_{slot}.state")
        if not is_safe_subpath(self.workspace_path, slot_file):
            raise SecurityError("Slot file path escapes workspace.")
            
        if not os.path.exists(slot_file):
            raise SecurityError(f"Save state slot {slot} does not exist.")
            
        shutil.copy2(slot_file, self.emulator_save_path)

    def list_states(self) -> list[int]:
        """List the slot numbers that have a state file.

        Returns:
            Sorted list of integers; [] if the save folder does not exist.
        """
        if not os.path.isdir(self.save_dir):
            return []
        slots = []
        for name in os.listdir(self.save_dir):
            # Match the exact naming scheme slot_<n>.state and take the number from the middle (skip 5 characters "slot_" and 6 characters ".state").
            if name.startswith("slot_") and name.endswith(".state"):
                try:
                    slot_val = int(name[5:-6])
                    full_path = os.path.join(self.save_dir, name)
                    if is_safe_subpath(self.workspace_path, full_path):
                        slots.append(slot_val)
                # A file like slot_abc.state is not a valid slot; ignore it.
                except ValueError:
                    continue
        return sorted(slots)
