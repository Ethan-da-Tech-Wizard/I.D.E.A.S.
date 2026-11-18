# ==============================================================================
# Program: __init__.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: Package initialization marker establishing the namespace and public exports for the assets module.
# ==============================================================================

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
