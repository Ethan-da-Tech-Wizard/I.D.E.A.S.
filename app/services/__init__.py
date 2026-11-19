# ==============================================================================
# Program: __init__.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: Package initialization marker establishing the namespace and public exports for the services module.
# ==============================================================================

# Package marker for back-end services that have no Qt widgets: audio (live engine, DSP, MIDI), offline sample effects (audio_fx.py), WAV export (audio_export.py), the legacy
# command-line sample player (audio_player.py), emulator control (emulators.py) and project-wide orchestration (orchestration.py). UI code calls into these modules; they never import UI code.
# Services Module
