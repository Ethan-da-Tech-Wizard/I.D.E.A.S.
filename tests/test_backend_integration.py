# ==============================================================================
# Program: tests/test_backend_integration.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: cheap integration checks that the backend modules load together and that MainWindow's
# lazy-initialisation helpers (peer-to-peer manager, emulator process) work without building a
# real window. Heavy objects are created only on first use, which these tests pin down.
# Run: python3 -m pytest tests/test_backend_integration.py -q
# ==============================================================================

import sys
import pytest
from unittest.mock import MagicMock

# Smoke test: importing these modules would raise on a syntax error, missing dependency or circular
# import. Imports are inside the function so a failure is reported as this test, not as a
# collection error for the whole file.
def test_imports():
    # Importing app.application also pulls in the Qt widgets layer, so this doubles as a PySide6 check.
    # Verify that everything imports cleanly from the root
    import app.network.p2p
    import app.services.emulators
    import app.application

# Scenario: call ensure_p2p_manager and ensure_emulator_process on a stand-in object. The first call
# must create and cache the manager with the given peer id, name and port; the second must create
# the emulator wrapper for the workspace path. Using a stand-in avoids needing a QApplication.
def test_lazy_initialization():
    # Test ensure_p2p_manager and ensure_emulator_process without creating QApplication/QWidget
    from app.application import MainWindow
    
    # We mock QMainWindow/QWidget methods if necessary, or mock the MainWindow class
    # to test only ensure_p2p_manager and ensure_emulator_process methods.
    # Since MainWindow inherits from QMainWindow, calling __init__ requires QApplication.
    # We can inspect the methods or instantiate a dummy class.
    
    # Stand-in that borrows the two real functions from MainWindow. They only touch self.p2p_manager
    # and self.emulator_process, so a plain object with those attributes is enough.
    class DummyMainWindow:
        def __init__(self):
            # Both start as None: that is the "not created yet" state the lazy helpers test for.
            self.p2p_manager = None
            self.emulator_process = None
            
        # Assigning the plain functions as class attributes turns them into bound methods of the stand-in.
        # Bind the methods from MainWindow
        ensure_p2p_manager = MainWindow.ensure_p2p_manager
        ensure_emulator_process = MainWindow.ensure_emulator_process

    dummy = DummyMainWindow()
    
    # Verify ensure_p2p_manager lazy-init
    # Port 5005 is an arbitrary local value; nothing listens because the manager is not started.
    mgr = dummy.ensure_p2p_manager("my_peer", "Bob", port=5005)
    assert dummy.p2p_manager is not None
    assert mgr == dummy.p2p_manager
    assert mgr.local_peer_info.peer_id == "my_peer"
    assert mgr.local_peer_info.display_name == "Bob"
    assert mgr.local_peer_info.port == 5005
    
    # Verify ensure_emulator_process lazy-init
    # The path need not exist: construction only stores it, no emulator process is launched.
    emu = dummy.ensure_emulator_process("/dummy/workspace")
    assert dummy.emulator_process is not None
    assert emu == dummy.emulator_process
    assert emu.workspace_path == "/dummy/workspace"
