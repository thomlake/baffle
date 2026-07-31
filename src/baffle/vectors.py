"""Grid arithmetic, kept out of rules and out of any expression language.

Positions and directions are tuples, so they are hashable and cannot be mutated in
place once they are shared between an event and the state.
"""

from __future__ import annotations

from typing import TypeGuard

type Vec2 = tuple[int, int]

NORTH: Vec2 = (0, -1)
SOUTH: Vec2 = (0, 1)
EAST: Vec2 = (1, 0)
WEST: Vec2 = (-1, 0)


def is_vec2(value: object) -> TypeGuard[Vec2]:
    """Whether `value` is a pair of plain integers.

    Shared by the two places a vector arrives from somewhere untyped: a component read
    back out of the world, and a coordinate field on an event. Returning a
    :class:`~typing.TypeGuard` is what lets both narrow rather than cast.

    `bool` is excluded because it is an `int` in Python, so a position of
    ``(True, False)`` is a bug wearing a coordinate's clothes.
    """
    return (
        isinstance(value, tuple)
        and len(value) == 2
        and all(
            isinstance(number, int) and not isinstance(number, bool) for number in value
        )
    )


def shift(position: Vec2, direction: Vec2) -> Vec2:
    """Move `position` by `direction`."""
    return (position[0] + direction[0], position[1] + direction[1])


def delta(origin: Vec2, destination: Vec2) -> Vec2:
    """The direction that carries `origin` to `destination`."""
    return (destination[0] - origin[0], destination[1] - origin[1])


def scale(direction: Vec2, factor: int) -> Vec2:
    return (direction[0] * factor, direction[1] * factor)


def manhattan(a: Vec2, b: Vec2) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])
