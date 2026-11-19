# ==============================================================================
# Program: app/ui/engine/ecs.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: a minimal Entity-Component-System, the data model under the Engine workspace. An ENTITY
# is just an integer id; COMPONENTS are small dataclasses (Transform, Sprite, RigidBody, Collider)
# attached to entities; SYSTEMS (such as PhysicsSystem) loop over entities that have the components
# they need. Keeping data and behaviour apart makes it easy to add features without a class
# hierarchy.
# Used by: physics.py (reads/writes Transform, RigidBody, Collider), workspace.py (mirrors scene
# nodes into entities), and tests/test_ecs.py.
# Design: components are stored per TYPE (type -> {entity id -> component}) so a query for "all
# entities with Transform and RigidBody" is a set intersection. Lookups on bad ids or missing
# components raise SecurityError (the project's single validation error type), not KeyError.
# Known limits: one component of a given type per entity; no events or component removal API
# (remove_entity deletes everything); entity ids are never reused.
# Units: positions in world pixels, y grows downward, velocities in pixels per second.
# ==============================================================================

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeVar, cast

from app.security import SecurityError


# Where an entity is: position (x, y), rotation and scale. Rotation is stored but not used by physics.
@dataclass
class Transform:
    """Position, rotation, and scale for an entity."""

    # Top-left corner of the entity's box in world pixels (not the centre).
    x: float = 0.0
    y: float = 0.0
    # Stored for the renderer; physics ignores it.
    rotation: float = 0.0
    scale_x: float = 1.0
    scale_y: float = 1.0


@dataclass
class Sprite:
    """Renderable primitive metadata for an entity."""

    # CSS-style hex colour string.
    color: str = "#FFFFFF"
    # One of the shapes the viewport can draw (see ALLOWED_SHAPES in viewport.py).
    shape: str = "rectangle"
    width: float = 50.0
    height: float = 50.0


# Physics state of a movable (or immovable) body. Fields: velocity in pixels/second, mass (relative weight in collisions), is_static (never moves, acts as a wall), restitution (bounciness 0..1), friction (horizontal slowdown on floor contact 0..1), damping (air drag per second), gravity_scale (multiplier on world gravity) and collision_layer (only equal layers collide).
@dataclass
class RigidBody:
    """Velocity and mass data for physics simulation."""

    velocity_x: float = 0.0
    velocity_y: float = 0.0
    mass: float = 1.0
    is_static: bool = False
    # Low default bounce so dropped objects settle quickly.
    restitution: float = 0.25
    friction: float = 0.0
    damping: float = 0.0
    gravity_scale: float = 1.0
    # Bodies collide only with bodies on the same layer value; 0 is the default shared layer.
    collision_layer: int = 0


# Axis-aligned box used for collision tests; is_trigger boxes are detected by nothing in physics (they are skipped).
@dataclass
class Collider:
    """Axis-aligned collision bounds for an entity."""

    width: float = 50.0
    height: float = 50.0
    is_trigger: bool = False


class Component(Protocol):
    """Marker protocol for ECS component instances."""


# Generic type so get_component(entity, Transform) is typed as returning a Transform.
T = TypeVar("T")


class World:
    """Registry for entities and their typed components."""

    def __init__(self) -> None:
        """Start with no entities. Entity ids begin at 1 (0 is never used)."""
        self._next_entity_id = 1
        # _entities: all live ids; _components: component type -> {entity id: instance}.
        self._entities: set[int] = set()
        self._components: dict[type[object], dict[int, object]] = {}

    # Allocate the next id; ids increase monotonically and are never reused, so stale ids cannot silently refer to a new entity.
    def create_entity(self) -> int:
        """Create and return a unique entity id."""
        entity_id = self._next_entity_id
        self._next_entity_id += 1
        self._entities.add(entity_id)
        return entity_id

    # Attach `component` to `entity_id`, replacing any component of the same type.
    #
    # Raises:
    #     SecurityError: unknown entity id.
    def add_component(self, entity_id: int, component: Component) -> None:
        """Attach a component instance to an entity."""
        self._require_entity(entity_id)
        # The concrete class is the lookup key, so subclasses are NOT found when querying the base class.
        component_type = type(component)
        self._components.setdefault(component_type, {})[entity_id] = component

    # Return the entity's component of `component_type`.
    #
    # Raises:
    #     SecurityError: unknown entity, or the entity has no such component.
    def get_component(self, entity_id: int, component_type: type[T]) -> T:
        """Retrieve a component by entity and type."""
        self._require_entity(entity_id)
        try:
            # cast only informs the type checker; no runtime conversion happens.
            return cast(T, self._components[component_type][entity_id])
        except KeyError as exc:
            raise SecurityError("Entity does not have requested component.") from exc

    # Return whether the (existing) entity has a component of the type; never raises.
    def has_component(self, entity_id: int, component_type: type[object]) -> bool:
        """Return whether an entity has a component of a given type."""
        return entity_id in self._entities and entity_id in self._components.get(component_type, {})

    # Return ids having ALL given component types, sorted ascending (stable, deterministic order for physics).
    #
    # Called with no types it returns every entity.
    def get_entities_with(self, *component_types: type[object]) -> list[int]:
        """Return entities that have all requested component types."""
        if not component_types:
            return sorted(self._entities)
        # Intersect the id sets of each requested type; a type nobody uses yields an empty result.
        matches = set(self._entities)
        for component_type in component_types:
            matches &= set(self._components.get(component_type, {}))
        return sorted(matches)

    # Delete an entity and every component it owns.
    #
    # Returns:
    #     True if it existed, False otherwise.
    def remove_entity(self, entity_id: int) -> bool:
        """Delete an entity and all of its components."""
        if entity_id not in self._entities:
            return False
        self._entities.remove(entity_id)
        for component_map in self._components.values():
            component_map.pop(entity_id, None)
        return True

    # Return whether the id is alive.
    def entity_exists(self, entity_id: int) -> bool:
        """Return whether an entity is alive in the world."""
        return entity_id in self._entities

    def _require_entity(self, entity_id: int) -> None:
        """Raise SecurityError for an unknown id so callers fail with a clear message."""
        if entity_id not in self._entities:
            raise SecurityError(f"Unknown entity id: {entity_id}")


class System:
    """Base class for systems that process ECS world state."""

    # Advance the world by `dt` seconds; subclasses must override. The base class raises NotImplementedError.
    def process(self, world: World, dt: float) -> None:
        """Process a simulation step."""
        raise NotImplementedError
