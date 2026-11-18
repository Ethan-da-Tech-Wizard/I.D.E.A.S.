# ==============================================================================
# Program: __init__.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: Package initialization marker establishing the namespace and public exports for the engine module.
# ==============================================================================

"""Engine viewport, ECS, physics, and shader builder."""

from app.ui.engine.ecs import Collider, RigidBody, Sprite, Transform, World
from app.ui.engine.panels import EnginePropertiesPanel, EngineSceneTreePanel
from app.ui.engine.viewport import SceneGraph, SceneNode, ViewportWidget
from app.ui.engine.workspace import EngineWorkspace

__all__ = [
    "Collider",
    "EngineWorkspace",
    "EnginePropertiesPanel",
    "EngineSceneTreePanel",
    "RigidBody",
    "SceneGraph",
    "SceneNode",
    "Sprite",
    "Transform",
    "ViewportWidget",
    "World",
]
