# ==============================================================================
# Program: __init__.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: Package initialization marker establishing the namespace and public exports for the app module.
# ==============================================================================

# Package marker (no code) so that `import app` and the relative layout of app/ work, and tools such as pytest and PyInstaller (see the release workflow, entry point app/main.py) can find the package.
# Sub-packages: ai (AI code gate), ide (compiler, git and key-binding helpers), models (reserved, currently empty), network (peer-to-peer), resources (bundled samples and styles),
# services (audio, export, emulator and orchestration back ends), storage (workspace files and the SQLite database), ui (Qt widgets), views (empty placeholder) and widgets (shared FX rack widget).
# Top-level modules: main.py (entry point), application.py (main window), project.py (project file format), security.py (all input-safety checks).
# App Module
