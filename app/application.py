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
from app.ui.editor import CodeEditor
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

        self.setup_menu()
        self.setup_ui()
        self.load_stylesheet()
        
    def setup_menu(self):
        menubar = self.menuBar()
        file_menu = menubar.addMenu("File")
        
        new_file_action = QAction("New File...", self)
        new_file_action.triggered.connect(self.new_file)
        file_menu.addAction(new_file_action)
        
        open_file_action = QAction("Open File...", self)
        open_file_action.triggered.connect(self.open_file)
        file_menu.addAction(open_file_action)
        
        open_folder_action = QAction("Open Folder...", self)
        open_folder_action.triggered.connect(self.open_folder)
        file_menu.addAction(open_folder_action)
        
        file_menu.addSeparator()
        
        save_project_action = QAction("Save Workspace...", self)
        save_project_action.setShortcut("Ctrl+S")
        save_project_action.triggered.connect(self.save_project)
        file_menu.addAction(save_project_action)
        
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
            self.open_new_tab(os.path.basename(file_path), content)

    def open_folder(self):
        folder_path = QFileDialog.getExistingDirectory(self, "Open Workspace Folder", QDir.homePath())
        if folder_path:
            self.explorer.set_workspace(folder_path)
            self.sidebar_stack.setCurrentIndex(1)
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
            
        try:
            saved_path = save_workspace(folder_path, project_state)
        except SecurityError as exc:
            self.append_console(f"Workspace save blocked: {exc}", error=True)
            return

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
        
        activity_layout.addWidget(self.btn_ide_mode)
        activity_layout.addWidget(self.btn_daw_mode)
        activity_bar.setGraphicsEffect(create_shadow())
        
        base_layout.addWidget(activity_bar)
        
        # --- TOP LEVEL STACK (IDE vs DAW) ---
        self.mode_stack = QStackedWidget()
        base_layout.addWidget(self.mode_stack, 1)
        
        # 1. Build IDE Mode
        self.mode_stack.addWidget(self.build_ide_mode())
        
        # 2. DAW mode is built lazily so audio backends cannot block IDE startup.
        self.mode_stack.addWidget(QWidget())
        
    def switch_mode(self, index: int):
        if index == 1 and self.daw_workspace is None:
            placeholder = self.mode_stack.widget(1)
            self.daw_workspace = self.build_daw_mode()
            self.mode_stack.removeWidget(placeholder)
            placeholder.deleteLater()
            self.mode_stack.insertWidget(1, self.daw_workspace)
        self.mode_stack.setCurrentIndex(index)
        
        # Update active styling on activity buttons
        self.btn_ide_mode.setProperty("active", "true" if index == 0 else "false")
        self.btn_daw_mode.setProperty("active", "true" if index == 1 else "false")
        
        # Force re-evaluation of QSS
        self.btn_ide_mode.style().unpolish(self.btn_ide_mode)
        self.btn_ide_mode.style().polish(self.btn_ide_mode)
        self.btn_daw_mode.style().unpolish(self.btn_daw_mode)
        self.btn_daw_mode.style().polish(self.btn_daw_mode)

    def build_ide_mode(self) -> QWidget:
        ide_widget = QWidget()
        main_layout = QVBoxLayout(ide_widget)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(20)
        
        # IDE HEADER
        header_layout = QHBoxLayout()
        title_label = QLabel("NEON DAW : IDE")
        title_label.setFont(QFont("Arial", 24, QFont.Weight.Bold))
        # We can't do gradient text natively easily in basic QSS without hacks, so we just color it
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
        self.sidebar_stack.setGraphicsEffect(create_shadow())
        
        self.sidebar_welcome = SidebarWelcome(self)
        self.sidebar_stack.addWidget(self.sidebar_welcome)
        
        explorer_panel = QWidget()
        explorer_layout = QVBoxLayout(explorer_panel)
        explorer_layout.setContentsMargins(0, 0, 0, 0)
        explorer_title = QLabel("EXPLORER")
        explorer_title.setObjectName("panelTitle")
        self.explorer = FileExplorer()
        self.explorer.doubleClicked.connect(self.on_file_double_clicked)
        explorer_layout.addWidget(explorer_title)
        explorer_layout.addWidget(self.explorer)
        
        self.sidebar_stack.addWidget(explorer_panel)
        main_splitter.addWidget(self.sidebar_stack)
        
        # RIGHT PANEL (Editor / Console)
        right_splitter = QSplitter(Qt.Orientation.Vertical)
        main_splitter.addWidget(right_splitter)
        
        # Top half: Tabs
        self.editor_stack = QStackedWidget()
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
        
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        
        editor_layout.addLayout(editor_title_layout)
        editor_layout.addWidget(self.tabs)
        
        self.editor_stack.addWidget(editor_panel)
        right_splitter.addWidget(self.editor_stack)
        
        # Bottom half: Console
        console_panel = QWidget()
        console_layout = QVBoxLayout(console_panel)
        console_layout.setContentsMargins(0, 0, 0, 0)
        
        console_title = QLabel("OUTPUT CONSOLE")
        console_title.setObjectName("panelTitle")
        
        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.console.setAcceptRichText(False)
        self.console.setObjectName("OutputConsole")
        self.console.setGraphicsEffect(create_shadow())
        
        console_layout.addWidget(console_title)
        console_layout.addWidget(self.console)
        right_splitter.addWidget(console_panel)
        
        main_splitter.setSizes([300, 900])
        right_splitter.setSizes([600, 200])
        
        self.sidebar_stack.setCurrentIndex(0)
        self.editor_stack.setCurrentIndex(0)
        
        return ide_widget

    def build_daw_mode(self) -> QWidget:
        from app.ui.daw import DawWorkspace
        daw = DawWorkspace()
        daw.theme_changed.connect(self.apply_global_theme)
        return daw

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
    def open_new_tab(self, filename: str, content: str = ""):
        self.editor_stack.setCurrentIndex(1)
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i) == filename:
                self.tabs.setCurrentIndex(i)
                return
        editor = CodeEditor()
        editor.setPlainText(content)
        idx = self.tabs.addTab(editor, filename)
        self.tabs.setCurrentIndex(idx)

    def close_tab(self, index):
        self.tabs.removeTab(index)
        if self.tabs.count() == 0:
            self.editor_stack.setCurrentIndex(0)

    def on_file_double_clicked(self, index):
        path = self.explorer.model.filePath(index)
        if os.path.isfile(path):
            try:
                content = read_text_file_safely(path)
            except SecurityError as exc:
                self.append_console(f"Open blocked: {exc}", error=True)
                return
            self.open_new_tab(os.path.basename(path), content)

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
        else:
            self.play_btn.setText("STOP GLOBAL AUDIO")
            self.play_btn.setStyleSheet("background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ff007f, stop:1 #8a2be2); color: white; border: none;")
            self.is_playing = True
            
    def run_code(self):
        if self.editor_stack.currentIndex() == 0:
            self.append_console("No file open to execute.", error=True)
            return
            
        current_widget = self.tabs.currentWidget()
        if isinstance(current_widget, CodeEditor):
            code = current_widget.toPlainText()
            self.append_console("> Running safe audio script...")
            try:
                played = run_safe_script(code, self.play_sample_by_name)
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
            
def create_app(args: list[str]) -> tuple[QApplication, QMainWindow]:
    app = QApplication(args)
    app.setApplicationName("NeonDAW")
    init_db()
    window = MainWindow()
    return app, window
