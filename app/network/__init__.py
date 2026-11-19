# ==============================================================================
# Program: __init__.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: Package initialization marker establishing the namespace and public exports for the network module.
# ==============================================================================

# Package marker for the LAN collaboration code in p2p.py (discovery, sessions, hosting). Nothing is imported here on purpose: importing the package must not open sockets or start threads.
# app/network/__init__.py
"""Peer-to-peer networking for local multiplayer."""
from __future__ import annotations
