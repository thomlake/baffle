"""Defects verified against the previous implementation.

Each test here reproduced a real, observed failure before the rewrite. They are kept
separate from the invariant suite because they pin down *bugs*, not semantics -- if one
of these ever fails again it is a regression, not a design change.
"""

from __future__ import annotations

import pytest

from baffle import (
    WORLD,
    AfterRule,
    AppendToList,
    BeforeRule,
    CreateEntity,
    DeleteEntity,
    Engine,
    EngineFault,
    Event,
    ExtendList,
    IncrementComponent,
    MoveEntity,
    SetComponent,
    WithinBounds,
    registered,
)
from scenarios import Push, Solid, push_world, snapshot


class Trigger(Event):
    name = "test.trigger"


# ---------------------------------------------------------------------------
# Event payloads used to be aliased into state.
# ---------------------------------------------------------------------------


def test_a_set_does_not_alias_its_payload_into_state():
    """Writing a.pos[0] used to change b.pos: the two shared one list.

    The whole class of defect is now unreachable rather than defended against -- a
    component value is immutable, so there is nothing for two entities to share. What is
    left worth asserting is that a value read out of state and written back stays intact.
    """

    class CopyPosition(BeforeRule[Trigger]):
        name = "copy-position"

        def do(self, world, event):
            yield SetComponent(
                entity="a",
                component="position",
                value=world["b"]["position"],
            )

    state = {"a": {"position": None}, "b": {"position": (1, 2)}}
    result = Engine(rules=[CopyPosition()]).simulate(state, Trigger())

    assert result.entities["a"]["position"] == (1, 2)
    assert result.entities["b"]["position"] == (1, 2)


@pytest.mark.parametrize(
    "event_for",
    [
        pytest.param(
            lambda shared: SetComponent(entity="a", component="bag", value=shared),
            id="set",
        ),
        pytest.param(
            lambda shared: AppendToList(entity="a", component="nested", value=shared),
            id="append",
        ),
        pytest.param(
            lambda shared: ExtendList(
                entity="a", component="nested", values=(tuple(shared),)
            ),
            id="extend",
        ),
        pytest.param(
            lambda shared: CreateEntity(entity="n", components={"bag": shared}),
            id="create",
        ),
    ],
)
def test_no_operation_stores_the_caller_s_list(event_for):
    """A mutable payload is converted, not copied, so nothing can alias into state.

    Before, each operation had to remember to deep-copy what it stored, or two entities
    ended up sharing a list and appending to either changed both -- invisibly, and across
    rollback boundaries.
    """
    shared: list[str] = ["rope"]
    state = {"a": {"bag": None, "nested": ()}}

    result = Engine().simulate(state, event_for(shared))

    assert result.root.committed
    for components in result.entities.values():
        for value in components.values():
            assert not isinstance(value, list), "state must hold no mutable container"

    before = {entity: dict(c) for entity, c in result.entities.items()}
    shared.append("torch")

    assert result.entities == before, "mutating the payload must not reach the world"
    assert shared == ["rope", "torch"], "the caller keeps their own list"


def test_deleting_an_entity_does_not_hand_out_the_callers_own_components():
    """``DeleteEntity`` reported the live dict it popped, which under copy-on-write is the
    caller's own mapping for any entity nothing had written to yet.

    So an ``after`` rule reacting to a death -- reading ``components`` to drop what the
    thing was carrying, the reason the effect reports them at all -- could write straight
    into the input state.
    """
    looted: list[dict] = []

    class Loot(AfterRule[DeleteEntity]):
        name = "loot"

        def do(self, world, event, result):
            looted.append(result["components"])
            result["components"]["hp"] = 999
            return ()

    state = {"orc": {"hp": 5, "bag": ("gold",)}}

    result = Engine(rules=[Loot()]).simulate(state, DeleteEntity(entity="orc"))

    assert result.root.committed
    assert state == {"orc": {"hp": 5, "bag": ("gold",)}}, "the input must be untouched"
    assert looted[0]["hp"] == 999, "the reaction owns outright what it was handed"


def test_a_created_entity_does_not_alias_the_event_that_made_it():
    """Mutating the new entity used to mutate Event.data."""
    payload = {"bag": ["rope"]}
    event = CreateEntity(entity="orc", components=payload)

    class Fill(AfterRule[CreateEntity]):
        name = "fill"

        def do(self, world, event, result):
            yield SetComponent(entity="orc", component="bag", value=("torch",))

    result = Engine(rules=[Fill()]).simulate({}, event)

    assert result.entities["orc"]["bag"] == ("torch",)
    assert payload == {"bag": ["rope"]}
    assert event.components == {"bag": ["rope"]}


# ---------------------------------------------------------------------------
# Equal rules ran alphabetically, because the ordering fields were never assigned.
# ---------------------------------------------------------------------------


def test_registration_order_is_not_alphabetical_order():
    order: list[str] = []

    def rule(name):
        return type(
            "R",
            (BeforeRule,),
            {
                "name": name,
                "on": Trigger,
                "do": lambda self, world, event: order.append(name) or (),
            },
        )()

    Engine(rules=[rule("zebra"), rule("alpha")]).simulate({}, Trigger())

    assert order == ["zebra", "alpha"]


# ---------------------------------------------------------------------------
# A failing cascade returned the caller's own dict.
# ---------------------------------------------------------------------------


