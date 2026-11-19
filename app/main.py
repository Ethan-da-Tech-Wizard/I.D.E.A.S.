# ==============================================================================
# Program: main.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: Primary application launcher for the I.D.E.A.S. integrated creative suite. Handles fatal startup exception recovery, initializes the Qt QApplication lifecycle, and presents the main application window.
# ==============================================================================

# How this module fits: the program's entry point (it is also the script the PyInstaller release build packages, see the Windows workflow; scripts/launch-linux.sh starts the built binary, not this file).
# create_app (app/application.py) builds the Qt application and main window; if the settings store cannot be opened (StorageError) the user sees one error dialog instead of a traceback.
# Run from source: python -m app.main (needs PySide6).
import sys
from PySide6.QtWidgets import QApplication, QMessageBox
from app.application import create_app
from app.storage.database import StorageError

def main() -> None:
    """Create the application and enter the Qt event loop; exit the process with its return code.

    A StorageError (for example an unwritable or locked settings database) is shown in a message box and ends the program with status 1. A QApplication is created first
    if create_app failed before making one, because Qt widgets such as QMessageBox need it.
    """
    try:
        app, window = create_app(sys.argv)
    except StorageError as exc:
        app = QApplication.instance() or QApplication(sys.argv)
        QMessageBox.critical(None, "NeonDAW Startup Blocked", str(exc))
        sys.exit(1)

    # create_app returns the main window hidden; it is shown only after construction succeeded.
    window.show()

    # app.exec() runs the event loop until the last window closes; its return value becomes the process exit status.
    sys.exit(app.exec())

# Only when run as a script (the packaged executable also enters here), not when imported by tests.
if __name__ == "__main__":
    main()
