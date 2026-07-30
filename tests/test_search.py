"""Running the engine as a state-transition function.

MCTS, A* and RL all want the same three things: expand a candidate without committing
to it, compare the state it produced against states reached some other way, and pay for
none of it twice. This is the test that those three work together.
"""

from __future__ import annotations

from baffle import WORLD, Engine, MoveEntity, WithinBounds, state_key
from scenarios import Push, Solid


def grid_world():
    return {
        WORLD: {"width": 4, "height": 4},
        "player": {"position": (1, 1), "solid": True},
        "crate": {"position": (2, 1), "solid": True, "pushable": True},
    }


def engine():
    return Engine(rules=[WithinBounds(), Push(), Solid()], narrate=False)


NEIGHBOURS = ((2, 1), (0, 1), (1, 0), (1, 2))


# ---------------------------------------------------------------------------
# Speculation
# ---------------------------------------------------------------------------


def test_speculating_leaves_the_input_untouched():
    state = grid_world()
    before = state_key(state)

    outcome = engine().speculate(state, MoveEntity(entity="player", destination=(1, 2)))

    assert outcome.committed
    assert state_key(state) == before
    assert outcome.entities["player"]["position"] == (1, 2)


def test_speculation_reports_rejections_without_raising():
    """A search needs illegal moves classified, not thrown."""
    state = {
        WORLD: {"width": 2, "height": 1},
        "player": {"position": (0, 0), "solid": True},
        "wall": {"position": (1, 0), "solid": True},
    }

    outcome = engine().speculate(state, MoveEntity(entity="player", destination=(1, 0)))

    assert not outcome.committed
    assert outcome.failure is not None
    assert outcome.failure.reason == "destination_obstructed"
    assert outcome.entities is not None


def test_speculation_does_not_cascade_consequences():
    """One ply at a time. Reactions belong to whoever decides to keep the move."""
    from baffle import AfterRule, IncrementComponent

    class Count(AfterRule[MoveEntity]):
        name = "count"

        def do(self, world, event, result):
            yield IncrementComponent(entity="player", component="moves", value=1)

    state = grid_world()
    state["player"]["moves"] = 0
    outcome = Engine(rules=[Count()]).speculate(
        state, MoveEntity(entity="player", destination=(1, 2))
    )

    assert outcome.committed
    assert outcome.entities["player"]["moves"] == 0
    assert [type(event).__name__ for event in outcome.consequences] == ["IncrementComponent"]


def test_expanding_every_move_from_one_position():
    """The shape of a node expansion: fan out, keep what is legal, discard the rest."""
    state = grid_world()
    legal = {}

    for destination in NEIGHBOURS:
        outcome = engine().speculate(state, MoveEntity(entity="player", destination=destination))
        if outcome.committed:
            legal[destination] = state_key(outcome.entities)

    # Every direction works: three are empty, and the crate can be pushed east.
    assert set(legal) == set(NEIGHBOURS)
    assert len(set(legal.values())) == 4, "each move must produce a distinct state"
    assert state_key(state) == state_key(grid_world()), "expansion mutated the node"


# ---------------------------------------------------------------------------
# Transposition
# ---------------------------------------------------------------------------


def test_states_reached_by_different_paths_hash_alike():
    """What makes a transposition table work."""
    start = grid_world()
    right_then_up = engine().simulate(start, MoveEntity(entity="player", destination=(1, 2)))
    both = engine().simulate(
        right_then_up.entities, MoveEntity(entity="player", destination=(0, 2))
    )

    up_then_right = engine().simulate(start, MoveEntity(entity="player", destination=(0, 1)))
    both_reversed = engine().simulate(
        up_then_right.entities, MoveEntity(entity="player", destination=(0, 2))
    )

    assert state_key(both.entities) == state_key(both_reversed.entities)
    assert hash(state_key(both.entities)) == hash(state_key(both_reversed.entities))


def test_a_rolled_back_transaction_leaves_the_hash_unchanged():
    state = {
        WORLD: {"width": 2, "height": 1},
        "player": {"position": (0, 0), "solid": True},
        "crate": {"position": (1, 0), "solid": True, "pushable": True},
    }
    before = state_key(state)

    result = engine().simulate(state, MoveEntity(entity="player", destination=(1, 0)))

    assert not result.root.committed
    assert state_key(result.entities) == before


# ---------------------------------------------------------------------------
# The changed set
# ---------------------------------------------------------------------------


def test_a_transaction_reports_which_entities_it_touched():
    """Half of incremental hashing, and free from copy-on-write."""
    outcome = engine().speculate(grid_world(), MoveEntity(entity="player", destination=(2, 1)))

    assert outcome.committed
    assert outcome.touched == {"player", "crate"}


def test_mutations_carry_both_sides_for_incremental_hashing():
    """Enough to XOR the old value out and the new one in, without rehashing the world."""
    from baffle import Mutation

    result = engine().simulate(grid_world(), MoveEntity(entity="player", destination=(2, 1)))
    changes = [record for record in result.records if isinstance(record, Mutation)]

    assert {(change.entity, change.old, change.new) for change in changes} == {
        ("crate", (2, 1), (3, 1)),
        ("player", (1, 1), (2, 1)),
    }
