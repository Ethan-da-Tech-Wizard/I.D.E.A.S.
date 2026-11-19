# ==============================================================================
# Program: app/ui/engine/physics.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: the small 2D physics step used by the Engine workspace: gravity, damping, movement,
# box-vs-box (AABB) collision detection and response, and world bounds. It is a deliberately
# simple arcade model, not a rigid-body solver: no rotation, no stacking stability, no continuous
# collision detection (fast bodies can tunnel through thin walls).
# Used by: app/ui/engine/workspace.py (calls PhysicsSystem.process each frame) and tests/test_physics.py.
# Units: pixels, seconds. y grows DOWNWARD, so positive gravity pulls objects down the screen.
# Defaults: gravity 980 px/s^2 (about 1 g if 100 px = 1 m), floor at y=600, world 1000 px wide.
# Step order in process(): integrate velocity and position -> detect overlaps -> resolve them ->
# enforce floor and side walls. Order matters because resolution works on already-moved positions.
# ==============================================================================

from __future__ import annotations

from dataclasses import dataclass

from app.ui.engine.ecs import Collider, RigidBody, System, Transform, World


# Two entity ids that overlap in the current step. Frozen so pairs can be compared or stored safely; `first` is always the lower id.
@dataclass(frozen=True)
class CollisionPair:
    """Pair of colliding ECS entity ids."""

    first: int
    second: int


