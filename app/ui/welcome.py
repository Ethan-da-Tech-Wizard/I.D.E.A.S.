from PySide6.QtWidgets import QWidget, QVBoxLayout, QPushButton, QLabel, QHBoxLayout, QFrame
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

class SidebarWelcome(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.setup_ui()
        
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.setSpacing(15)
        layout.setContentsMargins(15, 25, 15, 15)
        
        title = QLabel("NO FOLDER OPENED")
        title.setObjectName("panelTitle")
        layout.addWidget(title)
        
        info = QLabel("You have not yet opened a folder.")
        info.setWordWrap(True)
        info.setStyleSheet("color: #858585; margin-bottom: 10px;")
        layout.addWidget(info)
        
        btn_open_folder = QPushButton("Open Folder")
        btn_open_folder.clicked.connect(self.main_window.open_folder)
        
        layout.addWidget(btn_open_folder)
        layout.addStretch()

class EditorWelcome(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.setup_ui()
        
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(40)
        
        # Sleek Logo
        logo = QLabel("NeonDAW")
        logo.setFont(QFont("-apple-system", 42, QFont.Weight.Thin))
        logo.setStyleSheet("color: #ffffff; letter-spacing: 4px;")
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        subtitle = QLabel("The Professional IDE & DAW")
        subtitle.setFont(QFont("-apple-system", 14))
        subtitle.setStyleSheet("color: #858585;")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # Start Section
        start_layout = QVBoxLayout()
        start_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        start_layout.setSpacing(12)
        
        start_label = QLabel("Start")
        start_label.setObjectName("panelTitle")
        start_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        start_layout.addWidget(start_label)
        
        btn_new_file = self.create_link_button("New File...", self.main_window.new_file)
        btn_open_file = self.create_link_button("Open File...", self.main_window.open_file)
        btn_open_folder = self.create_link_button("Open Folder...", self.main_window.open_folder)
        start_layout.addWidget(btn_new_file)
        start_layout.addWidget(btn_open_file)
        start_layout.addWidget(btn_open_folder)
        
        layout.addStretch()
        layout.addWidget(logo)
        layout.addWidget(subtitle)
        layout.addLayout(start_layout)
        layout.addStretch()

    def create_link_button(self, text, callback):
        btn = QPushButton(text)
        btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                color: #007acc;
                font-size: 14px;
            }
            QPushButton:hover {
                color: #1177bb;
                text-decoration: underline;
            }
        """)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(callback)
        return btn
