from __future__ import annotations

from dataclasses import dataclass

from app.ui.engine.ecs import Collider, RigidBody, System, Transform, World


@dataclass(frozen=True)
class CollisionPair:
    """Pair of colliding ECS entity ids."""

    first: int
    second: int


class PhysicsSystem(System):
    """Simple 2D gravity, integration, AABB collision, and bounds system."""

    def __init__(self, gravity: float = 980.0, floor_y: float = 600.0, world_width: float = 1000.0) -> None:
        self.gravity = gravity
        self.floor_y = floor_y
        self.world_width = world_width
        self.collisions: list[CollisionPair] = []

    def process(self, world: World, dt: float) -> None:
        """Advance physics and store collision pairs from this step."""
        if dt < 0:
            raise ValueError("dt must not be negative.")
        for entity_id in world.get_entities_with(Transform, RigidBody):
            transform = world.get_component(entity_id, Transform)
            body = world.get_component(entity_id, RigidBody)
            if body.is_static:
                continue
            body.velocity_y += self.gravity * body.gravity_scale * dt
            damping = max(0.0, min(1.0, body.damping * dt))
            body.velocity_x *= 1.0 - damping
            body.velocity_y *= 1.0 - damping
            transform.x += body.velocity_x * dt
            transform.y += body.velocity_y * dt

        self.collisions = self.detect_collisions(world)
        for pair in self.collisions:
            self.resolve_collision(world, pair.first, pair.second)
        self.enforce_world_bounds(world)

    def detect_collisions(self, world: World) -> list[CollisionPair]:
        """Return all non-trigger AABB overlaps in the world."""
        entities = world.get_entities_with(Transform, Collider)
        collisions: list[CollisionPair] = []
        for left_index, first in enumerate(entities):
            for second in entities[left_index + 1 :]:
                first_collider = world.get_component(first, Collider)
                second_collider = world.get_component(second, Collider)
                if first_collider.is_trigger or second_collider.is_trigger:
                    continue
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

    def resolve_collision(self, world: World, first: int, second: int) -> None:
        """Resolve collision response between static and dynamic bodies."""
        if not world.has_component(first, RigidBody) or not world.has_component(second, RigidBody):
            return

        first_body = world.get_component(first, RigidBody)
        second_body = world.get_component(second, RigidBody)
        if first_body.is_static and second_body.is_static:
            return
        if first_body.is_static:
            self._resolve_dynamic_static(world, dynamic=second, static=first)
        elif second_body.is_static:
            self._resolve_dynamic_static(world, dynamic=first, static=second)
        else:
            self._resolve_dynamic_dynamic(first_body, second_body)

    def enforce_world_bounds(self, world: World) -> None:
        """Apply implicit world bounds as static colliders."""
        for entity_id in world.get_entities_with(Transform, Collider, RigidBody):
            transform = world.get_component(entity_id, Transform)
            collider = world.get_component(entity_id, Collider)
            body = world.get_component(entity_id, RigidBody)
            if body.is_static:
                continue
            if transform.y + collider.height > self.floor_y:
                transform.y = self.floor_y - collider.height
                body.velocity_y = min(0.0, -abs(body.velocity_y) * body.restitution)
                body.velocity_x *= max(0.0, 1.0 - body.friction)
            if transform.x < 0:
                transform.x = 0.0
                body.velocity_x = abs(body.velocity_x) * 0.5
            if transform.x + collider.width > self.world_width:
                transform.x = self.world_width - collider.width
                body.velocity_x = -abs(body.velocity_x) * 0.5

    def aabb_overlaps(
        self,
        first_transform: Transform,
        first_collider: Collider,
        second_transform: Transform,
        second_collider: Collider,
    ) -> bool:
        """Return True when two AABBs overlap with positive area."""
        return not (
            first_transform.x + first_collider.width <= second_transform.x
            or second_transform.x + second_collider.width <= first_transform.x
            or first_transform.y + first_collider.height <= second_transform.y
            or second_transform.y + second_collider.height <= first_transform.y
        )

    def _resolve_dynamic_static(self, world: World, dynamic: int, static: int) -> None:
        dynamic_transform = world.get_component(dynamic, Transform)
        dynamic_collider = world.get_component(dynamic, Collider)
        dynamic_body = world.get_component(dynamic, RigidBody)
        static_transform = world.get_component(static, Transform)
        static_collider = world.get_component(static, Collider)

        dynamic_center_x = dynamic_transform.x + dynamic_collider.width / 2.0
        dynamic_center_y = dynamic_transform.y + dynamic_collider.height / 2.0
        static_center_x = static_transform.x + static_collider.width / 2.0
        static_center_y = static_transform.y + static_collider.height / 2.0
        overlap_x = (dynamic_collider.width + static_collider.width) / 2.0 - abs(dynamic_center_x - static_center_x)
        overlap_y = (dynamic_collider.height + static_collider.height) / 2.0 - abs(dynamic_center_y - static_center_y)

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

    def _resolve_dynamic_dynamic(self, first_body: RigidBody, second_body: RigidBody) -> None:
        first_mass = max(first_body.mass, 0.001)
        second_mass = max(second_body.mass, 0.001)
        first_vx, first_vy = first_body.velocity_x, first_body.velocity_y
        restitution = max(0.0, min(1.0, (first_body.restitution + second_body.restitution) / 2.0))
        first_body.velocity_x = second_body.velocity_x * (second_mass / first_mass) * restitution
        first_body.velocity_y = second_body.velocity_y * (second_mass / first_mass) * restitution
        second_body.velocity_x = first_vx * (first_mass / second_mass) * restitution
        second_body.velocity_y = first_vy * (first_mass / second_mass) * restitution
