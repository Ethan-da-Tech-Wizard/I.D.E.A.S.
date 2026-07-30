# id_app Architecture

## Prohibited Technologies
- Electron, Tauri, Vite
- Node.js, npm, pnpm, yarn
- React, Vue, Svelte, Next.js, Nuxt
- HTML-based application shells, Browser-based user interfaces, WebAssembly frontends
- Embedded HTTP servers, localhost, development web servers, WebSocket servers
- TCP or UDP listening sockets
- Docker
- Qt WebEngine, QWebEngineView, Qt WebView, embedded Chromium, embedded browser controls
- Client-server architecture, internal REST APIs
- Database server (PostgreSQL, MySQL, MongoDB, Redis)

## Required Technologies
- Python 3.14.6
- PySide6
- Qt Widgets (QApplication, QMainWindow, etc.)
- Type hints
- pytest for non-GUI business-logic tests
- Local virtual environment
- SQLite via Python's `sqlite3` module for structured storage
- JSON only for simple configuration data
- `QStandardPaths.AppDataLocation` to locate the correct per-user data folder

## Rules
- The application must start without an internet connection.
- All application logic runs directly inside one desktop application process.
- Database operations must occur directly inside the desktop process.
- Include schema creation and safe migrations when SQLite is used.
