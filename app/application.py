# ==============================================================================
# Program: application.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: Core Qt MainWindow controller uniting the IDE, DAW, Engine, AI, and Studio creative workspaces. Manages top-level menus, docking splitters, tabbed navigation, media player controls, and workspace state.
# ==============================================================================
# How this module fits: app/main.py calls create_app() to get the QApplication and the MainWindow, then shows the window and runs
# the Qt event loop. MainWindow is the hub of the whole program: it owns one "mode" per creative workspace and swaps between them
# with a QStackedWidget driven by the activity bar on the left:
#     0 IDE (code editor + explorer + console), 1 DAW (app/ui/daw.py), 2 Engine (app/ui/engine), 3 Video (app/ui/studio/video_timeline.py),
#     4 Photo (app/ui/studio/photo_editor.py), 5 Asset manager (app/ui/assets).
# Only the IDE is built at start-up. The other five are built lazily (_lazy_build_mode) the first time their button is pressed, because
# the audio and graphics back ends can be slow or fail on a machine without the right hardware, and the editor must always open.
# Inputs: user clicks, menu actions and keyboard shortcuts. Outputs: widgets, console text, and calls into app/project.py,
# app/storage/workspace.py (save/load of a whole workspace) and app/security.py (every file read and every script run).
# Design decisions: workspace state is exchanged as plain dicts via each widget's get_state()/load_state() so this window never needs
# to know their internals; user scripts are never exec()ed, they go through security.run_safe_script which only allows sample playback and print.
# Known limits / bugs noticed (comment pass only, not fixed here):
#   - QTabBar is used in build_ide_mode() but is not imported at the top of this file, so building the IDE page raises NameError;
#     it must be added to the PySide6.QtWidgets import list.
#   - save_project() stores only the CURRENT editor tab, not every open file.
#   - run_code() substitutes macro values into the source text with a regular expression, so a macro name that appears inside a string or
#     comment is rewritten too, and a name containing regex metacharacters would misbehave (re.escape is not used).
#   - stop_code() is a stub; there is no process to stop because scripts run in-process through the safe interpreter.
#   - the p2p manager is created with the loopback address as the "local" address, so peers see 127.0.0.1 as our address.
# How to run: python -m app.main (needs PySide6 and a display); there is no headless test for this class because it builds real widgets.

import sys
import os
from typing import Any
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QSplitter, QTabWidget, QFileDialog, QMenuBar, QTextEdit,
    QStackedWidget, QGraphicsDropShadowEffect, QFrame
)
from PySide6.QtCore import Qt, QUrl, QDir
from PySide6.QtGui import QAction, QColor, QFont, QTextCursor
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from app.project import load_project_file, save_project_file
from app.storage.workspace import load_workspace, save_workspace
from app.security import SecurityError, read_text_file_safely, resolve_sample_path, run_safe_script
from app.storage.database import init_db
from app.ui.editor import CodeEditor, EditorArea
from app.ui.explorer import FileExplorer
from app.ui.welcome import SidebarWelcome, EditorWelcome

# Small helper used all over the UI so every "card" (activity bar, explorer, editor, console) gets the same soft drop shadow.
def create_shadow():
    """Build a new QGraphicsDropShadowEffect.

    Returns:
        A fresh effect each call; Qt allows an effect to be attached to only one widget, and attaching it to a second widget silently
        removes it from the first, which is why this is a factory instead of a shared module-level object.
    """
    # Blur radius 20 px with a 4 px downward offset and ~59% black (alpha 150 of 255) reads as a floating panel without hiding the dark theme.
    shadow = QGraphicsDropShadowEffect()
    shadow.setBlurRadius(20)
    shadow.setColor(QColor(0, 0, 0, 150))
    shadow.setOffset(0, 4)
    return shadow

# Top-level window and controller. Subclassing QMainWindow gives us the menu bar and the central widget slot for free.
class MainWindow(QMainWindow):
    def __init__(self) -> None:
        """Create the window, initial empty state, menu, widgets, stylesheet, shortcuts and key bindings.

        Everything optional (DAW, engine, studio widgets, network, emulator) starts as None and is filled in lazily by the
        ensure_* / build_* methods, so a failure in one of them cannot prevent the window from appearing.
        """
        super().__init__()
        self.setWindowTitle("I.D.E.A.S. - IDE, DAW, Engines, AI, Studio")
        self.resize(1300, 850)
        # State flags and handles. is_playing mirrors the "global audio" button; player/audio_output are the QMediaPlayer pair created on first sound;
        # the *_widget fields hold lazily built mode pages; current_workspace_path is the folder the user opened or saved, shared with the DAW, terminal and search panel.
        
        self.is_playing = False
        self.player = None
        self.audio_output = None
        self.daw_workspace = None
        self.engine_widget = None
        self.engine_panel_manager = None
        self.video_editor_widget = None
        self.photo_editor_widget = None
        self.asset_manager_widget = None
        self.current_workspace_path = None
# Collaboration and emulator helpers are also created only on demand (ensure_p2p_manager / ensure_emulator_process).

        self.p2p_manager = None
        self.emulator_process = None
