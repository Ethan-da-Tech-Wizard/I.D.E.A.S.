# IDE Vision Specification: Next-Generation Script Editor

This document outlines the master design, structural specifications, and integration requirements for the IDE mode in NeonDAW. It serves as a complete, bulletproof layout playbook for any developer or agent starting on the team with zero context.

---

## 🏁 Phase 1 Approved Specifications (Status: YES)
*   **Collapsible Sidebars & Bottom Panels**: Toggleable sidebar (Explorer, Settings) and bottom panels (Console) with keyboard shortcuts (`Ctrl+B`, ``Ctrl+```) and collapsing icon tab-bars.
*   **Git Diff Gutters**: Color-coded gutter lines next to line numbers to indicate additions (green), edits (yellow), and deletions (red).
*   **Split View**: Horizontal and vertical split-screen capabilities to support side-by-side editing.
*   **IntelliSense Autocomplete**: Interactive PySide6-native popup window displaying matching functions, keywords, and call signatures.
*   **Real-time AST Linting**: Live syntax checking with red wavy underlines and descriptive error tooltips on hover.
*   **Theme Configurator**: Color customizer UI that lets users edit syntax, caret, selection, and panel colors, exporting them to style QSS files.
*   **Custom Keybindings**: Custom keyboard shortcuts loaded from a JSON configuration file (`keybindings.json`).
*   **Interactive Macro Runner**: Parsed comment declarations (e.g. `# @param cutoff = 150 [20, 2000]`) that auto-render sliders/dials in a side panel.
*   **Live Modulation**: Variable values updated in memory dynamically, allowing scripts to respond to slider changes in real-time.

---

## 🏛️ Phase 2 Deep Architecture & Design Questionnaire
*(All 29 responses have been locked in and consolidated below).*

---

## 🗺️ Codebase Map & Feature Specifications

Below is the mapping of where these features reside (or will be created) in the codebase, along with the defined **Beginning State** and **End Goal** for each component.

### 1. Window Panels & Split Screen
*   **File Locations**: 
    *   [app/application.py](file:///home/ethan/I.D./id_app/app/application.py) — Main layout, QStackedWidget tabs, status bar setup.
    *   [app/ui/editor.py](file:///home/ethan/I.D./id_app/app/ui/editor.py) — Custom `QTabWidget` and `CodeEditor` instances.
*   **Beginning State**: A single horizontal QSplitter separating a static Left Panel (Explorer stack) and a Right Panel (tabbed code editor and console). Tabs cannot be dragged to split; they are locked to one QTabWidget. Panels can be resized but can be squished to zero size.
*   **End Goal**: A nested QSplitter layout where dragging a tab to the right/left/bottom of the text area automatically instantiates a new QTabWidget split group (split-screen). Both Left Panel and Console support keyboard shortcuts (`Ctrl+B`, ``Ctrl+```) to toggle visibility (collapsing/hiding completely). All splits have rigid `minimumWidth` (sidebar: 220px, editor: 400px, console: 120px) preventing accidental squishing.

### 2. Status Bar & Terminal
*   **File Locations**:
    *   [app/application.py](file:///home/ethan/I.D./id_app/app/application.py) — Status bar widget layout and event slots.
    *   `app/ui/terminal.py` **[NEW]** — Interactive terminal widget subclasses.
*   **Beginning State**: The IDE has a header title "NEON DAW: IDE" and a running status line, but no persistent status bar at the bottom. Console is read-only.
*   **End Goal**: A thin, custom-themed `#EditorStatusBar` at the very bottom containing active labels: line/col (updates on editor cursor shift), tab size (e.g., `Tab Size: 4`), active parser (`Python`), active Git branch name, and a visual play indicator showing the DAW sequence playback status. The bottom pane contains a toggleable "Terminal" tab running a read-write interactive Python shell limited to workspace directories.

### 3. Typography, Editor Mechanics, & Gutters
*   **File Locations**:
    *   [app/ui/editor.py](file:///home/ethan/I.D./id_app/app/ui/editor.py) — CodeEditor line painter, mouse wheel zoom, and delimiter auto-close.
    *   [app/ui/style.qss](file:///home/ethan/I.D./id_app/app/ui/style.qss) — Custom selection, carets, and editor styles.
*   **Beginning State**: A basic QPlainTextEdit subclass with default line numbers. Font is Monospace, zoom is static, braces do not auto-close, and no indent guides or Git line statuses exist.
*   **End Goal**: Upgraded `CodeEditor` supporting:
    *   Subtle line highlight background tint (`#6c5ce7` at 8% opacity).
    *   `Ctrl + Mouse Scroll` dynamically scaling font size.
    *   Smart auto-closing brackets: typing `(` prints `)` and puts cursor inside; typing `)` manually steps the cursor through it instead of duplicating.
    *   Dotted indentation lines indicating nesting blocks.
    *   Line number area paints green/yellow/red indicators reflecting Git diff status.
    *   Glowing cyan carets (`caret-color: #00f0ff;`) and semi-transparent cyan selection highlights.

### 4. Syntax Highlighting & Diagnostics
*   **File Locations**:
    *   [app/ui/highlighter.py](file:///home/ethan/I.D./id_app/app/ui/highlighter.py) — Python AST syntax coloring rules.
    *   `app/ui/problems.py` **[NEW]** — Problems list tab widget.
*   **Beginning State**: Standard regex-based syntax highlighter coloring keywords (pink), built-ins (cyan), strings (yellow), and comments (grey). AST check runs only on manual execute clicks.
*   **End Goal**: Enhanced `PythonHighlighter` that parses and colors delimiters, math/logic operators, decorators, number literals, and class self/cls keywords with dedicated neon theme colors. AST diagnostics run automatically on a **300ms delay** after the last keypress, drawing red wavy underlines under syntax issues. Active errors are aggregated into a table in the bottom "Problems" tab, where double-clicking a row jumps to the error line.

### 5. Autocomplete & API IntelliSense
*   **File Locations**:
    *   `app/ui/autocomplete.py` **[NEW]** — Dropdown menu list widget and workspace indexing scanner.
*   **Beginning State**: Autocomplete is absent. Users must remember the API names to write scripts.
*   **End Goal**: Popup list widget that triggers automatically as you type letters or dot operators. It scans and indices built-in audio/graphics commands along with custom classes/methods defined inside the workspace. Selecting an item shows a documentation panel displaying docstrings, parameter signatures, and snippet templates.

### 6. Keybindings Configuration Engine
*   **File Locations**:
    *   `app/ide/keybindings.py` **[NEW]** — Shortcuts parser and trigger controller.
    *   `keybindings.json` **[NEW]** — Hotkey JSON mapping.
*   **Beginning State**: Standard PySide6 QAction shortcuts bound in Python code, which cannot be adjusted by the user.
*   **End Goal**: Keybindings are parsed from a local `keybindings.json` file. If a custom user shortcut collides with a standard default (like copy/paste), the system shortcut is overridden, and a visual warnings dialog notifies the user. Contains built-in layout presets (VS Code, Sublime, Emacs presets).

### 7. Interactive Macro Panel (Visual Sliders)
*   **File Locations**:
    *   `app/ui/macro_panel.py` **[NEW]** — Control panel widget containing sliders, checkboxes, dropdowns, and spinboxes.
*   **Beginning State**: Executing code runs scripts statically. Variables inside the code must be manually edited and re-run to change output.
*   **End Goal**: A right-hand "Macro Panel" containing slider controls dynamically parsed from comments (e.g. `# @param volume = 0.8 [0.0, 1.0]`). Dragging controls sends updates throttled at **50ms** intervals to variables in memory, modulating the execution parameters dynamically (e.g., changing sound pitch or play rate live).

### 8. File Explorer, Search, & filesystem Watchers
*   **File Locations**:
    *   [app/ui/explorer.py](file:///home/ethan/I.D./id_app/app/ui/explorer.py) — Directory tree view, folder context menus.
    *   `app/ui/search_panel.py` **[NEW]** — Sidebar search-in-files view.
*   **Beginning State**: Basic QTreeView listing files when a directory is chosen. No search, no filesystem watcher, and no right-click file operations.
*   **End Goal**: File Explorer integrated with a background filesystem watcher that auto-refreshes the tree when files change externally. Supported right-click context menu options (New File/Folder, Delete, Rename, Reveal in OS). Sidebar includes a "Search in Files" text box that searches all files inside the workspace (excluding `.git/`, `.venv/`, `__pycache__/`) and lists matching rows with text previews.
