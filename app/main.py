# ==============================================================================
# Program: main.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: Primary application launcher for the I.D.E.A.S. integrated creative suite. Handles fatal startup exception recovery, initializes the Qt QApplication lifecycle, and presents the main application window.
# ==============================================================================

import sys
from PySide6.QtWidgets import QApplication, QMessageBox
from app.application import create_app
from app.storage.database import StorageError

def main() -> None:
    try:
        app, window = create_app(sys.argv)
    except StorageError as exc:
        app = QApplication.instance() or QApplication(sys.argv)
        QMessageBox.critical(None, "NeonDAW Startup Blocked", str(exc))
        sys.exit(1)
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
