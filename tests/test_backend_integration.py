import sys
import pytest
from unittest.mock import MagicMock

def test_imports():
    # Verify that everything imports cleanly from the root
    import app.network.p2p
    import app.services.emulators
    import app.application

def test_lazy_initialization():
    # Test ensure_p2p_manager and ensure_emulator_process without creating QApplication/QWidget
    from app.application import MainWindow
    
    # We mock QMainWindow/QWidget methods if necessary, or mock the MainWindow class
    # to test only ensure_p2p_manager and ensure_emulator_process methods.
    # Since MainWindow inherits from QMainWindow, calling __init__ requires QApplication.
    # We can inspect the methods or instantiate a dummy class.
    
    class DummyMainWindow:
        def __init__(self):
            self.p2p_manager = None
            self.emulator_process = None
            
        # Bind the methods from MainWindow
        ensure_p2p_manager = MainWindow.ensure_p2p_manager
        ensure_emulator_process = MainWindow.ensure_emulator_process

    dummy = DummyMainWindow()
    
    # Verify ensure_p2p_manager lazy-init
    mgr = dummy.ensure_p2p_manager("my_peer", "Bob", port=5005)
    assert dummy.p2p_manager is not None
    assert mgr == dummy.p2p_manager
    assert mgr.local_peer_info.peer_id == "my_peer"
    assert mgr.local_peer_info.display_name == "Bob"
    assert mgr.local_peer_info.port == 5005
    
    # Verify ensure_emulator_process lazy-init
    emu = dummy.ensure_emulator_process("/dummy/workspace")
    assert dummy.emulator_process is not None
    assert emu == dummy.emulator_process
    assert emu.workspace_path == "/dummy/workspace"