# Order matters: setup_menu() must run before keybindings are applied (it creates new_file_act, open_file_act and save_project_act),
# and setup_ui() creates the console and editor that the actions below talk to.

        self.setup_menu()
        self.setup_ui()
        self.load_stylesheet()

        # These four QActions are added to the window itself (addAction) rather than a menu so the shortcuts work even though no menu entry exists.
        # Ctrl+B and Ctrl+` are the VS Code defaults; KeybindingsEngine may replace them below.
        # Toggle Layout Shortcuts
        self.sidebar_toggle_act = QAction(self)
        self.sidebar_toggle_act.setShortcut("Ctrl+B")
        self.sidebar_toggle_act.triggered.connect(self.toggle_sidebar)
        self.addAction(self.sidebar_toggle_act)

        self.console_toggle_act = QAction(self)
        self.console_toggle_act.setShortcut("Ctrl+`")
        self.console_toggle_act.triggered.connect(self.toggle_console)
        self.addAction(self.console_toggle_act)

        # Run/Stop have no default shortcut here on purpose; the KeybindingsEngine assigns Ctrl+R / Ctrl+K (or the user's choice) in load_and_apply().
        # Run/Stop Code Actions
        self.run_code_act = QAction(self)
        self.run_code_act.triggered.connect(self.run_code)
        self.addAction(self.run_code_act)

        self.stop_code_act = QAction(self)
        self.stop_code_act.triggered.connect(self.stop_code)
        self.addAction(self.stop_code_act)

        # Imported inside __init__ so the keybindings module (and its Qt imports) is loaded only once a window exists.
        # It must run last because it reads every *_act attribute created above.
        # Initialize and apply keybindings
        from app.ide.keybindings import KeybindingsEngine
        self.keybindings_engine = KeybindingsEngine(self)
        self.keybindings_engine.load_and_apply()

    def ensure_p2p_manager(self, peer_id: str, display_name: str, port: int = 5001) -> 'NetworkManager':
        """Create the collaboration manager on first use and return the same instance afterwards.

        Args:
            peer_id: unique id for this running app (used to recognise our own broadcasts).
            display_name: name other users see.
            port: TCP port to advertise; default 5001.

        Returns:
            The shared NetworkManager (app/network/p2p.py).
        """
        # Import deferred so users who never open Collaborate do not pay for (or risk errors in) the networking module.
        if self.p2p_manager is None:
            from app.network.p2p import NetworkManager, PeerInfo
            # Our own PeerInfo uses the loopback address as a placeholder; the real LAN address is chosen later when hosting starts.
            local_peer = PeerInfo("127.0.0.1", port, peer_id, display_name)
            self.p2p_manager = NetworkManager(local_peer)
        return self.p2p_manager

    def ensure_emulator_process(self, workspace_path: str) -> 'EmulatorProcess':
        """Create the emulator helper on first use and return the same instance afterwards.

        Args:
            workspace_path: folder the emulator process should work in; only used the first time (later calls reuse the existing instance).

        Returns:
            The shared EmulatorProcess (app/services/emulators.py).
        """
        if self.emulator_process is None:
            from app.services.emulators import EmulatorProcess
            self.emulator_process = EmulatorProcess(workspace_path)
        return self.emulator_process
        
    def setup_menu(self):
        """Build the File menu and create the QActions that other code (KeybindingsEngine) needs to find by attribute name."""
        menubar = self.menuBar()
        # Menu entries that need shortcuts are stored on self (new_file_act, open_file_act, save_project_act); the rest are local because nothing else refers to them.
        file_menu = menubar.addMenu("File")
        
        # new_file_act and open_file_act get no shortcut here; the key-binding engine assigns Ctrl+N / Ctrl+O so the user can customise them.
        self.new_file_act = QAction("New File...", self)
        self.new_file_act.triggered.connect(self.new_file)
        file_menu.addAction(self.new_file_act)
        
        self.open_file_act = QAction("Open File...", self)
        self.open_file_act.triggered.connect(self.open_file)
        file_menu.addAction(self.open_file_act)
        
        open_folder_action = QAction("Open Folder...", self)
        open_folder_action.triggered.connect(self.open_folder)
        file_menu.addAction(open_folder_action)
        
        file_menu.addSeparator()
        
        # Save/Load Workspace carry fixed Ctrl+S and Ctrl+O shortcuts here, and KeybindingsEngine overrides save with the same key for File:Save.
        # Note the Ctrl+O default is shared with the File:Open binding, so only one of the two actually fires; see the collision warning in keybindings.py.
        self.save_project_act = QAction("Save Workspace...", self)
        self.save_project_act.setShortcut("Ctrl+S")
        self.save_project_act.triggered.connect(self.save_project)
        file_menu.addAction(self.save_project_act)
        
        load_project_action = QAction("Load Workspace...", self)
        load_project_action.setShortcut("Ctrl+O")
        load_project_action.triggered.connect(self.load_project)
        file_menu.addAction(load_project_action)
        
    def new_file(self):
        """Open an empty, unsaved "Untitled" editor tab."""
        self.open_new_tab("Untitled", "")
        
    def open_file(self):
        """Ask the user for a file and open it in an editor tab.

        Read through security.read_text_file_safely so huge, binary or non-UTF-8 files are refused with a console message rather than loaded into the editor.
        """
        # Start in the home folder because a blocking native dialog has no better default; returns an empty path if the user cancels.
        file_path, _ = QFileDialog.getOpenFileName(self, "Open File", QDir.homePath())
        if file_path:
            try:
                content = read_text_file_safely(file_path)
            except SecurityError as exc:
                self.append_console(f"Open blocked: {exc}", error=True)
                return
            self.open_new_tab(os.path.basename(file_path), content, file_path)

    def _set_current_workspace(self, folder_path: str) -> None:
        """Remember the workspace folder and tell every component that is scoped to it.

        Args:
            folder_path: absolute path of the workspace the user just opened, saved or loaded.

        The DAW needs it for custom sample import, the terminal for its working directory and WORKSPACE variable, the search panel
        for its search root, and the git status label for the branch lookup.
        """
        self.current_workspace_path = folder_path
        # Each component may not exist yet (DAW is lazy; terminal and search panel are created inside build_ide_mode), hence the None and hasattr guards.
        if self.daw_workspace is not None:
            self.daw_workspace.set_workspace_path(folder_path)
        if hasattr(self, 'terminal') and self.terminal:
            self.terminal.workspace_path = folder_path
            self.terminal.locals["WORKSPACE"] = folder_path
        if hasattr(self, 'search_panel') and self.search_panel:
            self.search_panel.set_workspace(folder_path)
        self.update_git_branch_status()

    def open_folder(self):
        """Choose a workspace folder, show it in the explorer and propagate the choice."""
        folder_path = QFileDialog.getExistingDirectory(self, "Open Workspace Folder", QDir.homePath())
        if folder_path:
            self.explorer.set_workspace(folder_path)
            # Index 1 of the sidebar stack is the explorer/search tabs; index 0 is the welcome page that shows before any folder is open.
            self.sidebar_stack.setCurrentIndex(1)
            if self.asset_manager_widget is not None:
                self.asset_manager_widget.set_root(folder_path)
            self._set_current_workspace(folder_path)
            self.append_console(f"> Workspace changed to: {folder_path}")

    def save_project(self):
        """Collect the state of every built workspace and write it to a folder.

        Only the widgets that have been built contribute a section, so saving before ever opening the DAW does not force it to load.
        Failures from app/storage/workspace.py (SecurityError) are shown in the console, not raised.
        """
        folder_path = QFileDialog.getExistingDirectory(self, "Save Workspace", QDir.homePath())
        if not folder_path:
            return

        project_state: dict[str, Any] = {}

        # Only the active editor tab is saved, and only when it is a CodeEditor (not a welcome page); other open files are lost on reload.
        # Save IDE State
        current_widget = self.tabs.currentWidget()
        if isinstance(current_widget, CodeEditor):
            project_state["ide_content"] = current_widget.toPlainText()
            project_state["ide_filename"] = self.tabs.tabText(self.tabs.currentIndex())

        # Each section key ("daw", "engine", "video_editor", ...) is what load_project() looks for later; renaming one breaks older saved workspaces.
        # Save DAW State
        if self.daw_workspace is not None:
            project_state["daw"] = self.daw_workspace.get_state()
        if self.engine_widget is not None:
            project_state["engine"] = self.engine_widget.get_state()

        # Save Studio State
        if self.video_editor_widget is not None:
            project_state["video_editor"] = self.video_editor_widget.get_state()
        if self.photo_editor_widget is not None:
            project_state["photo_editor"] = self.photo_editor_widget.get_state()
        if self.asset_manager_widget is not None:
            project_state["asset_manager"] = self.asset_manager_widget.get_state()

        try:
            saved_path = save_workspace(folder_path, project_state)
        except SecurityError as exc:
            self.append_console(f"Workspace save blocked: {exc}", error=True)
            return

        self._set_current_workspace(folder_path)
        self.append_console(f"> Workspace saved successfully: {saved_path}")

    def load_project(self):
        """Pick a workspace folder, read it, and push each saved section back into its widget.

        Each restore block builds the target mode first if it has never been opened (switch_mode(n) builds it, switch_mode(0) returns
        to the IDE), because load_state() needs a live widget to write into.
        """
        folder_path = QFileDialog.getExistingDirectory(self, "Load Workspace", QDir.homePath())
        if not folder_path:
            return
            
        try:
            project_state = load_workspace(folder_path)
        except SecurityError as exc:
            self.append_console(f"Workspace load blocked: {exc}", error=True)
            return
            
        # The code text is reopened as a tab; the saved file name is a label only, so the file on disk (if any) is not re-read.
        # Restore IDE
        if "ide_content" in project_state:
            filename = project_state.get("ide_filename", "main.py")
            self.open_new_tab(filename, project_state["ide_content"])
            
        # Switching to a mode and straight back is the simplest way to reuse the lazy-build code path; the page flickers for one event-loop turn.
        # Restore DAW
        if "daw" in project_state:
            if self.daw_workspace is None:
                self.switch_mode(1) # This forces DAW to build
                self.switch_mode(0) # Switch back to IDE
            self.daw_workspace.load_state(project_state["daw"])
        if "engine" in project_state:
            if self.engine_widget is None:
                self.switch_mode(2) # This forces Engine to build
                self.switch_mode(0) # Switch back to IDE
            self.engine_widget.load_state(project_state["engine"])

        # Restore Studio
        if "video_editor" in project_state:
            if self.video_editor_widget is None:
                self.switch_mode(3) # This forces Video Editor to build
                self.switch_mode(0) # Switch back to IDE
            self.video_editor_widget.load_state(project_state["video_editor"])
        if "photo_editor" in project_state:
            if self.photo_editor_widget is None:
                self.switch_mode(4) # This forces Photo Editor to build
                self.switch_mode(0) # Switch back to IDE
            self.photo_editor_widget.load_state(project_state["photo_editor"])
        if "asset_manager" in project_state:
            if self.asset_manager_widget is None:
                self.switch_mode(5) # This forces Asset Manager to build
                self.switch_mode(0) # Switch back to IDE
            self.asset_manager_widget.load_state(project_state["asset_manager"])

        self._set_current_workspace(folder_path)
        self.append_console(f"> Workspace loaded successfully: {folder_path}")
            
    def setup_ui(self):
        """Create the permanent frame of the window: activity bar on the left and a stack of mode pages on the right."""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # Zero margins and spacing so the activity bar sits flush against the page area and the dark theme has no stray gaps.
        # Base layout: Activity Bar + Top-Level Mode Stack
        base_layout = QHBoxLayout(central_widget)
        base_layout.setContentsMargins(0, 0, 0, 0)
        base_layout.setSpacing(0)
        
        # Activity bar: a fixed 70 px column of mode buttons. The object name "ActivityBar" / "activityBtn" are the selectors used in ui/style.qss;
        # the dynamic property "active" is what the stylesheet matches to highlight the current mode (see switch_mode).
        # --- ACTIVITY BAR ---
        activity_bar = QWidget()
        activity_bar.setObjectName("ActivityBar")
        activity_bar.setFixedWidth(70)
        activity_layout = QVBoxLayout(activity_bar)
        activity_layout.setContentsMargins(0, 20, 0, 20)
        activity_layout.setSpacing(20)
        activity_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        
        # Each button is wired with a lambda holding its mode index; the index must match the order pages are added to mode_stack below.
        self.btn_ide_mode = QPushButton("</>")
        self.btn_ide_mode.setObjectName("activityBtn")
        self.btn_ide_mode.setProperty("active", "true")
        self.btn_ide_mode.clicked.connect(lambda: self.switch_mode(0))
        
        self.btn_daw_mode = QPushButton("🎵")
        self.btn_daw_mode.setObjectName("activityBtn")
        self.btn_daw_mode.setProperty("active", "false")
        self.btn_daw_mode.clicked.connect(lambda: self.switch_mode(1))

        self.btn_engine_mode = QPushButton("ENG")
        self.btn_engine_mode.setObjectName("activityBtn")
        self.btn_engine_mode.setProperty("active", "false")
        self.btn_engine_mode.clicked.connect(lambda: self.switch_mode(2))

        self.btn_video_mode = QPushButton("🎬")
        self.btn_video_mode.setObjectName("activityBtn")
        self.btn_video_mode.setProperty("active", "false")
        self.btn_video_mode.clicked.connect(lambda: self.switch_mode(3))

        self.btn_photo_mode = QPushButton("🖌")
        self.btn_photo_mode.setObjectName("activityBtn")
        self.btn_photo_mode.setProperty("active", "false")
        self.btn_photo_mode.clicked.connect(lambda: self.switch_mode(4))

        self.btn_assets_mode = QPushButton("📁")
        self.btn_assets_mode.setObjectName("activityBtn")
        self.btn_assets_mode.setProperty("active", "false")
        self.btn_assets_mode.clicked.connect(lambda: self.switch_mode(5))

        # Added in the same order as the stack pages so the visual order and index order stay in step.
        activity_layout.addWidget(self.btn_ide_mode)
        activity_layout.addWidget(self.btn_daw_mode)
        activity_layout.addWidget(self.btn_engine_mode)
        activity_layout.addWidget(self.btn_video_mode)
        activity_layout.addWidget(self.btn_photo_mode)
        activity_layout.addWidget(self.btn_assets_mode)
        activity_bar.setGraphicsEffect(create_shadow())

        base_layout.addWidget(activity_bar)

        # Page stack: index 0 is the real IDE; indexes 1-5 are cheap empty placeholder QWidgets that _lazy_build_mode swaps for the real page on first use.
        # --- TOP LEVEL STACK (IDE vs DAW vs Studio) ---
        self.mode_stack = QStackedWidget()
        base_layout.addWidget(self.mode_stack, 1)

        # 1. The IDE is built eagerly because it is the landing page and has no heavy dependencies.
        # 1. Build IDE Mode
        self.mode_stack.addWidget(self.build_ide_mode())

        # 2. DAW mode is built lazily so audio backends cannot block IDE startup.
        self.mode_stack.addWidget(QWidget())

        # 3. Engine mode is built lazily.
        self.mode_stack.addWidget(QWidget())

        # 4-5. Studio modes (Video Editor, Photo Editor) are also built lazily.
        self.mode_stack.addWidget(QWidget())
        self.mode_stack.addWidget(QWidget())

        # 6. Asset Manager is also built lazily.
        self.mode_stack.addWidget(QWidget())

    def _lazy_build_mode(self, index: int, attr_name: str, builder) -> None:
        """Build a mode page once, replacing the placeholder widget in the stack.

        Args:
            index: position in mode_stack of the placeholder to replace.
            attr_name: name of the MainWindow attribute that caches the built widget (also checked to know whether it is already built).
            builder: zero-argument callable returning the new QWidget.
        """
        # Already built: do nothing, so repeated button clicks never rebuild (and lose the state of) a page.
        if getattr(self, attr_name) is None:
            placeholder = self.mode_stack.widget(index)
            widget = builder()
            setattr(self, attr_name, widget)
            # Remove and schedule deletion of the placeholder, then insert the real page at the same index so every other index stays valid.
            self.mode_stack.removeWidget(placeholder)
            placeholder.deleteLater()
            self.mode_stack.insertWidget(index, widget)

    def switch_mode(self, index: int):
        """Show mode page `index`, building it first if needed, and restyle the activity buttons.

        Args:
            index: 0 IDE, 1 DAW, 2 Engine, 3 Video, 4 Photo, 5 Assets.
        """
        if index == 1:
            self._lazy_build_mode(1, "daw_workspace", self.build_daw_mode)
        elif index == 2:
            self._lazy_build_mode(2, "engine_widget", self.build_engine_mode)
        elif index == 3:
            self._lazy_build_mode(3, "video_editor_widget", self.build_video_editor_mode)
        elif index == 4:
            self._lazy_build_mode(4, "photo_editor_widget", self.build_photo_editor_mode)
        elif index == 5:
            self._lazy_build_mode(5, "asset_manager_widget", self.build_asset_manager_mode)
        self.mode_stack.setCurrentIndex(index)

        # Engine mode has floating dock panels owned by the main window, so they must be hidden whenever another mode is shown.
        # Show/hide engine dock panels based on active mode
        self._sync_engine_panels(index == 2)

        # Qt style sheets are evaluated when a widget is polished; changing the dynamic "active" property does nothing visible until the style is
        # unpolished and polished again, hence the explicit unpolish/polish pair.
        # Update active styling on activity buttons
        mode_buttons = (
            self.btn_ide_mode, self.btn_daw_mode, self.btn_engine_mode,
            self.btn_video_mode, self.btn_photo_mode, self.btn_assets_mode,
        )
        for mode_index, button in enumerate(mode_buttons):
            button.setProperty("active", "true" if index == mode_index else "false")
            # Force re-evaluation of QSS
            button.style().unpolish(button)
            button.style().polish(button)

    def _sync_engine_panels(self, show: bool) -> None:
        """Show or hide the engine dock panels.

        Args:
            show: True when the engine mode is active.
        """
        # Panels only exist after the engine page has been built once.
        if self.engine_panel_manager is None:
            return
        for name in self.engine_panel_manager.active_panels():
            # Reaches into the manager's private _panels dict because it exposes no public lookup; a refactor of PanelManager would break this line.
            panel = self.engine_panel_manager._panels.get(name)
            if panel is not None:
                panel.setVisible(show)

    def build_ide_mode(self) -> QWidget:
        """Build the IDE page: header, explorer sidebar, editor tabs, console/terminal/problems, macro panel and status bar.

        Returns:
            The finished page widget, which the caller adds to mode_stack at index 0.

        Several widgets are stored on self because other methods update them later (console, tabs, status labels, explorer, search_panel, macro_panel).
        """
        ide_widget = QWidget()
        main_layout = QVBoxLayout(ide_widget)
        # Generous outer margin (20 px) with a smaller bottom margin so the status bar sits close to the window edge.
        main_layout.setContentsMargins(20, 20, 20, 10)
        main_layout.setSpacing(15)
        
        # Header row: product title on the left, the global audio toggle on the right (stretch pushes it to the edge).
        # IDE HEADER
        header_layout = QHBoxLayout()
        title_label = QLabel("NEON DAW : IDE")
        title_label.setFont(QFont("Arial", 24, QFont.Weight.Bold))
        # The accent colour #00f0ff is the app's neon cyan; apply_global_theme recolours the stylesheet but this inline style is fixed.
        title_label.setStyleSheet("color: #00f0ff; letter-spacing: 2px;")
        
        self.play_btn = QPushButton("START GLOBAL AUDIO")
        self.play_btn.clicked.connect(self.toggle_playback)
        self.play_btn.setGraphicsEffect(create_shadow())
        
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        header_layout.addWidget(self.play_btn)
        main_layout.addLayout(header_layout)
        
        # Three-way horizontal split: sidebar | editor/console column | macro panel. The stretch factor 1 gives the splitter all spare space.
        # SPLITTER
        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(main_splitter, 1)
        
        # Left panel: a stack with a welcome page (index 0, no workspace yet) and the explorer/search tabs (index 1). Minimum width 220 px keeps file names legible.
        # LEFT PANEL (Explorer)
        self.sidebar_stack = QStackedWidget()
        self.sidebar_stack.setMinimumWidth(220)
        self.sidebar_stack.setGraphicsEffect(create_shadow())
        
        self.sidebar_welcome = SidebarWelcome(self)
        self.sidebar_stack.addWidget(self.sidebar_welcome)
        
        explorer_panel = QWidget()
        explorer_layout = QVBoxLayout(explorer_panel)
        explorer_layout.setContentsMargins(0, 0, 0, 0)
        
        # Explorer toolbar: a title plus two small icon buttons that reuse new_file and open_folder. The repeated inline stylesheet removes the default button chrome
        # so they look like icons.
        explorer_title_layout = QHBoxLayout()
        explorer_title = QLabel("EXPLORER")
        explorer_title.setObjectName("panelTitle")
        
        btn_explorer_new = QPushButton("📄")
        btn_explorer_new.setToolTip("New File...")
        btn_explorer_new.setFixedWidth(28)
        btn_explorer_new.setFixedHeight(22)
        btn_explorer_new.setStyleSheet("""
            QPushButton {
                padding: 0; background: transparent; border: none; font-size: 13px;
            }
            QPushButton:hover {
                color: #00f0ff;
            }
        """)
        btn_explorer_new.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_explorer_new.clicked.connect(self.new_file)
        
        btn_explorer_open = QPushButton("📂")
        btn_explorer_open.setToolTip("Open Folder...")
        btn_explorer_open.setFixedWidth(28)
        btn_explorer_open.setFixedHeight(22)
        btn_explorer_open.setStyleSheet("""
            QPushButton {
                padding: 0; background: transparent; border: none; font-size: 13px;
            }
            QPushButton:hover {
                color: #00f0ff;
            }
        """)
        btn_explorer_open.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_explorer_open.clicked.connect(self.open_folder)
        
        explorer_title_layout.addWidget(explorer_title)
        explorer_title_layout.addStretch()
        explorer_title_layout.addWidget(btn_explorer_new)
        explorer_title_layout.addWidget(btn_explorer_open)
        
        # FileExplorer emits doubleClicked with a model index; on_file_double_clicked turns that into an opened tab.
        self.explorer = FileExplorer()
        self.explorer.doubleClicked.connect(self.on_file_double_clicked)
        explorer_layout.addLayout(explorer_title_layout)
        explorer_layout.addWidget(self.explorer)
        
        # Search and Explorer share one tab widget. setTabBar(QTabBar()) installs a fresh plain tab bar (the intent is not documented in the code);
        # it is also the line that raises NameError, see the missing-import note in the file header.
        self.sidebar_tabs = QTabWidget()
        self.sidebar_tabs.setTabBar(QTabBar())
        self.sidebar_tabs.addTab(explorer_panel, "EXPLORER")
        
        # Imported here because search_panel pulls in file-reading helpers that are only needed once the IDE page is built.
        from app.ui.search_panel import SearchPanel
        self.search_panel = SearchPanel(self)
        self.search_panel.result_selected.connect(self.navigate_to_problem)
        self.sidebar_tabs.addTab(self.search_panel, "SEARCH")
        
        self.sidebar_stack.addWidget(self.sidebar_tabs)
        main_splitter.addWidget(self.sidebar_stack)
        
        # Right column: editor on top, console/terminal/problems below, in a vertical splitter the user can drag.
        # RIGHT PANEL (Editor / Console)
        right_splitter = QSplitter(Qt.Orientation.Vertical)
        main_splitter.addWidget(right_splitter)
        
        # Editor stack: index 0 is the welcome screen shown when no tab is open, index 1 is the real tab area. open_new_tab and close_tab flip between them.
        # Top half: Tabs
        self.editor_stack = QStackedWidget()
        self.editor_stack.setMinimumWidth(400)
        self.editor_stack.setGraphicsEffect(create_shadow())
        
        self.editor_welcome = EditorWelcome(self)
        self.editor_stack.addWidget(self.editor_welcome)
        
        editor_panel = QWidget()
        editor_layout = QVBoxLayout(editor_panel)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        
        editor_title_layout = QHBoxLayout()
        editor_title = QLabel("EDITOR")
        editor_title.setObjectName("panelTitle")
        
        # RUN/STOP buttons call the same slots as the keyboard shortcuts so the two stay consistent.
        run_btn = QPushButton("RUN CODE")
        run_btn.setObjectName("runBtn")
        run_btn.clicked.connect(self.run_code)
        
        stop_btn = QPushButton("STOP CODE")
        stop_btn.setStyleSheet("background-color: #d13a3a; border: 1px solid #c92c2c;")
        stop_btn.clicked.connect(self.stop_code)
        
        editor_title_layout.addWidget(editor_title)
        editor_title_layout.addStretch()
        editor_title_layout.addWidget(stop_btn)
        editor_title_layout.addWidget(run_btn)
        
        # EditorArea is a splitter of tab widgets (split view); currentChanged keeps the status bar and macro panel in sync with the focused tab.
        self.tabs = EditorArea(self)
        self.tabs.currentChanged.connect(self.on_tab_changed)
        
        editor_layout.addLayout(editor_title_layout)
        editor_layout.addWidget(self.tabs)
        
        self.editor_stack.addWidget(editor_panel)
        right_splitter.addWidget(self.editor_stack)
        
        # Bottom area: three tabs sharing one panel so toggle_console can hide them all together.
        # Bottom half: Console & Terminal Tabs
        self.console_panel = QWidget()
        self.console_panel.setMinimumHeight(120)
        console_layout = QVBoxLayout(self.console_panel)
        console_layout.setContentsMargins(0, 0, 0, 0)
        
        self.console_tabs = QTabWidget()
        self.console_tabs.setTabBar(QTabBar())
        
        # Output console is read-only and plain text; append_console is the only writer, and it colours errors pink.
        # 1. Output Console
        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.console.setAcceptRichText(False)
        self.console.setObjectName("OutputConsole")
        self.console.setGraphicsEffect(create_shadow())
        self.console_tabs.addTab(self.console, "OUTPUT CONSOLE")
        
        # Terminal gets the current workspace path (None at start); _set_current_workspace updates it later.
        # 2. Interactive Terminal
        from app.ui.terminal import TerminalWidget
        self.terminal = TerminalWidget(workspace_path=self.current_workspace_path)
        self.console_tabs.addTab(self.terminal, "TERMINAL")
        
        # Problems panel lists linter diagnostics from every open editor; double clicking a row calls navigate_to_problem(file, line).
        # 3. Problems Panel
        from app.ui.problems import ProblemsWidget
        self.problems_widget = ProblemsWidget(self)
        self.problems_widget.row_double_clicked.connect(self.navigate_to_problem)
        self.console_tabs.addTab(self.problems_widget, "PROBLEMS")
        
        console_layout.addWidget(self.console_tabs)
        right_splitter.addWidget(self.console_panel)
        
        # Far-right macro panel: it scans the open script for tweakable numeric variables and emits param_changed so code can be re-run live while audio plays.
        # 4. Right Sidebar: Macro Panel
        from app.ui.macro_panel import MacroPanel
        self.macro_panel = MacroPanel(self)
        self.macro_panel.param_changed.connect(self.on_macro_changed)
        main_splitter.addWidget(self.macro_panel)
        
        # Initial splitter sizes in pixels: 220 sidebar, 860 editor area, 220 macro panel; and 600/200 for editor vs console. Qt rescales them to the real window size.
        main_splitter.setSizes([220, 860, 220])
        right_splitter.setSizes([600, 200])
        
        # Start on the welcome pages; they are replaced when a folder or file is opened.
        self.sidebar_stack.setCurrentIndex(0)
        self.editor_stack.setCurrentIndex(0)
        
        # Status bar widgets. Cursor position, tab size, encoding and language are static or updated through update_status_bar; the git label is shown only inside a git repository.
        # STATUS BAR
        self.lbl_cursor_pos = QLabel("Ln 1, Col 1")
        self.lbl_cursor_pos.setStyleSheet("color: #858585; font-size: 11px; font-weight: bold;")
        self.lbl_tab_size = QLabel("Tab Size: 4")
        self.lbl_tab_size.setStyleSheet("color: #858585; font-size: 11px; font-weight: bold;")
        self.lbl_file_encoding = QLabel("UTF-8")
        self.lbl_file_encoding.setStyleSheet("color: #858585; font-size: 11px; font-weight: bold;")
        self.lbl_language = QLabel("Python")
        self.lbl_language.setStyleSheet("color: #858585; font-size: 11px; font-weight: bold;")
        self.lbl_git_branch = QLabel("")
        self.lbl_git_branch.setStyleSheet("color: #6c5ce7; font-size: 11px; font-weight: bold;")
        self.lbl_play_status = QLabel("⏸ STOPPED")
        self.lbl_play_status.setStyleSheet("color: #858585; font-size: 11px; font-weight: bold;")

        # Hand-built bar (not QStatusBar) so it can live inside the IDE page only; the object name is a stylesheet hook.
        status_bar = QWidget()
        status_bar.setObjectName("EditorStatusBar")
        status_bar.setFixedHeight(22)
        status_layout = QHBoxLayout(status_bar)
        status_layout.setContentsMargins(15, 0, 15, 0)
        status_layout.setSpacing(15)
        
        status_layout.addWidget(self.lbl_language)
        status_layout.addWidget(self.lbl_git_branch)
        status_layout.addStretch()
        status_layout.addWidget(self.lbl_play_status)
        status_layout.addWidget(self.lbl_tab_size)
        status_layout.addWidget(self.lbl_file_encoding)
        status_layout.addWidget(self.lbl_cursor_pos)
        
        main_layout.addWidget(status_bar)
        
        self.update_git_branch_status()
        
        return ide_widget

    def build_daw_mode(self) -> QWidget:
        """Construct the DAW page (app/ui/daw.py).

        Imported inside the function so merely importing application.py does not load the audio stack.
        Wires theme_changed to the global recolour and asset_dropped to the asset router.
        """
        from app.ui.daw import DawWorkspace
        daw = DawWorkspace()
        daw.theme_changed.connect(self.apply_global_theme)
        daw.asset_dropped.connect(self._route_asset_activation)
        if self.current_workspace_path:
            daw.set_workspace_path(self.current_workspace_path)
        return daw

    def build_engine_mode(self) -> QWidget:
        """Construct the engine page plus the dock panels that belong to it."""
        from app.ui.engine import EngineWorkspace
        from app.ui.themes import PanelManager

        engine = EngineWorkspace()
        if self.engine_panel_manager is None:
            self.engine_panel_manager = PanelManager(self)
            # The engine widget supplies factories for its panels so the manager can create them on demand; the three default docks are Scene Tree (left),
            # Properties (right) and Console (bottom). Created only once, even if the engine page were rebuilt.
            for panel_name, factory in engine.dock_panel_factories().items():
                self.engine_panel_manager.register_panel(panel_name, factory)
            self.engine_panel_manager.create_panel("Scene Tree", Qt.DockWidgetArea.LeftDockWidgetArea)
            self.engine_panel_manager.create_panel("Properties", Qt.DockWidgetArea.RightDockWidgetArea)
            self.engine_panel_manager.create_panel("Console", Qt.DockWidgetArea.BottomDockWidgetArea)
        # The page is about to be shown, so the docks must be visible (they are hidden again when another mode is selected).
        # Panels start visible since we're switching to Engine mode
        self._sync_engine_panels(True)
        return engine

    def build_video_editor_mode(self) -> QWidget:
        """Construct the video editor page and forward its dropped assets to the asset router."""
        from app.ui.studio.video_timeline import VideoEditorWidget
        widget = VideoEditorWidget()
        widget.asset_dropped.connect(self._route_asset_activation)
        return widget

    def build_photo_editor_mode(self) -> QWidget:
        """Construct the photo editor page and forward its dropped assets to the asset router."""
        from app.ui.studio.photo_editor import PhotoEditorPanel
        widget = PhotoEditorPanel()
        widget.asset_dropped.connect(self._route_asset_activation)
        return widget

    def build_asset_manager_mode(self) -> QWidget:
        """Construct the asset manager page.

        The root is the opened workspace if there is one, otherwise the bundled sample folder so the page is never empty on a fresh install.
        """
        from app.ui.assets.asset_manager import AssetManagerWidget

        # os.path.dirname(__file__) is app/, so this resolves to app/resources/samples regardless of the current working directory.
        root = self.explorer.workspace_path or os.path.join(os.path.dirname(__file__), "resources", "samples")
        widget = AssetManagerWidget(root)
        widget.asset_activated.connect(self._route_asset_activation)
        return widget

    def _route_asset_activation(self, relative_path: str) -> None:
        """Open a double-clicked asset in the editor that matches its type.

        Args:
            relative_path: asset path relative to the asset manager's root folder.

        SecurityError from any target widget is reported in the console instead of crashing. Unknown types just print a notice.
        """
        from app.ui.assets.asset_manager import kind_for_relative_path
        from app.ui.assets.catalog import AssetKind

        kind = kind_for_relative_path(relative_path)
        # asset_manager_root can be None if the signal came from a DAW/studio drop before the asset manager was ever built, which is why images additionally require it.
        asset_manager_root = self.asset_manager_widget.model.root_path if self.asset_manager_widget else None

        if kind == AssetKind.IMAGE and asset_manager_root:
            # Build the target mode first so the import has a live widget; only then switch to it, so a blocked import leaves the user where they were.
            self._lazy_build_mode(4, "photo_editor_widget", self.build_photo_editor_mode)
            full_path = os.path.join(asset_manager_root, relative_path)
            try:
                self.photo_editor_widget.import_image_as_layer(full_path)
            except SecurityError as exc:
                self.append_console(f"Image import blocked: {exc}", error=True)
                return
            self.switch_mode(4)
            self.append_console(f"> Imported {relative_path} into Photo Editor as a new layer.")
        elif kind == AssetKind.VIDEO:
            self._lazy_build_mode(3, "video_editor_widget", self.build_video_editor_mode)
            try:
                self.video_editor_widget.import_clip(relative_path)
            except SecurityError as exc:
                self.append_console(f"Video import blocked: {exc}", error=True)
                return
            self.switch_mode(3)
            self.append_console(f"> Imported {relative_path} onto the Video Editor timeline.")
        elif kind == AssetKind.AUDIO:
            self._lazy_build_mode(1, "daw_workspace", self.build_daw_mode)
            try:
                self.daw_workspace.add_track_with_sample(relative_path)
            except SecurityError as exc:
                # The message admits a current limitation: the DAW can load only bundled samples, so audio from the user's own workspace is refused here.
                self.append_console(
                    f"Audio import blocked: {exc} Only samples bundled under "
                    "app/resources/samples can be loaded into the DAW today -- "
                    "loading arbitrary asset-workspace WAVs is a documented future task.",
                    error=True,
                )
                return
            self.switch_mode(1)
            self.append_console(f"> Loaded {relative_path} into a new DAW track.")
        else:
            self.append_console(f"No editor is wired up yet for this asset type: {relative_path}")

    def apply_global_theme(self, accent_color: str):
        """Recolour the whole app from one accent colour by rewriting the base stylesheet.

        Args:
            accent_color: any colour string QColor understands (e.g. "#ff007f"); connected to the DAW's theme_changed signal.

        Does nothing if ui/style.qss is missing. The stylesheet is re-read from disk on every call so edits to the file take effect without a restart.
        """
        style_path = os.path.join(os.path.dirname(__file__), 'ui', 'style.qss')
        if not os.path.exists(style_path):
            return
            
        # The original hexadecimal values below are the dark-grey palette the stylesheet was written with; replacing them by string search is cheap
        # but means any stylesheet colour that happens to equal one of them is also recoloured.
        with open(style_path, "r") as f:
            base_qss = f.read()
            
        # Redundant re-import (QColor is already imported at the top of the module); harmless.
        from PySide6.QtGui import QColor
        accent = QColor(accent_color)
        hue = accent.hue()
        # QColor.hue() returns -1 for greys (no hue); use 0 so fromHsv still works.
        if hue == -1: hue = 0
        
        # HSV lightness steps (value 10%, 14%, 18%, 25% with 15-20% saturation) keep the original relative contrast between background, editor, panel and border,
        # just tinted with the accent hue so the whole UI shifts colour with the DAW theme.
        # Base lightness levels from the original #1e1e1e theme
        # We tint the greys with the accent hue to recolor the entire IDE and DAW
        bg_main = QColor.fromHsv(hue, int(255 * 0.15), int(255 * 0.10)).name()    # Originally #1e1e1e
        bg_editor = QColor.fromHsv(hue, int(255 * 0.15), int(255 * 0.14)).name()  # Originally #252526
        bg_panel = QColor.fromHsv(hue, int(255 * 0.15), int(255 * 0.18)).name()   # Originally #2d2d2d
        border = QColor.fromHsv(hue, int(255 * 0.20), int(255 * 0.25)).name()     # Originally #3e3e42
        
        themed_qss = base_qss.replace("#1e1e1e", bg_main)
        themed_qss = themed_qss.replace("#252526", bg_editor)
        themed_qss = themed_qss.replace("#2d2d2d", bg_panel)
        themed_qss = themed_qss.replace("#3e3e42", border)
        # The editor's original accent blue (#007acc) is replaced by the exact colour the user picked.
        themed_qss = themed_qss.replace("#007acc", accent_color)
        
        self.setStyleSheet(themed_qss)

    # Everything below is the behaviour layer: tabs, status text, playback and script running.
    # --- BEHAVIOR ---
    def open_new_tab(self, filename: str, content: str = "", full_path: str = None):
        """Show a file in an editor tab, reusing an already open tab for the same file name.

        Args:
            filename: tab label; also the de-duplication key (two different folders with the same file name share one tab).
            content: initial text.
            full_path: absolute path if the file exists on disk; otherwise a path is derived from the workspace folder.
        """
        # Switch from the welcome page to the tab area before anything else, so the new tab is visible.
        self.editor_stack.setCurrentIndex(1)
        # Same-name tab already open: focus it instead of opening a duplicate.
        res = self.tabs.find_tab_by_filename(filename)
        if res:
            tab_widget, idx = res
            tab_widget.setCurrentIndex(idx)
            self.tabs.set_active_tab_widget(tab_widget)
            return
        editor = CodeEditor()
        editor.workspace_path = self.current_workspace_path
        # Prefer the real path; otherwise build one inside the workspace so the git diff and lint features still have a location.
        editor.file_path = full_path or (os.path.join(self.current_workspace_path, filename) if self.current_workspace_path else filename)
        editor.setPlainText(content)
        editor.cursor_position_changed.connect(self.update_status_bar)
        
        # ed=editor binds the loop variable now (default-argument trick) so each editor reports its own diagnostics.
        # Connect diagnostics linter callback
        editor.diagnostics_updated.connect(lambda diags, ed=editor: self.update_problems_list(ed, diags))
        
        # update_git_diff_cache and run_lint populate the gutter markers and the Problems list as soon as the tab appears instead of waiting for the first edit.
        idx = self.tabs.addTab(editor, filename)
        self.tabs.setCurrentIndex(idx)
        editor.update_git_diff_cache()
        editor.run_lint()
        
        # Live macro scanning: any edit re-reads the numeric variables so the macro sliders follow the code. The hasattr guard covers the brief time
        # before the macro panel is built.
        # Connect text changes to update parameters in macro panel
        editor.textChanged.connect(lambda: self.macro_panel.update_params_from_code(editor.toPlainText()) if hasattr(self, 'macro_panel') else None)
        if hasattr(self, 'macro_panel'):
            self.macro_panel.update_params_from_code(content)


    def close_tab(self, index):
        """Close the editor tab at `index` and fall back to the welcome page when none remain."""
        self.tabs.removeTab(index)
        if self.tabs.count() == 0:
            self.editor_stack.setCurrentIndex(0)

    def on_tab_changed(self, index):
        """Refresh the status bar and macro panel for the newly selected tab.

        Args:
            index: new tab index (-1 when no tab).
        """
        widget = self.tabs.widget(index)
        if isinstance(widget, CodeEditor):
            cursor = widget.textCursor()
            line = cursor.blockNumber() + 1
            col = cursor.columnNumber() + 1
            self.update_status_bar(line, col)
            if hasattr(self, 'macro_panel'):
                self.macro_panel.update_params_from_code(widget.toPlainText())
        # Non-editor widget (or no tab): reset to Ln 1, Col 1 and clear the macro sliders so they do not show stale values.
        else:
            self.update_status_bar(1, 1)
            if hasattr(self, 'macro_panel'):
                self.macro_panel.clear_panel()

    def update_status_bar(self, line: int, col: int):
        """Update the cursor label.

        Args:
            line: 1-based line number.
            col: 1-based column number.
        """
        if hasattr(self, 'lbl_cursor_pos'):
            self.lbl_cursor_pos.setText(f"Ln {line}, Col {col}")

    def on_file_double_clicked(self, index):
        """Open the file that was double-clicked in the explorer (folders are ignored; the tree handles expanding them)."""
        path = self.explorer.model.filePath(index)
        if os.path.isfile(path):
            try:
                content = read_text_file_safely(path)
            except SecurityError as exc:
                self.append_console(f"Open blocked: {exc}", error=True)
                return
            self.open_new_tab(os.path.basename(path), content, path)

    def load_stylesheet(self):
        """Load ui/style.qss as the initial window theme if the file exists."""
        style_path = os.path.join(os.path.dirname(__file__), 'ui', 'style.qss')
        if os.path.exists(style_path):
            with open(style_path, "r") as f:
                self.setStyleSheet(f.read())
        
    def toggle_playback(self):
        """Toggle the global audio button, its status label and the player.

        Only the player stops when switching off; starting playback just changes the display, because sound actually plays when scripts call play_sample_by_name.
        """
        if self.is_playing:
            self.play_btn.setText("START GLOBAL AUDIO")
            self.play_btn.setStyleSheet("")
            self.is_playing = False
            if self.player is not None:
                self.player.stop()
            # The status label may not exist before build_ide_mode has run; the guard avoids an AttributeError during early construction.
            if hasattr(self, 'lbl_play_status'):
                self.lbl_play_status.setText("⏸ STOPPED")
                self.lbl_play_status.setStyleSheet("color: #858585; font-size: 11px; font-weight: bold;")
        else:
            self.play_btn.setText("STOP GLOBAL AUDIO")
            self.play_btn.setStyleSheet("background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ff007f, stop:1 #8a2be2); color: white; border: none;")
            self.is_playing = True
            if hasattr(self, 'lbl_play_status'):
                self.lbl_play_status.setText("▶ PLAYING")
                self.lbl_play_status.setStyleSheet("color: #00f0ff; font-size: 11px; font-weight: bold;")
            
    def run_code(self):
        """Run the current editor's text through the safe script interpreter.

        Prints an error when no file is open. Macro values (if any) are substituted into the source first. Any SecurityError from
        app/security.py is shown in the console; nothing is executed outside the whitelist (play_sample / print).
        """
        # Index 0 of the editor stack is the welcome page, i.e. there is no tab to run.
        if self.editor_stack.currentIndex() == 0:
            self.append_console("No file open to execute.", error=True)
            return
            
        current_widget = self.tabs.currentWidget()
        if isinstance(current_widget, CodeEditor):
            code = current_widget.toPlainText()
            
            # Live modulation: replace each macro variable name with its current value's repr before parsing, so slider changes affect the script without editing the file.
            # Preprocess code with macro values to simulate live variable modulation safely!
            if hasattr(self, 'active_macro_values') and self.active_macro_values:
                # Local import keeps the regex module out of the start-up path; the substitution is a plain text replacement (see known limits in the header).
                import re
                for name, val in self.active_macro_values.items():
                    literal_repr = repr(val)
                    code = re.sub(rf"\b{name}\b", literal_repr, code)
                    
            self.append_console("> Running safe audio script...")
            try:
                # Callbacks hand the interpreter two capabilities and nothing else: play a bundled sample, and print to our console.
                played = run_safe_script(code, self.play_sample_by_name, lambda msg: self.append_console(msg))
            except SecurityError as exc:
                self.append_console(f"Execution blocked: {exc}", error=True)
                return
            self.append_console(f"> Played {len(played)} sample command(s).")

    def stop_code(self):
        """Placeholder for the STOP button/shortcut; explains why nothing can be stopped."""
        self.append_console("No external script process is available in secure mode.", error=True)

    def play_sample_by_name(self, name):
        """Play a bundled WAV sample by name (callback used by run_safe_script).

        Args:
            name: sample file name as written in the user's script; validated against the bundled sample folder.

        An invalid name is silently ignored here because the security module has already reported or rejected it.
        """
        # Samples are only taken from app/resources/samples; resolve_sample_path rejects traversal (../) and non-WAV names.
        samples_dir = os.path.join(os.path.dirname(__file__), "resources", "samples")
        try:
            file_path = resolve_sample_path(samples_dir, name)
        except SecurityError:
            return
        # Create the player lazily so users who never play sound do not initialise an audio device at start-up.
        self.ensure_audio_player()
        self.player.setSource(QUrl.fromLocalFile(file_path))
        self.player.play()

    def ensure_audio_player(self):
        """Create the QMediaPlayer and its QAudioOutput on first use."""
        if self.player is None:
            # Qt 6 needs an explicit QAudioOutput attached to the player; without it the player is silent.
            self.player = QMediaPlayer()
            self.audio_output = QAudioOutput()
            self.player.setAudioOutput(self.audio_output)

    def append_console(self, message: str, error: bool = False):
        """Append a line to the output console.

        Args:
            message: text to show.
            error: True draws the line in pink (#ff007f) instead of the normal grey.
        """
        # Always write at the end so the user's own scroll position or click in the read-only box cannot make new text land in the middle.
        self.console.moveCursor(QTextCursor.MoveOperation.End)
        self.console.setTextColor(QColor("#ff007f" if error else "#a9b7c6"))
        self.console.insertPlainText(f"{message}\n")
        self.console.moveCursor(QTextCursor.MoveOperation.End)

    def toggle_sidebar(self):
        """Show or hide the explorer sidebar (default Ctrl+B)."""
        if hasattr(self, 'sidebar_stack'):
            self.sidebar_stack.setVisible(not self.sidebar_stack.isVisible())

    def toggle_console(self):
        """Show or hide the console/terminal/problems area (default Ctrl+`)."""
        if hasattr(self, 'console_panel'):
            self.console_panel.setVisible(not self.console_panel.isVisible())

    def update_git_branch_status(self):
        """Refresh the branch name in the status bar from the current workspace.

        Hides the label when there is no workspace, no repository or git is unavailable.
        """
        if not hasattr(self, 'lbl_git_branch'):
            return
        if self.current_workspace_path:
            # Imported lazily: app/ide/git.py shells out to the git program, which is only worth touching once a workspace exists.
            from app.ide.git import git_get_branch
            branch = git_get_branch(self.current_workspace_path)
            if branch:
                self.lbl_git_branch.setText(f"🌿 {branch}")
                self.lbl_git_branch.show()
                return
        self.lbl_git_branch.hide()

    def navigate_to_problem(self, file_path: str, line_number: int):
        """Open a file and put the cursor on a given line (used by the Problems and Search panels).

        Args:
            file_path: path of the file; silently ignored if empty or missing.
            line_number: 1-based line to jump to; out-of-range lines leave the cursor where it was.
        """
        if not file_path or not os.path.exists(file_path):
            return
        filename = os.path.basename(file_path)
        try:
            content = read_text_file_safely(file_path)
        except SecurityError as exc:
            self.append_console(f"Open blocked: {exc}", error=True)
            return
        self.open_new_tab(filename, content, file_path)
        
        editor = self.tabs.currentWidget()
        if isinstance(editor, CodeEditor):
            # QTextDocument blocks are lines; subtracting 1 converts from the 1-based display number to the 0-based block index.
            doc = editor.document()
            block = doc.findBlockByLineNumber(line_number - 1)
            if block.isValid():
                cursor = editor.textCursor()
                cursor.setPosition(block.position())
                editor.setTextCursor(cursor)
                editor.setFocus()

    def update_problems_list(self, editor, diagnostics):
        """Store one editor's diagnostics and refresh the combined Problems list.

        Args:
            editor: the CodeEditor that produced the diagnostics (used as the dict key).
            diagnostics: list of problem records from that editor's linter.
        """
        # Created lazily on first call; keyed by editor so a re-lint replaces only that editor's old entries.
        if not hasattr(self, 'problems_by_editor'):
            self.problems_by_editor = {}
        self.problems_by_editor[editor] = diagnostics
        
        # The Problems panel shows one flat list across all open files.
        combined = []
        for diags in self.problems_by_editor.values():
            combined.extend(diags)
            
        if hasattr(self, 'problems_widget'):
            self.problems_widget.update_problems(combined)

    def on_macro_changed(self, params):
        """Remember the latest macro slider values and re-run the script live when audio is playing.

        Args:
            params: dict of macro variable name -> current value, emitted by MacroPanel.param_changed.
        """
        self.active_macro_values = params
        if self.is_playing:
            self.run_code()
            
def create_app(args: list[str]) -> tuple[QApplication, QMainWindow]:
    """Create the Qt application object, initialise the database and build the main window.

    Args:
        args: command-line arguments, normally sys.argv.

    Returns:
        (QApplication, MainWindow); the caller (app/main.py) shows the window and starts the event loop.
    """
    app = QApplication(args)
    # Application name is used by Qt for the settings location and window grouping; "NeonDAW" is the project's earlier name.
    app.setApplicationName("NeonDAW")
    # Create tables before any widget could query the database.
    init_db()
    window = MainWindow()
    return app, window
