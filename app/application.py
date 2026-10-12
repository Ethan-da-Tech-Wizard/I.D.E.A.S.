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

def create_shadow():
    shadow = QGraphicsDropShadowEffect()
    shadow.setBlurRadius(20)
    shadow.setColor(QColor(0, 0, 0, 150))
    shadow.setOffset(0, 4)
    return shadow

class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("I.D.E.A.S. - IDE, DAW, Engines, AI, Studio")
        self.resize(1300, 850)
        
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

        self.p2p_manager = None
        self.emulator_process = None

        self.setup_menu()
        self.setup_ui()
        self.load_stylesheet()

        # Toggle Layout Shortcuts
        self.sidebar_toggle_act = QAction(self)
        self.sidebar_toggle_act.setShortcut("Ctrl+B")
        self.sidebar_toggle_act.triggered.connect(self.toggle_sidebar)
        self.addAction(self.sidebar_toggle_act)

        self.console_toggle_act = QAction(self)
        self.console_toggle_act.setShortcut("Ctrl+`")
        self.console_toggle_act.triggered.connect(self.toggle_console)
        self.addAction(self.console_toggle_act)

        # Run/Stop Code Actions
        self.run_code_act = QAction(self)
        self.run_code_act.triggered.connect(self.run_code)
        self.addAction(self.run_code_act)

        self.stop_code_act = QAction(self)
        self.stop_code_act.triggered.connect(self.stop_code)
        self.addAction(self.stop_code_act)

        # Initialize and apply keybindings
        from app.ide.keybindings import KeybindingsEngine
        self.keybindings_engine = KeybindingsEngine(self)
        self.keybindings_engine.load_and_apply()

    def ensure_p2p_manager(self, peer_id: str, display_name: str, port: int = 5001) -> 'NetworkManager':
        """Lazy-initializes and returns the NetworkManager instance."""
        if self.p2p_manager is None:
            from app.network.p2p import NetworkManager, PeerInfo
            local_peer = PeerInfo("127.0.0.1", port, peer_id, display_name)
            self.p2p_manager = NetworkManager(local_peer)
        return self.p2p_manager

    def ensure_emulator_process(self, workspace_path: str) -> 'EmulatorProcess':
        """Lazy-initializes and returns the EmulatorProcess instance."""
        if self.emulator_process is None:
            from app.services.emulators import EmulatorProcess
            self.emulator_process = EmulatorProcess(workspace_path)
        return self.emulator_process
        
    def setup_menu(self):
        menubar = self.menuBar()
        file_menu = menubar.addMenu("File")
        
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
        
        self.save_project_act = QAction("Save Workspace...", self)
        self.save_project_act.setShortcut("Ctrl+S")
        self.save_project_act.triggered.connect(self.save_project)
        file_menu.addAction(self.save_project_act)
        
        load_project_action = QAction("Load Workspace...", self)
        load_project_action.setShortcut("Ctrl+O")
        load_project_action.triggered.connect(self.load_project)
        file_menu.addAction(load_project_action)
        
    def new_file(self):
        self.open_new_tab("Untitled", "")
        
    def open_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Open File", QDir.homePath())
        if file_path:
            try:
                content = read_text_file_safely(file_path)
            except SecurityError as exc:
                self.append_console(f"Open blocked: {exc}", error=True)
                return
            self.open_new_tab(os.path.basename(file_path), content, file_path)

    def _set_current_workspace(self, folder_path: str) -> None:
        """Tracks the active project workspace so features scoped to it (like
        DAW custom sample import, Task F1) know where to read/write."""
        self.current_workspace_path = folder_path
        if self.daw_workspace is not None:
            self.daw_workspace.set_workspace_path(folder_path)
        if hasattr(self, 'terminal') and self.terminal:
            self.terminal.workspace_path = folder_path
            self.terminal.locals["WORKSPACE"] = folder_path
        if hasattr(self, 'search_panel') and self.search_panel:
            self.search_panel.set_workspace(folder_path)
        self.update_git_branch_status()

    def open_folder(self):
        folder_path = QFileDialog.getExistingDirectory(self, "Open Workspace Folder", QDir.homePath())
        if folder_path:
            self.explorer.set_workspace(folder_path)
            self.sidebar_stack.setCurrentIndex(1)
            if self.asset_manager_widget is not None:
                self.asset_manager_widget.set_root(folder_path)
            self._set_current_workspace(folder_path)
            self.append_console(f"> Workspace changed to: {folder_path}")

    def save_project(self):
        folder_path = QFileDialog.getExistingDirectory(self, "Save Workspace", QDir.homePath())
        if not folder_path:
            return

        project_state: dict[str, Any] = {}

        # Save IDE State
        current_widget = self.tabs.currentWidget()
        if isinstance(current_widget, CodeEditor):
            project_state["ide_content"] = current_widget.toPlainText()
            project_state["ide_filename"] = self.tabs.tabText(self.tabs.currentIndex())

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
        folder_path = QFileDialog.getExistingDirectory(self, "Load Workspace", QDir.homePath())
        if not folder_path:
            return
            
        try:
            project_state = load_workspace(folder_path)
        except SecurityError as exc:
            self.append_console(f"Workspace load blocked: {exc}", error=True)
            return
            
        # Restore IDE
        if "ide_content" in project_state:
            filename = project_state.get("ide_filename", "main.py")
            self.open_new_tab(filename, project_state["ide_content"])
            
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
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # Base layout: Activity Bar + Top-Level Mode Stack
        base_layout = QHBoxLayout(central_widget)
        base_layout.setContentsMargins(0, 0, 0, 0)
        base_layout.setSpacing(0)
        
        # --- ACTIVITY BAR ---
        activity_bar = QWidget()
        activity_bar.setObjectName("ActivityBar")
        activity_bar.setFixedWidth(70)
        activity_layout = QVBoxLayout(activity_bar)
        activity_layout.setContentsMargins(0, 20, 0, 20)
        activity_layout.setSpacing(20)
        activity_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        
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

        activity_layout.addWidget(self.btn_ide_mode)
        activity_layout.addWidget(self.btn_daw_mode)
        activity_layout.addWidget(self.btn_engine_mode)
        activity_layout.addWidget(self.btn_video_mode)
        activity_layout.addWidget(self.btn_photo_mode)
        activity_layout.addWidget(self.btn_assets_mode)
        activity_bar.setGraphicsEffect(create_shadow())

        base_layout.addWidget(activity_bar)

        # --- TOP LEVEL STACK (IDE vs DAW vs Studio) ---
        self.mode_stack = QStackedWidget()
        base_layout.addWidget(self.mode_stack, 1)

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
        if getattr(self, attr_name) is None:
            placeholder = self.mode_stack.widget(index)
            widget = builder()
            setattr(self, attr_name, widget)
            self.mode_stack.removeWidget(placeholder)
            placeholder.deleteLater()
            self.mode_stack.insertWidget(index, widget)

    def switch_mode(self, index: int):
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

        # Show/hide engine dock panels based on active mode
        self._sync_engine_panels(index == 2)

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
        """Hide or show engine dock panels so they don't overlap other modes."""
        if self.engine_panel_manager is None:
            return
        for name in self.engine_panel_manager.active_panels():
            panel = self.engine_panel_manager._panels.get(name)
            if panel is not None:
                panel.setVisible(show)

    def build_ide_mode(self) -> QWidget:
        ide_widget = QWidget()
        main_layout = QVBoxLayout(ide_widget)
        main_layout.setContentsMargins(20, 20, 20, 10)
        main_layout.setSpacing(15)
        
        # IDE HEADER
        header_layout = QHBoxLayout()
        title_label = QLabel("NEON DAW : IDE")
        title_label.setFont(QFont("Arial", 24, QFont.Weight.Bold))
        title_label.setStyleSheet("color: #00f0ff; letter-spacing: 2px;")
        
        self.play_btn = QPushButton("START GLOBAL AUDIO")
        self.play_btn.clicked.connect(self.toggle_playback)
        self.play_btn.setGraphicsEffect(create_shadow())
        
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        header_layout.addWidget(self.play_btn)
        main_layout.addLayout(header_layout)
        
        # SPLITTER
        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(main_splitter, 1)
        
        # LEFT PANEL (Explorer)
        self.sidebar_stack = QStackedWidget()
        self.sidebar_stack.setMinimumWidth(220)
        self.sidebar_stack.setGraphicsEffect(create_shadow())
        
        self.sidebar_welcome = SidebarWelcome(self)
        self.sidebar_stack.addWidget(self.sidebar_welcome)
        
        explorer_panel = QWidget()
        explorer_layout = QVBoxLayout(explorer_panel)
        explorer_layout.setContentsMargins(0, 0, 0, 0)
        
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
        
        self.explorer = FileExplorer()
        self.explorer.doubleClicked.connect(self.on_file_double_clicked)
        explorer_layout.addLayout(explorer_title_layout)
        explorer_layout.addWidget(self.explorer)
        
        self.sidebar_tabs = QTabWidget()
        self.sidebar_tabs.setTabBar(QTabBar())
        self.sidebar_tabs.addTab(explorer_panel, "EXPLORER")
        
        from app.ui.search_panel import SearchPanel
        self.search_panel = SearchPanel(self)
        self.search_panel.result_selected.connect(self.navigate_to_problem)
        self.sidebar_tabs.addTab(self.search_panel, "SEARCH")
        
        self.sidebar_stack.addWidget(self.sidebar_tabs)
        main_splitter.addWidget(self.sidebar_stack)
        
        # RIGHT PANEL (Editor / Console)
        right_splitter = QSplitter(Qt.Orientation.Vertical)
        main_splitter.addWidget(right_splitter)
        
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
        
        self.tabs = EditorArea(self)
        self.tabs.currentChanged.connect(self.on_tab_changed)
        
        editor_layout.addLayout(editor_title_layout)
        editor_layout.addWidget(self.tabs)
        
        self.editor_stack.addWidget(editor_panel)
        right_splitter.addWidget(self.editor_stack)
        
        # Bottom half: Console & Terminal Tabs
        self.console_panel = QWidget()
        self.console_panel.setMinimumHeight(120)
        console_layout = QVBoxLayout(self.console_panel)
        console_layout.setContentsMargins(0, 0, 0, 0)
        
        self.console_tabs = QTabWidget()
        self.console_tabs.setTabBar(QTabBar())
        
        # 1. Output Console
        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.console.setAcceptRichText(False)
        self.console.setObjectName("OutputConsole")
        self.console.setGraphicsEffect(create_shadow())
        self.console_tabs.addTab(self.console, "OUTPUT CONSOLE")
        
        # 2. Interactive Terminal
        from app.ui.terminal import TerminalWidget
        self.terminal = TerminalWidget(workspace_path=self.current_workspace_path)
        self.console_tabs.addTab(self.terminal, "TERMINAL")
        
        # 3. Problems Panel
        from app.ui.problems import ProblemsWidget
        self.problems_widget = ProblemsWidget(self)
        self.problems_widget.row_double_clicked.connect(self.navigate_to_problem)
        self.console_tabs.addTab(self.problems_widget, "PROBLEMS")
        
        console_layout.addWidget(self.console_tabs)
        right_splitter.addWidget(self.console_panel)
        
        # 4. Right Sidebar: Macro Panel
        from app.ui.macro_panel import MacroPanel
        self.macro_panel = MacroPanel(self)
        self.macro_panel.param_changed.connect(self.on_macro_changed)
        main_splitter.addWidget(self.macro_panel)
        
        main_splitter.setSizes([220, 860, 220])
        right_splitter.setSizes([600, 200])
        
        self.sidebar_stack.setCurrentIndex(0)
        self.editor_stack.setCurrentIndex(0)
        
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
        from app.ui.daw import DawWorkspace
        daw = DawWorkspace()
        daw.theme_changed.connect(self.apply_global_theme)
        daw.asset_dropped.connect(self._route_asset_activation)
        if self.current_workspace_path:
            daw.set_workspace_path(self.current_workspace_path)
        return daw

    def build_engine_mode(self) -> QWidget:
        from app.ui.engine import EngineWorkspace
        from app.ui.themes import PanelManager

        engine = EngineWorkspace()
        if self.engine_panel_manager is None:
            self.engine_panel_manager = PanelManager(self)
            for panel_name, factory in engine.dock_panel_factories().items():
                self.engine_panel_manager.register_panel(panel_name, factory)
            self.engine_panel_manager.create_panel("Scene Tree", Qt.DockWidgetArea.LeftDockWidgetArea)
            self.engine_panel_manager.create_panel("Properties", Qt.DockWidgetArea.RightDockWidgetArea)
            self.engine_panel_manager.create_panel("Console", Qt.DockWidgetArea.BottomDockWidgetArea)
        # Panels start visible since we're switching to Engine mode
        self._sync_engine_panels(True)
        return engine

    def build_video_editor_mode(self) -> QWidget:
        from app.ui.studio.video_timeline import VideoEditorWidget
        widget = VideoEditorWidget()
        widget.asset_dropped.connect(self._route_asset_activation)
        return widget

    def build_photo_editor_mode(self) -> QWidget:
        from app.ui.studio.photo_editor import PhotoEditorPanel
        widget = PhotoEditorPanel()
        widget.asset_dropped.connect(self._route_asset_activation)
        return widget

    def build_asset_manager_mode(self) -> QWidget:
        from app.ui.assets.asset_manager import AssetManagerWidget

        root = self.explorer.workspace_path or os.path.join(os.path.dirname(__file__), "resources", "samples")
        widget = AssetManagerWidget(root)
        widget.asset_activated.connect(self._route_asset_activation)
        return widget

    def _route_asset_activation(self, relative_path: str) -> None:
        """Routes a double-clicked asset-manager entry to the right editor,
        based on file kind. Each import path is validated by the target
        widget itself (SecurityError surfaces here as a console warning
        instead of crashing)."""
        from app.ui.assets.asset_manager import kind_for_relative_path
        from app.ui.assets.catalog import AssetKind

        kind = kind_for_relative_path(relative_path)
        asset_manager_root = self.asset_manager_widget.model.root_path if self.asset_manager_widget else None

        if kind == AssetKind.IMAGE and asset_manager_root:
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
        style_path = os.path.join(os.path.dirname(__file__), 'ui', 'style.qss')
        if not os.path.exists(style_path):
            return
            
        with open(style_path, "r") as f:
            base_qss = f.read()
            
        from PySide6.QtGui import QColor
        accent = QColor(accent_color)
        hue = accent.hue()
        if hue == -1: hue = 0
        
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
        themed_qss = themed_qss.replace("#007acc", accent_color)
        
        self.setStyleSheet(themed_qss)

    # --- BEHAVIOR ---
    def open_new_tab(self, filename: str, content: str = "", full_path: str = None):
        self.editor_stack.setCurrentIndex(1)
        res = self.tabs.find_tab_by_filename(filename)
        if res:
            tab_widget, idx = res
            tab_widget.setCurrentIndex(idx)
            self.tabs.set_active_tab_widget(tab_widget)
            return
        editor = CodeEditor()
        editor.workspace_path = self.current_workspace_path
        editor.file_path = full_path or (os.path.join(self.current_workspace_path, filename) if self.current_workspace_path else filename)
        editor.setPlainText(content)
        editor.cursor_position_changed.connect(self.update_status_bar)
        
        # Connect diagnostics linter callback
        editor.diagnostics_updated.connect(lambda diags, ed=editor: self.update_problems_list(ed, diags))
        
        idx = self.tabs.addTab(editor, filename)
        self.tabs.setCurrentIndex(idx)
        editor.update_git_diff_cache()
        editor.run_lint()
        
        # Connect text changes to update parameters in macro panel
        editor.textChanged.connect(lambda: self.macro_panel.update_params_from_code(editor.toPlainText()) if hasattr(self, 'macro_panel') else None)
        if hasattr(self, 'macro_panel'):
            self.macro_panel.update_params_from_code(content)


    def close_tab(self, index):
        self.tabs.removeTab(index)
        if self.tabs.count() == 0:
            self.editor_stack.setCurrentIndex(0)

    def on_tab_changed(self, index):
        widget = self.tabs.widget(index)
        if isinstance(widget, CodeEditor):
            cursor = widget.textCursor()
            line = cursor.blockNumber() + 1
            col = cursor.columnNumber() + 1
            self.update_status_bar(line, col)
            if hasattr(self, 'macro_panel'):
                self.macro_panel.update_params_from_code(widget.toPlainText())
        else:
            self.update_status_bar(1, 1)
            if hasattr(self, 'macro_panel'):
                self.macro_panel.clear_panel()

    def update_status_bar(self, line: int, col: int):
        if hasattr(self, 'lbl_cursor_pos'):
            self.lbl_cursor_pos.setText(f"Ln {line}, Col {col}")

    def on_file_double_clicked(self, index):
        path = self.explorer.model.filePath(index)
        if os.path.isfile(path):
            try:
                content = read_text_file_safely(path)
            except SecurityError as exc:
                self.append_console(f"Open blocked: {exc}", error=True)
                return
            self.open_new_tab(os.path.basename(path), content, path)

    def load_stylesheet(self):
        style_path = os.path.join(os.path.dirname(__file__), 'ui', 'style.qss')
        if os.path.exists(style_path):
            with open(style_path, "r") as f:
                self.setStyleSheet(f.read())
        
    def toggle_playback(self):
        if self.is_playing:
            self.play_btn.setText("START GLOBAL AUDIO")
            self.play_btn.setStyleSheet("")
            self.is_playing = False
            if self.player is not None:
                self.player.stop()
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
        if self.editor_stack.currentIndex() == 0:
            self.append_console("No file open to execute.", error=True)
            return
            
        current_widget = self.tabs.currentWidget()
        if isinstance(current_widget, CodeEditor):
            code = current_widget.toPlainText()
            
            # Preprocess code with macro values to simulate live variable modulation safely!
            if hasattr(self, 'active_macro_values') and self.active_macro_values:
                import re
                for name, val in self.active_macro_values.items():
                    literal_repr = repr(val)
                    code = re.sub(rf"\b{name}\b", literal_repr, code)
                    
            self.append_console("> Running safe audio script...")
            try:
                played = run_safe_script(code, self.play_sample_by_name, lambda msg: self.append_console(msg))
            except SecurityError as exc:
                self.append_console(f"Execution blocked: {exc}", error=True)
                return
            self.append_console(f"> Played {len(played)} sample command(s).")

    def stop_code(self):
        self.append_console("No external script process is available in secure mode.", error=True)

    def play_sample_by_name(self, name):
        samples_dir = os.path.join(os.path.dirname(__file__), "resources", "samples")
        try:
            file_path = resolve_sample_path(samples_dir, name)
        except SecurityError:
            return
        self.ensure_audio_player()
        self.player.setSource(QUrl.fromLocalFile(file_path))
        self.player.play()

    def ensure_audio_player(self):
        if self.player is None:
            self.player = QMediaPlayer()
            self.audio_output = QAudioOutput()
            self.player.setAudioOutput(self.audio_output)

    def append_console(self, message: str, error: bool = False):
        self.console.moveCursor(QTextCursor.MoveOperation.End)
        self.console.setTextColor(QColor("#ff007f" if error else "#a9b7c6"))
        self.console.insertPlainText(f"{message}\n")
        self.console.moveCursor(QTextCursor.MoveOperation.End)

    def toggle_sidebar(self):
        if hasattr(self, 'sidebar_stack'):
            self.sidebar_stack.setVisible(not self.sidebar_stack.isVisible())

    def toggle_console(self):
        if hasattr(self, 'console_panel'):
            self.console_panel.setVisible(not self.console_panel.isVisible())

    def update_git_branch_status(self):
        if not hasattr(self, 'lbl_git_branch'):
            return
        if self.current_workspace_path:
            from app.ide.git import git_get_branch
            branch = git_get_branch(self.current_workspace_path)
            if branch:
                self.lbl_git_branch.setText(f"🌿 {branch}")
                self.lbl_git_branch.show()
                return
        self.lbl_git_branch.hide()

    def navigate_to_problem(self, file_path: str, line_number: int):
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
            doc = editor.document()
            block = doc.findBlockByLineNumber(line_number - 1)
            if block.isValid():
                cursor = editor.textCursor()
                cursor.setPosition(block.position())
                editor.setTextCursor(cursor)
                editor.setFocus()

    def update_problems_list(self, editor, diagnostics):
        if not hasattr(self, 'problems_by_editor'):
            self.problems_by_editor = {}
        self.problems_by_editor[editor] = diagnostics
        
        combined = []
        for diags in self.problems_by_editor.values():
            combined.extend(diags)
            
        if hasattr(self, 'problems_widget'):
            self.problems_widget.update_problems(combined)

    def on_macro_changed(self, params):
        self.active_macro_values = params
        if self.is_playing:
            self.run_code()
            
def create_app(args: list[str]) -> tuple[QApplication, QMainWindow]:
    app = QApplication(args)
    app.setApplicationName("NeonDAW")
    init_db()
    window = MainWindow()
    return app, window
