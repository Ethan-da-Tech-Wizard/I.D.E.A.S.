# ==============================================================================
# Program: app/ui/engine/__init__.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: package entry point for the 2D game-engine workspace. It re-exports the classes other
# parts of the app need (EngineWorkspace for the Engine mode in app/application.py, the dock panels
# for the panel manager, and the ECS/scene types used by tests) so callers can write
# `from app.ui.engine import EngineWorkspace` instead of reaching into submodules.
# Layout: ecs.py (entities/components), physics.py (PhysicsSystem), viewport.py (scene graph +
# canvas), shader_builder.py (node graph), panels.py (scene tree / properties docks), history.py
# (undo), scene_io.py (.ideas-scene files), workspace.py (ties everything together).
# Import cost: importing this package imports PySide6 (viewport and panels are Qt widgets).
# ==============================================================================

"""Engine viewport, ECS, physics, and shader builder."""

from app.ui.engine.ecs import Collider, RigidBody, Sprite, Transform, World
from app.ui.engine.panels import EnginePropertiesPanel, EngineSceneTreePanel
from app.ui.engine.viewport import SceneGraph, SceneNode, ViewportWidget
from app.ui.engine.workspace import EngineWorkspace

# Public API of the package. Listing names here also keeps linters from reporting the re-imports as unused.
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