def test_a_wholly_failed_cascade_still_returns_an_owned_mapping():
    state = push_world(width=2)
    before = snapshot(state)

    result = Engine(rules=[WithinBounds(), Push(), Solid()]).simulate(
        state, MoveEntity(entity="player", destination=(1, 0))
    )

    assert not result.root.committed
    assert result.entities is not state
    assert state == before


# ---------------------------------------------------------------------------
# Unknown event names succeeded silently as no-ops.
# ---------------------------------------------------------------------------


def test_a_misspelled_event_cannot_be_constructed():
    """The old registry returned Effect() for any unrecognised name.

    Classes make the whole failure mode unreachable: there is no way to spell an event
    that does not exist. The dynamic escape hatch still validates.
    """
    from baffle import emit, lookup

    assert "increment_component" in registered()
    with pytest.raises(EngineFault, match="No event registered"):
        lookup("incremnt")
    with pytest.raises(EngineFault, match="No event registered"):
        emit("incremnt", entity="c", component="v", value=1)


def test_two_event_classes_cannot_claim_one_name():
    with pytest.raises(EngineFault, match="claim the name"):

        class Duplicate(Event):
            name = "increment_component"


# ---------------------------------------------------------------------------
# Depth bounded recursion but not total work.
# ---------------------------------------------------------------------------


def test_fan_out_cannot_run_away_within_the_depth_limit():
    """8191 events resolved in a single transaction at depth 12."""

    class Split(Event):
        name = "test.split_wide"
        depth: int = 0

    class Fan(BeforeRule[Split]):
        name = "fan"

        def do(self, world, event):
            if event.depth < 12:
                yield Split(depth=event.depth + 1)
                yield Split(depth=event.depth + 1)

    engine = Engine(rules=[Fan()], max_depth=64, max_events_per_transaction=500)
    with pytest.raises(EngineFault, match="work budget"):
        engine.simulate({}, Split())


# ---------------------------------------------------------------------------
# A fault mid-cascade discarded every transaction that had already committed.
# ---------------------------------------------------------------------------


def test_a_fault_mid_cascade_preserves_what_committed():
    class Corrupt(AfterRule[IncrementComponent]):
        name = "corrupt"

        def do(self, world, event, result):
            yield IncrementComponent(entity="label", component="text", value=1)

    state = {"counter": {"value": 0}, "label": {"text": "hi"}}
    engine = Engine(rules=[Corrupt()])

    with pytest.raises(EngineFault) as excinfo:
        engine.simulate(
            state, IncrementComponent(entity="counter", component="value", value=1)
        )

    partial = excinfo.value.partial
    assert partial is not None
    assert partial.entities["counter"]["value"] == 1
    assert len(partial.transactions) == 1
    assert partial.transactions[0].committed


def test_exhausting_the_transaction_budget_preserves_what_committed():
    class Forever(AfterRule[IncrementComponent]):
        name = "forever"

        def do(self, world, event, result):
            yield IncrementComponent(entity="counter", component="value", value=1)

    engine = Engine(rules=[Forever()], max_transactions=5)
    with pytest.raises(EngineFault) as excinfo:
        engine.simulate(
            {"counter": {"value": 0}},
            IncrementComponent(entity="counter", component="value", value=1),
        )

    assert excinfo.value.partial.entities["counter"]["value"] == 5


# ---------------------------------------------------------------------------
# SetComponent could not create a component.
# ---------------------------------------------------------------------------


def test_set_can_introduce_a_component():
    """This used to fault with "Cannot set undeclared component"."""
    result = Engine().simulate(
        {"counter": {}}, SetComponent(entity="counter", component="value", value=1)
    )

    assert result.root.committed
    assert result.entities["counter"]["value"] == 1


# ---------------------------------------------------------------------------
# MoveEntity hardcoded the entity id "grid" and faulted when it was absent.
# ---------------------------------------------------------------------------


def test_movement_does_not_require_a_world_entity():
    result = Engine([WithinBounds()]).simulate(
        {"player": {"position": (0, 0)}}, MoveEntity(entity="player", destination=(4, 4))
    )

    assert result.root.committed
    assert result.entities["player"]["position"] == (4, 4)


def test_bounds_come_from_the_reserved_world_entity():
    state = {WORLD: {"width": 2, "height": 2}, "player": {"position": (0, 0)}}

    result = Engine([WithinBounds()]).simulate(
        state, MoveEntity(entity="player", destination=(5, 5))
    )

    assert not result.root.committed
    assert result.root.failure is not None
    assert result.root.failure.reason == "outside_grid"


# ---------------------------------------------------------------------------
# Replacement chaining was an accident of priority ordering.
# ---------------------------------------------------------------------------


def test_replacement_does_not_silently_stop_chaining():
    """Chaining now depends on a declared constraint, not on two magic numbers.

    Reversing two priorities used to make the chain vanish with no error at all.
    """
    from baffle import ReplaceRule

    class First(Event):
        name = "test.chain_first"

    class Second(Event):
        name = "test.chain_second"

    class ToSecond(ReplaceRule[First]):
        name = "to-second"
        run_before = ("to-increment",)

        def do(self, world, event):
            return Second()

    class ToIncrement(ReplaceRule[Second]):
        name = "to-increment"

        def do(self, world, event):
            return IncrementComponent(entity="counter", component="value", value=1)

    for rules in ([ToSecond(), ToIncrement()], [ToIncrement(), ToSecond()]):
        result = Engine(rules=rules).simulate({"counter": {"value": 0}}, First())
        assert result.entities["counter"]["value"] == 1
