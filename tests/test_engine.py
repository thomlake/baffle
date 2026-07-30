"""The engine facade: registration, compilation, and the knobs."""

from __future__ import annotations

import dataclasses

import pytest

from baffle import (
    AfterRule,
    BeforeRule,
    Engine,
    EngineFault,
    Event,
    IncrementComponent,
    MoveEntity,
    SetComponent,
    emit,
)


class Tick(Event):
    name = "test.tick"


def counting_rule(order, name):
    return type(
        "R",
        (BeforeRule,),
        {
            "name": name,
            "on": Tick,
            "do": lambda self, world, event: order.append(name) or (),
        },
    )()


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_rules_can_be_added_after_construction():
    order: list[str] = []
    engine = Engine().add(counting_rule(order, "first")).add(counting_rule(order, "second"))

    engine.simulate({}, Tick())

    assert order == ["first", "second"]


def test_adding_a_rule_invalidates_the_compiled_set():
    order: list[str] = []
    engine = Engine(rules=[counting_rule(order, "first")])
    engine.simulate({}, Tick())

    engine.add(counting_rule(order, "second"))
    engine.simulate({}, Tick())

    assert order == ["first", "first", "second"]


def test_compilation_is_cached_until_the_rule_set_changes():
    engine = Engine(rules=[counting_rule([], "only")])

    compiled = engine.compile()
    assert engine.compile() is compiled

    engine.add(counting_rule([], "another"))
    assert engine.compile() is not compiled


def test_the_engine_holds_no_world_state():
    """Two runs from the same input produce the same result."""
    engine = Engine()
    state = {"counter": {"value": 0}}
    event = IncrementComponent(entity="counter", component="value", value=1)

    first = engine.simulate(state, event)
    second = engine.simulate(state, event)

    assert first.entities["counter"]["value"] == second.entities["counter"]["value"] == 1
    assert state["counter"]["value"] == 0


# ---------------------------------------------------------------------------
# An event is the only way to change state
# ---------------------------------------------------------------------------


def test_a_rule_cannot_reach_through_a_view_to_write():
    """Under copy-on-write this write would reach committed state and survive rollback."""

    class Sneaky(BeforeRule[Tick]):
        name = "sneaky"

        def do(self, world, event):
            # Deliberately wrong: a rule must emit an event, never write state. The
            # checker objects, which is the point -- the sealed world is the backstop.
            world["counter"]["value"] = 99  # type: ignore[index]
            return ()

    with pytest.raises(TypeError):
        Engine(rules=[Sneaky()]).simulate({"counter": {"value": 0}}, Tick())


def test_a_before_rule_cannot_write_through_the_world_api():
    """This used to commit, with no frame and no operation to explain it."""

    class Sneaky(BeforeRule[Tick]):
        name = "sneaky"

        def do(self, world, event):
            world.set("counter", "value", 99)
            return ()

    with pytest.raises(EngineFault, match="may not write"):
        Engine(rules=[Sneaky()]).simulate({"counter": {"value": 0}}, Tick())


def test_an_after_rule_cannot_write_through_the_world_api():
    """The worse half: the write was discarded, but its mutation was logged as committed.

    ``committed_mutations()`` is the hasher's view, so a transposition table took on a
    change that never happened -- silently, and only in the reaction phase.
    """

    class Sneaky(AfterRule[Tick]):
        name = "sneaky"

        def do(self, world, event, result):
            world.set("counter", "value", 99)
            return ()

    with pytest.raises(EngineFault, match="may not write"):
        Engine(rules=[Sneaky()]).simulate({"counter": {"value": 0}}, Tick())


def test_reads_still_work_against_a_sealed_world():
    seen: list[int] = []

    class Peek(BeforeRule[Tick]):
        name = "peek"

        def do(self, world, event):
            seen.append(world.value("counter", "value"))
            seen.append(world.value("counter", "missing", default=-1))
            return ()

    Engine(rules=[Peek()]).simulate({"counter": {"value": 7}}, Tick())

    assert seen == [7, -1]


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------


def test_the_cascade_length_is_bounded():
    class Forever(AfterRule[IncrementComponent]):
        name = "forever"

        def do(self, world, event, result):
            yield IncrementComponent(entity="counter", component="value", value=1)

    engine = Engine(rules=[Forever()], max_transactions=4)
    with pytest.raises(EngineFault, match="maximum of 4 transactions"):
        engine.simulate(
            {"counter": {"value": 0}},
            IncrementComponent(entity="counter", component="value", value=1),
        )


# ---------------------------------------------------------------------------
# The dynamic escape hatch
# ---------------------------------------------------------------------------


def test_events_can_be_constructed_by_name():
    """For data-driven emission, where the class is not known at authoring time."""
    event = emit("set_component", entity="player", component="hp", value=5)

    assert isinstance(event, SetComponent)
    result = Engine().simulate({"player": {}}, event)
    assert result.entities["player"]["hp"] == 5


def test_a_signal_event_succeeds_without_mutating():
    """Events exist to be reacted to as much as to do work."""
    reacted: list[str] = []

    class Note(AfterRule[Tick]):
        name = "note"

        def do(self, world, event, result):
            reacted.append(event.name)
            return ()

    result = Engine(rules=[Note()]).simulate({}, Tick())

    assert result.root.committed
    assert reacted == ["test.tick"]
    assert result.entities == {}


def test_event_constructors_stay_visible_to_a_type_checker():
    """A runtime guard on the one thing the test suite otherwise cannot see.

    ``__init_subclass__`` synthesises ``__init__`` at runtime, which static analysis
    cannot follow. Without the ``dataclass_transform`` marker every event appears to take
    no arguments, so a misspelled field looks exactly like a correct call -- and checked
    construction is the whole reason rules are written in Python rather than YAML.
    """
    spec = getattr(Event, "__dataclass_transform__", None)

    assert spec is not None, "Event lost its dataclass_transform marker"
    assert spec["frozen_default"] is True, "events must read as frozen to a checker"


def test_events_are_frozen_value_objects():
    """Equal by value and hashable, which state hashing and replay both rely on."""
    event = MoveEntity(entity="player", destination=(2, 3))

    assert event == MoveEntity(entity="player", destination=(2, 3))
    assert hash(event) == hash(MoveEntity(entity="player", destination=(2, 3)))
    assert dataclasses.asdict(event) == {"entity": "player", "destination": (2, 3)}

    with pytest.raises(dataclasses.FrozenInstanceError):
        event.destination = (0, 0)  # type: ignore[misc]  # frozen, as asserted
