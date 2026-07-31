"""Rules the engine ships.

The whole argument for the four phases is that a mechanic is a rule, so the engine's own
mechanics are rules too -- not checks buried inside an operation. A game installs the ones
it wants and is not paying for the rest.

These are also the worked examples: whatever is here, a game could have written itself.
"""

from __future__ import annotations

from .events import Failure
from .operations import MoveEntity
from .rules import BeforeRule
from .state import World
from .types import WORLD


class WithinBounds(BeforeRule[MoveEntity]):
    """Refuse a move that leaves the grid declared on the world entity.

    ``width`` and ``height`` on :data:`~baffle.types.WORLD` are the grid. When either is
    absent the world is unbounded and this stays out of the way, which is what lets a
    battle system or a card game use movement without declaring a shape.

    Install it before rules that emit further movement, so an illegal destination is
    refused without first computing the consequences of reaching it. Registration order
    does that; a game with its own opinion declares ``run_after = ("within_bounds",)``.
    """

    def do(self, world: World, event: MoveEntity) -> Failure | tuple[()]:
        width = world.value(WORLD, "width", default=None)
        height = world.value(WORLD, "height", default=None)
        if not isinstance(width, int) or not isinstance(height, int):
            return ()
        x, y = event.destination
        if 0 <= x < width and 0 <= y < height:
            return ()
        return Failure(
            "outside_grid",
            {
                "entity": event.entity,
                "origin": world.value(event.entity, "position", default=None),
                "destination": event.destination,
                "bounds": (width, height),
            },
        )
