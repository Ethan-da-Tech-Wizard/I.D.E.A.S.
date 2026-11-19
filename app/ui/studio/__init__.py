# ==============================================================================
# Program: app/ui/studio/__init__.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: package marker for the "studio" creative tools: photo_editor.py (layered image editor)
# and video_timeline.py (multi-track timeline). The modules are imported directly by
# app/application.py when the matching mode is opened; nothing is re-exported here so importing the
# package stays cheap and does not pull in Qt.
# ==============================================================================

"""Video editor, photo editor, and studio tools."""
from __future__ import annotations
