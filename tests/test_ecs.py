from __future__ import annotations

import pytest

from app.security import SecurityError
from app.ui.engine.ecs import Collider, RigidBody, Sprite, System, Transform, World


def test_entity_creation_returns_unique_ids() -> None:
    world = World()

    first = world.create_entity()
    second = world.create_entity()

    assert first != second
    assert world.entity_exists(first) is True
    assert world.get_entities_with() == [first, second]


def test_add_and_get_component() -> None:
    world = World()
    entity = world.create_entity()
    transform = Transform(x=4.0, y=8.0)

    world.add_component(entity, transform)

    assert world.get_component(entity, Transform) is transform
    assert world.has_component(entity, Transform) is True


def test_get_component_rejects_missing_entity_or_component() -> None:
    world = World()
    entity = world.create_entity()

    with pytest.raises(SecurityError):
        world.add_component(999, Transform())

    with pytest.raises(SecurityError):
        world.get_component(entity, Sprite)


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


def test_remove_entity_deletes_components() -> None:
    world = World()
    entity = world.create_entity()
    world.add_component(entity, Transform())

    assert world.remove_entity(entity) is True
    assert world.entity_exists(entity) is False
    assert world.remove_entity(entity) is False
    assert world.get_entities_with(Transform) == []


def test_system_processing_updates_world() -> None:
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
