# ==============================================================================
# Program: __init__.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: Package initialization marker establishing the namespace and public exports for the audio module.
# ==============================================================================

# Package marker for the real-time audio code: dsp.py (filters, effects, envelopes), voice.py (one played note), track_engine.py (a track's settings and processing chain),
# engine.py (output stream, step clock, master bus), midi_service.py (hardware input), plugin_bridge.py (plugin API stub) and sample_import.py (custom sample import).
# The docstring below only mentions MIDI and the plugin bridge because it was written when those were the first modules here; the package now holds all of the above.
"""MIDI services and plugin bridge."""
from __future__ import annotations
