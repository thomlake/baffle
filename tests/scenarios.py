"""The two canonical scenarios, as reusable rules.

Push exercises prerequisite emission and atomic rollback. The locked door exercises
multiple prerequisites from a single rule plus inter-rule mutation visibility. They
cover different guarantees, so both are load-bearing.

Note what the parameters are *not*: annotated. ``BeforeRule[MoveEntity]`` types ``event``
and declares which events the rule matches in one stroke, so ``event.destinaton`` is an
error and ``on`` cannot drift out of step with the code below it.
"""

from __future__ import annotations

from baffle import (
    WORLD,
    BeforeRule,
    Failure,
    IncrementComponent,
    MoveEntity,
    SetComponent,
    delta,
    shift,
)


class Push(BeforeRule[MoveEntity]):
    """Displace a pushable obstruction ahead of the mover."""

    name = "push"
    run_before = ("solid",)

    def do(self, world, event):
        offset = delta(world.vector(event.entity), event.destination)
        for entity_id in world.query("pushable", position=event.destination):
            yield MoveEntity(
                entity=entity_id,
                destination=shift(world.vector(entity_id), offset),
            )


class Solid(BeforeRule[MoveEntity]):
    """Refuse movement into anything solid.

    The mover is skipped, or a solid entity would obstruct its own square and standing
    still would be illegal.
    """

    name = "solid"

    def do(self, world, event):
        for entity_id in world.query("solid", position=event.destination):
            if entity_id != event.entity:
                return Failure("destination_obstructed", {"obstruction": entity_id})
        return ()


class Unlock(BeforeRule[MoveEntity]):
    """Spend a key and unlock the door, as prerequisites of entering it.

    Three prerequisites from one rule. All must succeed or the move fails, and the
    unlocking must be visible to ``solid`` when it runs afterwards.
    """

    name = "unlock"
    run_before = ("solid",)

    def do(self, world, event):
        for door in world.query("locked", position=event.destination):
            yield IncrementComponent(
                entity=event.entity, component="keys", value=-1, minimum=0
            )
            yield SetComponent(entity=door, component="solid", value=False)
            yield SetComponent(entity=door, component="locked", value=False)


def push_world(width: int = 5) -> dict:
    """Player at (0,0), pushable crate at (1,0). Narrow the grid to trap the crate."""
    return {
        WORLD: {"width": width, "height": 1},
        "player": {"position": (0, 0), "solid": True},
        "crate": {"position": (1, 0), "solid": True, "pushable": True},
    }


def door_world(keys: int = 1) -> dict:
    """Player at (0,0) holding `keys` keys, locked door at (1,0)."""
    return {
        WORLD: {"width": 3, "height": 1},
        "player": {"position": (0, 0), "keys": keys, "solid": True},
        "door": {"position": (1, 0), "solid": True, "locked": True},
    }


def snapshot(state: dict) -> dict:
    """A comparable copy, for asserting a world was left untouched."""
    return {entity: dict(components) for entity, components in state.items()}
