from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeVar, cast

from app.security import SecurityError


@dataclass
class Transform:
    """Position, rotation, and scale for an entity."""

    x: float = 0.0
    y: float = 0.0
    rotation: float = 0.0
    scale_x: float = 1.0
    scale_y: float = 1.0


@dataclass
class Sprite:
    """Renderable primitive metadata for an entity."""

    color: str = "#FFFFFF"
    shape: str = "rectangle"
    width: float = 50.0
    height: float = 50.0


@dataclass
class RigidBody:
    """Velocity and mass data for physics simulation."""

    velocity_x: float = 0.0
    velocity_y: float = 0.0
    mass: float = 1.0
    is_static: bool = False
    restitution: float = 0.25
    friction: float = 0.0
    damping: float = 0.0
    gravity_scale: float = 1.0
    collision_layer: int = 0


@dataclass
class Collider:
    """Axis-aligned collision bounds for an entity."""

    width: float = 50.0
    height: float = 50.0
    is_trigger: bool = False


class Component(Protocol):
    """Marker protocol for ECS component instances."""


T = TypeVar("T")


class World:
    """Registry for entities and their typed components."""

    def __init__(self) -> None:
        self._next_entity_id = 1
        self._entities: set[int] = set()
        self._components: dict[type[object], dict[int, object]] = {}

    def create_entity(self) -> int:
        """Create and return a unique entity id."""
        entity_id = self._next_entity_id
        self._next_entity_id += 1
        self._entities.add(entity_id)
        return entity_id

    def add_component(self, entity_id: int, component: Component) -> None:
        """Attach a component instance to an entity."""
        self._require_entity(entity_id)
        component_type = type(component)
        self._components.setdefault(component_type, {})[entity_id] = component

    def get_component(self, entity_id: int, component_type: type[T]) -> T:
        """Retrieve a component by entity and type."""
        self._require_entity(entity_id)
        try:
            return cast(T, self._components[component_type][entity_id])
        except KeyError as exc:
            raise SecurityError("Entity does not have requested component.") from exc

    def has_component(self, entity_id: int, component_type: type[object]) -> bool:
        """Return whether an entity has a component of a given type."""
        return entity_id in self._entities and entity_id in self._components.get(component_type, {})

    def get_entities_with(self, *component_types: type[object]) -> list[int]:
        """Return entities that have all requested component types."""
        if not component_types:
            return sorted(self._entities)
        matches = set(self._entities)
        for component_type in component_types:
            matches &= set(self._components.get(component_type, {}))
        return sorted(matches)

    def remove_entity(self, entity_id: int) -> bool:
        """Delete an entity and all of its components."""
        if entity_id not in self._entities:
            return False
        self._entities.remove(entity_id)
        for component_map in self._components.values():
            component_map.pop(entity_id, None)
        return True

    def entity_exists(self, entity_id: int) -> bool:
        """Return whether an entity is alive in the world."""
        return entity_id in self._entities

    def _require_entity(self, entity_id: int) -> None:
        if entity_id not in self._entities:
            raise SecurityError(f"Unknown entity id: {entity_id}")


class System:
    """Base class for systems that process ECS world state."""

    def process(self, world: World, dt: float) -> None:
        """Process a simulation step."""
        raise NotImplementedError
