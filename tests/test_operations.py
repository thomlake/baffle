"""The built-in mutation events.

Operations are tested directly against a :class:`~baffle.World`, without an engine,
because their contract is narrow: change the world and report, or refuse. Which side of
the fault/refusal line each outcome falls on is the thing worth pinning down -- a refusal
is a legal move that did not work, a fault is a bug.
"""

from __future__ import annotations

import pytest

from baffle import (
    MISSING,
    AppendToList,
    CreateEntity,
    DeleteEntity,
    Effect,
    EngineFault,
    ExtendList,
    Failure,
    IncrementComponent,
    MoveEntity,
    RecordLog,
    RemoveComponent,
    RemoveValue,
    SetComponent,
    World,
)


def build(**entities):
    return World(entities, log=RecordLog(), strict=False)


def run(event, world):
    """Resolve an event the way the engine does: precheck, then apply."""
    refusal = event.precheck(world)
    return refusal if refusal is not None else event.apply(world)


# ---------------------------------------------------------------------------
# The checks hoisted onto EntityEvent and ComponentEvent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "event",
    [
        SetComponent(entity="ghost", component="hp", value=1),
        IncrementComponent(entity="ghost", component="hp", value=1),
        DeleteEntity(entity="ghost"),
        MoveEntity(entity="ghost", destination=(0, 0)),
    ],
    ids=lambda event: type(event).__name__,
)
def test_a_missing_entity_is_a_rejection_not_a_fault(event):
    """Four operations used to carry their own copy of this check."""
    result = run(event, build(player={"hp": 3}))

    assert isinstance(result, Failure)
    assert result.reason == "entity_missing"
    assert result.data == {"entity": "ghost"}


@pytest.mark.parametrize("key", ["", ("hp",), 1, None])
def test_a_malformed_component_key_is_rejected_at_construction(key):
    """Validated once on the base class, not in each operation that reads a key."""
    with pytest.raises(EngineFault, match="non-empty string"):
        SetComponent(entity="player", component=key, value=1)


# ---------------------------------------------------------------------------
# Scalars
# ---------------------------------------------------------------------------


def test_set_replaces_a_value():
    world = build(player={"hp": 3})

    assert isinstance(
        run(SetComponent(entity="player", component="hp", value=5), world), Effect
    )
    assert world.value("player", "hp") == 5


def test_set_introduces_a_namespaced_component():
    """No container above the key has to exist, because there is no container."""
    world = build(player={})

    run(SetComponent(entity="player", component="inventory.keys", value=2), world)

    assert world.value("player", "inventory.keys") == 2


def test_set_can_be_told_to_refuse_absent_components():
    world = build(player={"hp": 3})

    with pytest.raises(EngineFault):
        run(
            SetComponent(entity="player", component="mp", value=1, create=False),
            world,
        )


def test_increment_applies_and_reports_both_sides():
    world = build(player={"keys": 1})

    result = run(
        IncrementComponent(entity="player", component="keys", value=-1, minimum=0),
        world,
    )

    assert isinstance(result, Effect)
    assert result.details == {"previous": 1, "current": 0}
    assert world.value("player", "keys") == 0


def test_increment_rejects_at_its_minimum():
    world = build(player={"keys": 0})

    result = run(
        IncrementComponent(entity="player", component="keys", value=-1, minimum=0),
        world,
    )

    assert isinstance(result, Failure)
    assert result.reason == "minimum_violated"
    assert result.data["minimum"] == 0
    assert world.value("player", "keys") == 0


def test_increment_rejects_at_its_maximum():
    world = build(player={"hp": 9})

    result = run(
        IncrementComponent(entity="player", component="hp", value=3, maximum=10),
        world,
    )

    assert isinstance(result, Failure)
    assert result.reason == "maximum_violated"
    assert world.value("player", "hp") == 9


def test_incrementing_a_non_integer_is_a_fault():
    """Malformed state, not a legal move that failed."""
    world = build(player={"keys": "one"})

    with pytest.raises(EngineFault):
        run(IncrementComponent(entity="player", component="keys", value=1), world)


def test_unset_removes_a_component_and_rejects_when_absent():
    world = build(player={"hp": 3, "poisoned": True})

    assert isinstance(
        run(RemoveComponent(entity="player", component="poisoned"), world), Effect
    )
    assert "poisoned" not in world["player"]

    result = run(RemoveComponent(entity="player", component="poisoned"), world)
    assert isinstance(result, Failure)
    assert result.reason == "component_missing"


def test_removing_a_falsy_component_still_works():
    """``query`` reads a component truthily, so False and absent look alike there.

    They do not here: removal is about the key, not the value.
    """
    world = build(door={"solid": False})

    assert isinstance(
        run(RemoveComponent(entity="door", component="solid"), world), Effect
    )
    assert "solid" not in world["door"]


# ---------------------------------------------------------------------------
# Sequences
# ---------------------------------------------------------------------------


def test_append_and_extend_grow_a_sequence():
    world = build(player={"bag": ("rope",)})

    appended = run(AppendToList(entity="player", component="bag", value="torch"), world)
    extended = run(
        ExtendList(entity="player", component="bag", values=("map", "key")), world
    )

    assert world.value("player", "bag") == ("rope", "torch", "map", "key")
    assert appended.details == {"previous": ("rope",), "current": ("rope", "torch")}
    assert extended.details == {
        "previous": ("rope", "torch"),
        "current": ("rope", "torch", "map", "key"),
    }


