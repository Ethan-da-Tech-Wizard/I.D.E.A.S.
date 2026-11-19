# ==============================================================================
# Program: tests/test_ecs.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: tests app/ui/engine/ecs.py, the minimal entity-component-system behind the game-engine
# workspace: a World that hands out entity ids, stores components by type, answers "which entities
# have all of these components" queries and runs Systems. No Qt is involved.
# Run: python3 -m pytest tests/test_ecs.py -q
# ==============================================================================

from __future__ import annotations

import pytest

from app.security import SecurityError
from app.ui.engine.ecs import Collider, RigidBody, Sprite, System, Transform, World


# Ids must be unique and listed in creation order, because systems iterate entities in that order.
def test_entity_creation_returns_unique_ids() -> None:
    world = World()

    first = world.create_entity()
    second = world.create_entity()

    assert first != second
    assert world.entity_exists(first) is True
    assert world.get_entities_with() == [first, second]


# Components are stored by reference (`is`): systems mutate them in place, so a copy would break updates.
def test_add_and_get_component() -> None:
    world = World()
    entity = world.create_entity()
    transform = Transform(x=4.0, y=8.0)

    world.add_component(entity, transform)

    assert world.get_component(entity, Transform) is transform
    assert world.has_component(entity, Transform) is True


# Errors are SecurityError (the module's single error type): adding to an unknown entity id (999)
# or reading a component the entity does not have.
def test_get_component_rejects_missing_entity_or_component() -> None:
    world = World()
    entity = world.create_entity()

    with pytest.raises(SecurityError):
        world.add_component(999, Transform())

    with pytest.raises(SecurityError):
        world.get_component(entity, Sprite)


# Queries return entities having ALL listed types, preserving creation order. `renderable` has
# Transform+Sprite; `physical` additionally has a body and collider.
def test_query_entities_with_multiple_component_types() -> None:
    world = World()
    renderable = world.create_entity()
    physical = world.create_entity()

    world.add_component(renderable, Transform())
    world.add_component(renderable, Sprite())
    world.add_component(physical, Transform())
    world.add_component(physical, Sprite())
    world.add_component(physical, RigidBody())
    world.add_component(physical, Collider())

    assert world.get_entities_with(Transform, Sprite) == [renderable, physical]
    assert world.get_entities_with(Transform, RigidBody, Collider) == [physical]


# Removing returns True once, False after; the entity's components disappear from queries too.
def test_remove_entity_deletes_components() -> None:
    world = World()
    entity = world.create_entity()
    world.add_component(entity, Transform())

    assert world.remove_entity(entity) is True
    assert world.entity_exists(entity) is False
    assert world.remove_entity(entity) is False
    assert world.get_entities_with(Transform) == []


# Contract for Systems: subclasses implement process(world, dt) and mutate components. Here velocity
# 10 units/s for 0.5 s must move x by 5.0 (approx, floating point).
def test_system_processing_updates_world() -> None:
    # Local minimal system: Euler integration x += vx * dt.
    class MoveSystem(System):
        def process(self, world: World, dt: float) -> None:
            for entity_id in world.get_entities_with(Transform, RigidBody):
                transform = world.get_component(entity_id, Transform)
                body = world.get_component(entity_id, RigidBody)
                transform.x += body.velocity_x * dt

    world = World()
    entity = world.create_entity()
    world.add_component(entity, Transform())
    world.add_component(entity, RigidBody(velocity_x=10.0))

    MoveSystem().process(world, 0.5)

    assert world.get_component(entity, Transform).x == pytest.approx(5.0)