class PhysicsSystem(System):
    """Simple 2D gravity, integration, AABB collision, and bounds system."""

    def __init__(self, gravity: float = 980.0, floor_y: float = 600.0, world_width: float = 1000.0) -> None:
        """Create a physics system.

        Args:
            gravity: downward acceleration in px/s^2.
            floor_y: y coordinate of the floor (bottom edge of the world).
            world_width: width of the playable area; left wall is at x=0.
        """
        self.gravity = gravity
        self.floor_y = floor_y
        self.world_width = world_width
        # Overlapping pairs found in the most recent process() call (read by tests and the workspace).
        self.collisions: list[CollisionPair] = []

    # Advance physics and store collision pairs from this step.
    #
    # Args:
    #     world: the ECS world to simulate.
    #     dt: elapsed time in seconds (must be >= 0).
    #
    # Raises:
    #     ValueError: dt is negative.
    def process(self, world: World, dt: float) -> None:
        """Advance physics and store collision pairs from this step."""
        # Negative time would run the simulation backwards.
        if dt < 0:
            raise ValueError("dt must not be negative.")
        # Only entities that can move are integrated.
        for entity_id in world.get_entities_with(Transform, RigidBody):
            transform = world.get_component(entity_id, Transform)
            body = world.get_component(entity_id, RigidBody)
            # Static bodies are walls/platforms: they never accelerate or move.
            if body.is_static:
                continue
            # Velocity change = gravity x body's scale x dt (semi-implicit Euler: velocity first, then position).
            body.velocity_y += self.gravity * body.gravity_scale * dt
            # Fraction of speed lost this step, clamped to 0..1 so large dt cannot reverse the velocity.
            damping = max(0.0, min(1.0, body.damping * dt))
            body.velocity_x *= 1.0 - damping
            body.velocity_y *= 1.0 - damping
            # Position uses the already-updated velocity (semi-implicit Euler), which is more stable than explicit Euler.
            transform.x += body.velocity_x * dt
            transform.y += body.velocity_y * dt

        # Collisions are computed after everyone has moved, then resolved pair by pair.
        self.collisions = self.detect_collisions(world)
        for pair in self.collisions:
            self.resolve_collision(world, pair.first, pair.second)
        # Last, so the floor and walls win over collision pushes.
        self.enforce_world_bounds(world)

    # Return all non-trigger AABB overlaps in the world.
    #
    # Pairs are (lower id, higher id). Triggers never collide; bodies on different collision layers are skipped.
    def detect_collisions(self, world: World) -> list[CollisionPair]:
        """Return all non-trigger AABB overlaps in the world."""
        entities = world.get_entities_with(Transform, Collider)
        collisions: list[CollisionPair] = []
        # Compare each unordered pair once (O(n^2); fine for the small scenes the editor allows).
        for left_index, first in enumerate(entities):
            for second in entities[left_index + 1 :]:
                first_collider = world.get_component(first, Collider)
                second_collider = world.get_component(second, Collider)
                # Triggers are overlap zones, not solid objects.
                if first_collider.is_trigger or second_collider.is_trigger:
                    continue
                # Layer filtering only applies when both entities have bodies; colliders without a body always collide.
                if world.has_component(first, RigidBody) and world.has_component(second, RigidBody):
                    first_body = world.get_component(first, RigidBody)
                    second_body = world.get_component(second, RigidBody)
                    if first_body.collision_layer != second_body.collision_layer:
                        continue
                if self.aabb_overlaps(
                    world.get_component(first, Transform),
                    first_collider,
                    world.get_component(second, Transform),
                    second_collider,
                ):
                    collisions.append(CollisionPair(first, second))
        return collisions

    # Resolve collision response between static and dynamic bodies (and dynamic-dynamic).
    #
    # Pairs without two RigidBody components, or two static bodies, are ignored.
    def resolve_collision(self, world: World, first: int, second: int) -> None:
        """Resolve collision response between static and dynamic bodies."""
        if not world.has_component(first, RigidBody) or not world.has_component(second, RigidBody):
            return

        first_body = world.get_component(first, RigidBody)
        second_body = world.get_component(second, RigidBody)
        if first_body.is_static and second_body.is_static:
            return
        # Exactly one static: push the dynamic one out. Both dynamic: exchange momentum-like velocities.
        if first_body.is_static:
            self._resolve_dynamic_static(world, dynamic=second, static=first)
        elif second_body.is_static:
            self._resolve_dynamic_static(world, dynamic=first, static=second)
        else:
            self._resolve_dynamic_dynamic(first_body, second_body)

    # Keep dynamic bodies inside the world: a floor at floor_y and walls at x=0 and x=world_width.
    def enforce_world_bounds(self, world: World) -> None:
        """Apply implicit world bounds as static colliders."""
        for entity_id in world.get_entities_with(Transform, Collider, RigidBody):
            transform = world.get_component(entity_id, Transform)
            collider = world.get_component(entity_id, Collider)
            body = world.get_component(entity_id, RigidBody)
            if body.is_static:
                continue
            # Floor: snap the box bottom to the floor and bounce upward with speed scaled by restitution; friction slows horizontal movement while touching.
            if transform.y + collider.height > self.floor_y:
                transform.y = self.floor_y - collider.height
                body.velocity_y = min(0.0, -abs(body.velocity_y) * body.restitution)
                body.velocity_x *= max(0.0, 1.0 - body.friction)
            # Walls: snap inside and reverse horizontal velocity at half speed (a fixed damping, independent of restitution).
            if transform.x < 0:
                transform.x = 0.0
                body.velocity_x = abs(body.velocity_x) * 0.5
            if transform.x + collider.width > self.world_width:
                transform.x = self.world_width - collider.width
                body.velocity_x = -abs(body.velocity_x) * 0.5

    # Return True when two AABBs overlap with positive area.
    #
    # Boxes that only touch edges do not overlap (the comparisons use <=), which keeps resting bodies from colliding every frame.
    def aabb_overlaps(
        self,
        first_transform: Transform,
        first_collider: Collider,
        second_transform: Transform,
        second_collider: Collider,
    ) -> bool:
        """Return True when two AABBs overlap with positive area."""
        # Boxes are separate if one is entirely left of, right of, above or below the other.
        return not (
            first_transform.x + first_collider.width <= second_transform.x
            or second_transform.x + second_collider.width <= first_transform.x
            or first_transform.y + first_collider.height <= second_transform.y
            or second_transform.y + second_collider.height <= first_transform.y
        )

    # Push a dynamic body out of a static one along the axis of LEAST penetration, and stop its velocity on that axis (so it rests on or slides along the surface).
    def _resolve_dynamic_static(self, world: World, dynamic: int, static: int) -> None:
        dynamic_transform = world.get_component(dynamic, Transform)
        dynamic_collider = world.get_component(dynamic, Collider)
        dynamic_body = world.get_component(dynamic, RigidBody)
        static_transform = world.get_component(static, Transform)
        static_collider = world.get_component(static, Collider)

        # Centres and half-extent sums give the penetration depth on each axis.
        dynamic_center_x = dynamic_transform.x + dynamic_collider.width / 2.0
        dynamic_center_y = dynamic_transform.y + dynamic_collider.height / 2.0
        static_center_x = static_transform.x + static_collider.width / 2.0
        static_center_y = static_transform.y + static_collider.height / 2.0
        overlap_x = (dynamic_collider.width + static_collider.width) / 2.0 - abs(dynamic_center_x - static_center_x)
        overlap_y = (dynamic_collider.height + static_collider.height) / 2.0 - abs(dynamic_center_y - static_center_y)

        # Smaller overlap = the shortest way out: resolve horizontally if the boxes overlap less on x, otherwise vertically (this is what lets a falling body land on a platform).
        if overlap_x < overlap_y:
            if dynamic_center_x < static_center_x:
                dynamic_transform.x = static_transform.x - dynamic_collider.width
            else:
                dynamic_transform.x = static_transform.x + static_collider.width
            dynamic_body.velocity_x = 0.0
        else:
            if dynamic_center_y < static_center_y:
                dynamic_transform.y = static_transform.y - dynamic_collider.height
            else:
                dynamic_transform.y = static_transform.y + static_collider.height
            dynamic_body.velocity_y = 0.0

    # Exchange velocities between two moving bodies, scaled by the mass ratio and the average restitution. This is the engine's simplified rule, not conservation of momentum; it is pinned by tests/test_physics.py.
    def _resolve_dynamic_dynamic(self, first_body: RigidBody, second_body: RigidBody) -> None:
        # Mass floor avoids division by zero for degenerate bodies.
        first_mass = max(first_body.mass, 0.001)
        second_mass = max(second_body.mass, 0.001)
        # Remember the first body's old velocity because it is overwritten before the second body reads it.
        first_vx, first_vy = first_body.velocity_x, first_body.velocity_y
        restitution = max(0.0, min(1.0, (first_body.restitution + second_body.restitution) / 2.0))
        first_body.velocity_x = second_body.velocity_x * (second_mass / first_mass) * restitution
        first_body.velocity_y = second_body.velocity_y * (second_mass / first_mass) * restitution
        second_body.velocity_x = first_vx * (first_mass / second_mass) * restitution
        second_body.velocity_y = first_vy * (first_mass / second_mass) * restitution
