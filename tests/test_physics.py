# ==============================================================================
# Program: tests/test_physics.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: tests app/ui/engine/physics.py (PhysicsSystem) on a bare ECS World: gravity integration,
# axis-aligned bounding box (AABB) overlap, collision pair detection, resolution for static and
# dynamic bodies, floor bounds, and rigid-body material fields (gravity scale, damping, layers).
# Run: python3 -m pytest tests/test_physics.py -q
# ==============================================================================

from __future__ import annotations

import pytest

from app.ui.engine.ecs import Collider, RigidBody, Transform, World
from app.ui.engine.physics import PhysicsSystem


# Helper: create an entity with Transform, RigidBody and Collider (default 10x10 box) and return its id.
def add_body(
    world: World,
    x: float,
    y: float,
    velocity_x: float = 0.0,
    velocity_y: float = 0.0,
    is_static: bool = False,
    width: float = 10.0,
    height: float = 10.0,
) -> int:
    """Add a physics-ready entity to `world` at (x, y) and return its entity id."""
    entity = world.create_entity()
    world.add_component(entity, Transform(x=x, y=y))
    world.add_component(entity, RigidBody(velocity_x=velocity_x, velocity_y=velocity_y, is_static=is_static))
    world.add_component(entity, Collider(width=width, height=height))
    return entity


# Semi-implicit Euler check with gravity 10 and two 0.5 s steps: velocity 10, and position 7.5
# (= 0.5*5 + 0.5*10: each step moves by the updated velocity times dt). The floor is far away so it is not hit.
def test_gravity_applies_over_multiple_frames() -> None:
    world = World()
    entity = add_body(world, 0, 0)
    physics = PhysicsSystem(gravity=10.0, floor_y=1000)

    physics.process(world, 0.5)
    physics.process(world, 0.5)

    body = world.get_component(entity, RigidBody)
    transform = world.get_component(entity, Transform)
    assert body.velocity_y == pytest.approx(10.0)
    assert transform.y == pytest.approx(7.5)


# Boxes are 10 wide and measured from the Transform position: 9 apart overlaps, exactly 10 apart
# only touches and counts as NOT overlapping (strict inequality avoids resting bodies colliding forever).
def test_aabb_overlap_detection_cases() -> None:
    physics = PhysicsSystem()
    collider = Collider(width=10, height=10)

    assert physics.aabb_overlaps(Transform(0, 0), collider, Transform(9, 0), collider) is True
    assert physics.aabb_overlaps(Transform(0, 0), collider, Transform(10, 0), collider) is False
    assert physics.aabb_overlaps(Transform(0, 0), collider, Transform(11, 0), collider) is False


# Only the two nearby bodies produce a pair, ordered by entity id; the far body is ignored.
def test_detect_collisions_returns_pairs() -> None:
    world = World()
    first = add_body(world, 0, 0)
    second = add_body(world, 5, 5)
    add_body(world, 100, 100)
    physics = PhysicsSystem()

    pairs = physics.detect_collisions(world)

    assert [(pair.first, pair.second) for pair in pairs] == [(first, second)]


# A falling body overlapping a static one is pushed back to the static's surface (y = 15 - 10 = 5)
# and its downward velocity is zeroed, so it rests on the platform instead of sinking in.
def test_dynamic_static_collision_snaps_to_boundary_and_stops_velocity() -> None:
    world = World()
    dynamic = add_body(world, 0, 9, velocity_y=20)
    static = add_body(world, 0, 15, is_static=True)
    physics = PhysicsSystem(gravity=0.0)

    physics.resolve_collision(world, dynamic, static)

    assert world.get_component(dynamic, Transform).y == pytest.approx(5.0)
    assert world.get_component(dynamic, RigidBody).velocity_y == pytest.approx(0.0)


# Two moving bodies (mass 2 at +10, mass 1 at -5) exchange velocity scaled by the mass ratio. The
# expected values (-0.625 and 5.0) are the engine's simplified rule, pinned so that changes to the
# collision response are deliberate. dt is 0 so only the collision step runs, not movement.
def test_dynamic_dynamic_collision_swaps_velocities_scaled_by_mass() -> None:
    world = World()
    first = add_body(world, 0, 0, velocity_x=10)
    second = add_body(world, 5, 0, velocity_x=-5)
    world.get_component(first, RigidBody).mass = 2.0
    world.get_component(second, RigidBody).mass = 1.0
    physics = PhysicsSystem(gravity=0.0)

    physics.process(world, 0.0)

    assert world.get_component(first, RigidBody).velocity_x == pytest.approx(-0.625)
    assert world.get_component(second, RigidBody).velocity_x == pytest.approx(5.0)


# Floor at y=100: a 10-high body that dips below is moved so its bottom rests on the floor (y=90) and its downward velocity is cleared.
def test_world_boundary_enforcement() -> None:
    world = World()
    entity = add_body(world, 0, 95, velocity_y=20, height=10)
    physics = PhysicsSystem(gravity=0.0, floor_y=100.0)

    physics.enforce_world_bounds(world)

    assert world.get_component(entity, Transform).y == pytest.approx(90.0)
    assert world.get_component(entity, RigidBody).velocity_y <= 0.0


# Material fields: gravity_scale and damping (both 0.5) reduce the vertical and horizontal speed
# after one 1 s step to the asserted 5.0 each (this pins the current integration formula), and
# bodies on different collision layers do not collide even though they overlap, so
# physics.collisions stays empty.
def test_rigidbody_material_controls_gravity_damping_and_layer() -> None:
    world = World()
    first = add_body(world, 0, 0)
    second = add_body(world, 5, 0)
    first_body = world.get_component(first, RigidBody)
    second_body = world.get_component(second, RigidBody)
    first_body.gravity_scale = 0.5
    first_body.damping = 0.5
    first_body.velocity_x = 10.0
    first_body.collision_layer = 1
    second_body.collision_layer = 2
    physics = PhysicsSystem(gravity=20.0, floor_y=1000.0)

    physics.process(world, 1.0)

    assert first_body.velocity_y == pytest.approx(5.0)
    assert first_body.velocity_x == pytest.approx(5.0)
    assert physics.collisions == []