def test_append_adds_a_tuple_as_one_element():
    """Which is why ExtendList is not merely sugar once a tuple is a legal value."""
    world = build(player={"log": ()})

    run(AppendToList(entity="player", component="log", value=(1, 2)), world)
    run(ExtendList(entity="player", component="log", values=((3, 4),)), world)

    assert world.value("player", "log") == ((1, 2), (3, 4))


def test_remove_takes_the_first_match_and_rejects_when_absent():
    world = build(player={"bag": ("rope", "torch", "rope")})

    result = run(RemoveValue(entity="player", component="bag", value="rope"), world)
    assert isinstance(result, Effect)
    assert result.details == {
        "removed": "rope",
        "index": 0,
        "previous": ("rope", "torch", "rope"),
        "current": ("torch", "rope"),
    }
    assert world.value("player", "bag") == ("torch", "rope")

    result = run(RemoveValue(entity="player", component="bag", value="anvil"), world)
    assert isinstance(result, Failure)
    assert result.reason == "value_absent"


def test_sequence_operations_refuse_the_wrong_shape():
    world = build(player={"hp": 3})

    appended = run(AppendToList(entity="player", component="hp", value=1), world)
    removed = run(RemoveValue(entity="player", component="hp", value=1), world)

    assert isinstance(appended, Failure) and appended.reason == "not_a_list"
    assert isinstance(removed, Failure) and removed.reason == "not_a_list"


def test_a_sequence_operation_records_one_replacement():
    """No in-place edit exists to record, because the value is immutable."""
    log = RecordLog()
    world = World({"player": {"bag": ("rope",)}}, log=log, strict=False)

    run(AppendToList(entity="player", component="bag", value="torch"), world)

    (record,) = list(log)
    assert (record.kind, record.old, record.new) == (
        "replace",
        ("rope",),
        ("rope", "torch"),
    )


# ---------------------------------------------------------------------------
# Entity lifecycle
# ---------------------------------------------------------------------------


def test_create_adds_an_entity_and_rejects_a_taken_id():
    world = build(player={"hp": 3})

    assert isinstance(
        run(CreateEntity(entity="orc", components={"hp": 5}), world), Effect
    )
    assert world.value("orc", "hp") == 5

    result = run(CreateEntity(entity="orc", components={"hp": 1}), world)
    assert isinstance(result, Failure)
    assert result.reason == "entity_exists"


def test_create_normalises_what_it_stores():
    """A payload decoded from JSON arrives with lists; state holds tuples."""
    world = build()

    run(CreateEntity(entity="orc", components={"bag": ["rope"]}), world)

    assert world.value("orc", "bag") == ("rope",)


def test_delete_removes_an_entity():
    world = build(player={"hp": 3}, orc={"hp": 5})

    assert isinstance(run(DeleteEntity(entity="orc"), world), Effect)
    assert "orc" not in world


# ---------------------------------------------------------------------------
# Movement
# ---------------------------------------------------------------------------


def test_move_reports_where_it_came_from():
    world = build(player={"position": (0, 0)})

    result = run(MoveEntity(entity="player", destination=(2, 1)), world)

    assert isinstance(result, Effect)
    assert result.details == {"origin": (0, 0), "destination": (2, 1)}
    assert world.value("player", "position") == (2, 1)


def test_move_itself_holds_no_opinion_about_legality():
    """Bounds are a rule -- see test_mechanics.py -- not part of the operation."""
    world = build(player={"position": (0, 0)})

    assert isinstance(
        run(MoveEntity(entity="player", destination=(99, 99)), world), Effect
    )
    assert world.value("player", "position") == (99, 99)


# ---------------------------------------------------------------------------
# What an operation computed
# ---------------------------------------------------------------------------
#
# `Effect.details` carries what the operation worked out that the event does not already
# say. Every one of these previous values is computed to record the mutation anyway; an
# `after` rule receives details as an argument and should not have to scan the record
# stream for them.


def test_set_reports_what_it_displaced():
    world = build(player={"hp": 3})

    result = run(SetComponent(entity="player", component="hp", value=5), world)

    assert result.details == {"previous": 3, "current": 5}


def test_set_reports_missing_when_it_introduced_the_component():
    """Which is how a reaction tells a create from a replace."""
    world = build(player={})

    result = run(SetComponent(entity="player", component="hp", value=5), world)

    assert result.details == {"previous": MISSING, "current": 5}


def test_remove_component_reports_what_was_there():
    world = build(player={"poisoned": True})

    result = run(RemoveComponent(entity="player", component="poisoned"), world)

    assert result.details == {"previous": True}


def test_delete_reports_what_the_entity_held():
    """What a reaction to a death needs in order to drop the loot."""
    world = build(orc={"hp": 5, "loot": ("gold",)})

    result = run(DeleteEntity(entity="orc"), world)

    assert result.details == {"components": {"hp": 5, "loot": ("gold",)}}


def test_create_computes_nothing_so_reports_nothing():
    """Its components are on the event; there is no displaced value."""
    world = build()

    result = run(CreateEntity(entity="orc", components={"hp": 5}), world)

    assert result.details == {}
