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
