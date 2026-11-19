# ==============================================================================
# Program: app/ui/welcome.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: the two "nothing open yet" screens of the IDE mode: SidebarWelcome (replaces the file
# explorer until a folder is opened) and EditorWelcome (centre screen with the NeonDAW title and
# Start links). Both only forward clicks to MainWindow slots; they hold no state of their own.
# Used by: app/application.py (creates them in the sidebar/editor stacks and swaps them for the
# explorer/editor once a folder or file is open).
# Calls into MainWindow: open_folder, new_file, open_file (these must exist on main_window).
# Styling: inline stylesheet strings use the same palette as the dark theme (#858585 grey text,
# #007acc accent blue, #1177bb hover); object name "panelTitle" is styled by the global theme.
# ==============================================================================

from PySide6.QtWidgets import QWidget, QVBoxLayout, QPushButton, QLabel, QHBoxLayout, QFrame
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

class SidebarWelcome(QWidget):
    """Sidebar placeholder shown while no workspace folder is open, with one "Open Folder" button."""
    def __init__(self, main_window):
        """Args:
            main_window: the MainWindow whose open_folder slot the button triggers.
        """
        super().__init__()
        # Keep a reference only to call its slots; no signals are re-emitted here.
        self.main_window = main_window
        self.setup_ui()
        
    def setup_ui(self):
        """Build the vertical layout: title, explanation and the Open Folder button."""
        # Top-aligned column with padding (left 15, top 25, right 15, bottom 15) to line up with other sidebar panels.
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.setSpacing(15)
        layout.setContentsMargins(15, 25, 15, 15)
        
        # objectName "panelTitle" lets the global stylesheet give it the same look as other panel headers.
        title = QLabel("NO FOLDER OPENED")
        title.setObjectName("panelTitle")
        layout.addWidget(title)
        
        info = QLabel("You have not yet opened a folder.")
        # Wrap so the text fits a narrow sidebar instead of widening it.
        info.setWordWrap(True)
        info.setStyleSheet("color: #858585; margin-bottom: 10px;")
        layout.addWidget(info)
        
        btn_open_folder = QPushButton("Open Folder")
        # Direct connection to the main window slot; the button does nothing else.
        btn_open_folder.clicked.connect(self.main_window.open_folder)
        
        layout.addWidget(btn_open_folder)
        # Pushes content to the top.
        layout.addStretch()

class EditorWelcome(QWidget):
    """Centre-screen start page: product name, subtitle and links to create or open files."""
    def __init__(self, main_window):
        """Args:
            main_window: the MainWindow providing new_file, open_file and open_folder slots.
        """
        super().__init__()
        self.main_window = main_window
        self.setup_ui()
        
    def setup_ui(self):
        """Assemble the logo, subtitle and the "Start" link group, centred with stretch above and below."""
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Generous gaps give the page a calm launch-screen look.
        layout.setSpacing(40)
        
        # 42 pt thin weight uses the platform UI font; letter-spacing is in the stylesheet because QFont has no tracking in this form.
        # Sleek Logo
        logo = QLabel("NeonDAW")
        logo.setFont(QFont("-apple-system", 42, QFont.Weight.Thin))
        logo.setStyleSheet("color: #ffffff; letter-spacing: 4px;")
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        subtitle = QLabel("The Professional IDE & DAW")
        subtitle.setFont(QFont("-apple-system", 14))
        subtitle.setStyleSheet("color: #858585;")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # Links are stacked in their own layout so they can be centred as a group.
        # Start Section
        start_layout = QVBoxLayout()
        start_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        start_layout.setSpacing(12)
        
        start_label = QLabel("Start")
        start_label.setObjectName("panelTitle")
        start_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        start_layout.addWidget(start_label)
        
        # Each link receives the MainWindow callback directly.
        btn_new_file = self.create_link_button("New File...", self.main_window.new_file)
        btn_open_file = self.create_link_button("Open File...", self.main_window.open_file)
        btn_open_folder = self.create_link_button("Open Folder...", self.main_window.open_folder)
        start_layout.addWidget(btn_new_file)
        start_layout.addWidget(btn_open_file)
        start_layout.addWidget(btn_open_folder)
        
        # First stretch: pushes the block towards vertical centre.
        layout.addStretch()
        layout.addWidget(logo)
        layout.addWidget(subtitle)
        layout.addLayout(start_layout)
        # Second stretch balances the first so the block sits in the middle.
        layout.addStretch()

    def create_link_button(self, text, callback):
        """Create a flat, text-only button that looks like a hyperlink.

        Args:
            text: caption.
            callback: slot connected to clicked.

        Returns:
            The configured QPushButton (caller adds it to a layout).
        """
        btn = QPushButton(text)
        # Transparent background, blue text, underline on hover: the VS Code-like start link look.
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
        # A pointing hand signals clickability since the button has no frame.
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(callback)
        return btn
