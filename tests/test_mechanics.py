"""The rules the engine ships.

``WithinBounds`` used to be a check inside ``MoveEntity.apply``. It is a rule now, which
is the point: the engine's own mechanics go through the same mechanism a game's do, so
there is nothing about them a game could not have written.
"""

from __future__ import annotations

from baffle import WORLD, Engine, MoveEntity, WithinBounds
from scenarios import Push, Solid, push_world


def grid(width=3, height=2, **entities):
    return {WORLD: {"width": width, "height": height}, **entities}


def move(engine, state, destination):
    return engine.simulate(state, MoveEntity(entity="player", destination=destination))


def test_a_move_inside_the_grid_is_allowed():
    result = move(
        Engine([WithinBounds()]), grid(player={"position": (0, 0)}), (2, 1)
    )

    assert result.root.committed
    assert result.entities["player"]["position"] == (2, 1)


def test_a_move_outside_the_grid_is_refused():
    result = move(
        Engine([WithinBounds()]), grid(player={"position": (2, 1)}), (3, 1)
    )

    assert not result.root.committed
    assert result.root.failure is not None
    assert result.root.failure.reason == "outside_grid"
    assert result.root.failure.data["bounds"] == (3, 2)
    assert result.root.failure.data["origin"] == (2, 1)
    assert result.entities["player"]["position"] == (2, 1)


def test_an_undeclared_grid_means_an_unbounded_world():
    """A battle system or a card game uses movement without declaring a shape."""
    result = move(Engine([WithinBounds()]), {"player": {"position": (0, 0)}}, (99, 99))

    assert result.root.committed
    assert result.entities["player"]["position"] == (99, 99)


def test_a_partially_declared_grid_is_also_unbounded():
    state = {WORLD: {"width": 3}, "player": {"position": (0, 0)}}

    assert move(Engine([WithinBounds()]), state, (9, 9)).root.committed


def test_an_uninstalled_rule_means_no_bounds_are_enforced():
    """The engine holds no opinion a game did not ask for."""
    result = move(Engine(), grid(player={"position": (0, 0)}), (9, 9))

    assert result.root.committed


def test_bounds_refuse_a_required_move_and_roll_back_the_whole_chain():
    """The canonical chain: the crate cannot leave the grid, so the player cannot move."""
    state = push_world(width=2)
    engine = Engine([WithinBounds(), Push(), Solid()])

    result = move(engine, state, (1, 0))

    assert not result.root.committed
    assert [f.reason for f in result.root.failure.chain()] == [
        "required_event_failed",
        "outside_grid",
    ]
    assert result.entities["crate"]["position"] == (1, 0)
