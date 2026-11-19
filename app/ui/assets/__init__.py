# ==============================================================================
# Program: __init__.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: Package initialization marker establishing the namespace and public exports for the assets module.
# ==============================================================================
# How this module fits: package marker for the Asset Manager UI. It re-exports the four catalog names so other modules can write
# `from app.ui.assets import AssetCatalog` without knowing the file layout. The widget itself (asset_manager.py) is deliberately NOT imported here, so
# importing this package (for example from tests) does not pull in Qt.

from app.ui.assets.catalog import (
    AssetCatalog,
    AssetEntry,
    AssetKind,
    build_asset_catalog,
)

__all__ = [
    "AssetCatalog",
    "AssetEntry",
    "AssetKind",
    "build_asset_catalog",
]
