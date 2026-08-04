from collections.abc import Iterable
from dataclasses import dataclass

import pytest

from baffle.engine import Engine, Reaction, Trace
from baffle.events import Event, Rejected, Rejection, Set
from baffle.resolve import (
    ResolutionLimitError,
    ResolutionStatus,
    ResolverConfig,
)
from baffle.rules import ReactRule, RejectRule, RequireRule
from baffle.world import World


@dataclass(frozen=True)
class Move(Event):
    entity: str
    destination: tuple[int, int]


@dataclass(frozen=True)
class Step(Event):
    entity: str
    destination: tuple[int, int]


@dataclass(frozen=True)
class Marker(Event):
    name: str


def test_submit_returns_trace_with_external_root_first() -> None:
    engine = Engine()
    world = World({})
    move = Move("player", (1, 0))

    trace = engine.submit(world, move)

    assert isinstance(trace, Trace)
    assert len(trace.entries) == 1
    assert trace.entries[0].reaction is None
    assert trace.entries[0].resolution.event == move
    assert trace.root is trace.entries[0].resolution
    assert trace.root.status is ResolutionStatus.ACCEPTED


def test_reaction_event_records_rule_and_source() -> None:
    def mark_moved(
        world: World,
        event: Move,
    ) -> Iterable[Event]:
        yield Set(event.entity, "moved", True)

    reaction_rule = ReactRule(Move, mark_moved)
    engine = Engine([reaction_rule])
    world = World({"player": {}})
    move = Move("player", (1, 0))

    trace = engine.submit(world, move)

    assert len(trace.entries) == 2

    reaction_entry = trace.entries[1]

    assert reaction_entry.resolution.event == Set(
        "player",
        "moved",
        True,
    )
    assert reaction_entry.reaction == Reaction(
        rule=reaction_rule,
        source=move,
    )


def test_accepted_events_react_child_before_parent() -> None:
    observed: list[Event] = []

    def require_move(
        world: World,
        event: Step,
    ) -> Iterable[Event]:
        yield Move(event.entity, event.destination)

    def require_position(
        world: World,
        event: Move,
    ) -> Iterable[Event]:
        yield Set(event.entity, "position", event.destination)

    def observe(
        world: World,
        event: Event,
    ) -> Iterable[Event]:
        observed.append(event)
        return ()

    engine = Engine(
        [
            RequireRule(Step, require_move),
            RequireRule(Move, require_position),
            ReactRule(Event, observe),
        ]
    )
    world = World({"player": {"position": (0, 0)}})

    step = Step("player", (1, 0))
    move = Move("player", (1, 0))
    set_position = Set("player", "position", (1, 0))

    engine.submit(world, step)

    assert observed == [
        set_position,
        move,
        step,
    ]


def test_reactions_observe_committed_state() -> None:
    observations: list[tuple[int, int]] = []

    def require_position(
        world: World,
        event: Move,
    ) -> Iterable[Event]:
        yield Set(event.entity, "position", event.destination)

    def observe(
        world: World,
        event: Move,
    ) -> Iterable[Event]:
        position = world.get(event.entity, "position")
        assert isinstance(position, tuple)
        observations.append(position)
        return ()

    engine = Engine(
        [
            RequireRule(Move, require_position),
            ReactRule(Move, observe),
        ]
    )
    world = World({"player": {"position": (0, 0)}})

    engine.submit(
        world,
        Move("player", (1, 0)),
    )

    assert observations == [(1, 0)]


def test_rejection_emits_one_rejected_event_with_root_and_cause() -> None:
    observed: list[Rejected] = []

    def require_move(
        world: World,
        event: Step,
    ) -> Iterable[Event]:
        yield Move(event.entity, event.destination)

    def reject_move(
        world: World,
        event: Move,
    ) -> Rejection:
        return Rejection("blocked")

    def observe(
        world: World,
        event: Rejected,
    ) -> Iterable[Event]:
        observed.append(event)
        return ()

    engine = Engine(
        [
            RequireRule(Step, require_move),
            RejectRule(Move, reject_move),
            ReactRule(Rejected, observe),
        ]
    )
    world = World({"player": {}})

    step = Step("player", (1, 0))
    move = Move("player", (1, 0))

    trace = engine.submit(world, step)

    assert trace.root.status is ResolutionStatus.ABORTED
    assert observed == [
        Rejected(
            root=step,
            event=move,
            rejection=Rejection("blocked"),
        )
    ]


def test_directly_rejected_root_has_rejected_status() -> None:
    def reject_move(
        world: World,
        event: Move,
    ) -> Rejection:
        return Rejection("blocked")

    engine = Engine(
        [
            RejectRule(Move, reject_move),
        ]
    )
    world = World({"player": {}})
    move = Move("player", (1, 0))

    trace = engine.submit(world, move)

    assert trace.root.status is ResolutionStatus.REJECTED
    assert trace.root.rejection == Rejection("blocked")


def test_rejected_reaction_records_rejected_as_source() -> None:
    def reject_move(
        world: World,
        event: Move,
    ) -> Rejection:
        return Rejection("blocked")

    def mark_blocked(
        world: World,
        event: Rejected,
    ) -> Iterable[Event]:
        yield Set("player", "blocked", True)

    reaction_rule = ReactRule(Rejected, mark_blocked)
    engine = Engine(
        [
            RejectRule(Move, reject_move),
            reaction_rule,
        ]
    )
    world = World({"player": {}})
    move = Move("player", (1, 0))

    trace = engine.submit(world, move)

    expected_source = Rejected(
        root=move,
        event=move,
        rejection=Rejection("blocked"),
    )

    assert trace.entries[1].reaction == Reaction(
        rule=reaction_rule,
        source=expected_source,
    )
    assert world.get("player", "blocked") is True


def test_rejection_reactions_observe_rolled_back_state() -> None:
    observations: list[int] = []

    def spend_health(
        world: World,
        event: Move,
    ) -> Iterable[Event]:
        yield Set(event.entity, "health", 2)

    def reject_move(
        world: World,
        event: Move,
    ) -> Rejection:
        return Rejection("blocked")

    def observe(
        world: World,
        event: Rejected,
    ) -> Iterable[Event]:
        health = world.get("player", "health")
        assert isinstance(health, int)
        observations.append(health)
        return ()

    engine = Engine(
        [
            RequireRule(Move, spend_health),
            RejectRule(Move, reject_move),
            ReactRule(Rejected, observe),
        ]
    )
    world = World({"player": {"health": 3}})

    engine.submit(
        world,
        Move("player", (1, 0)),
    )

    assert observations == [3]
    assert world.get("player", "health") == 3


def test_rolled_back_events_do_not_trigger_accepted_reactions() -> None:
    observed: list[Set] = []

    def spend_health(
        world: World,
        event: Move,
    ) -> Iterable[Event]:
        yield Set(event.entity, "health", 2)

    def reject_move(
        world: World,
        event: Move,
    ) -> Rejection:
        return Rejection("blocked")

    def observe_set(
        world: World,
        event: Set,
    ) -> Iterable[Event]:
        observed.append(event)
        return ()

    engine = Engine(
        [
            RequireRule(Move, spend_health),
            RejectRule(Move, reject_move),
            ReactRule(Set, observe_set),
        ]
    )
    world = World({"player": {"health": 3}})

    engine.submit(
        world,
        Move("player", (1, 0)),
    )

    assert observed == []


def test_reaction_roots_are_processed_fifo() -> None:
    def emit_siblings(
        world: World,
        event: Move,
    ) -> Iterable[Event]:
        yield Marker("a")
        yield Marker("b")

    def emit_child(
        world: World,
        event: Marker,
    ) -> Iterable[Event]:
        if event.name == "a":
            yield Marker("c")

    engine = Engine(
        [
            ReactRule(Move, emit_siblings),
            ReactRule(Marker, emit_child),
        ]
    )
    world = World({})
    move = Move("player", (1, 0))

    trace = engine.submit(world, move)

    assert [
        entry.resolution.event
        for entry in trace.entries
    ] == [
        move,
        Marker("a"),
        Marker("b"),
        Marker("c"),
    ]


def test_all_reactions_to_one_resolution_observe_same_state() -> None:
    observations: list[bool] = []

    def first(
        world: World,
        event: Move,
    ) -> Iterable[Event]:
        observations.append(
            bool(world.get("player", "marked", default=False))
        )
        yield Set("player", "marked", True)

    def second(
        world: World,
        event: Move,
    ) -> Iterable[Event]:
        observations.append(
            bool(world.get("player", "marked", default=False))
        )
        return ()

    engine = Engine(
        [
            ReactRule(Move, first),
            ReactRule(Move, second),
        ]
    )
    world = World({"player": {}})

    engine.submit(
        world,
        Move("player", (1, 0)),
    )

    assert observations == [False, False]
    assert world.get("player", "marked") is True


def test_engine_event_budget_spans_reaction_roots() -> None:
    def emit_marker(
        world: World,
        event: Move,
    ) -> Iterable[Event]:
        yield Marker("reaction")

    engine = Engine(
        [ReactRule(Move, emit_marker)],
        resolver_config=ResolverConfig(max_events=1),
    )
    world = World({})
    move = Move("player", (1, 0))

    with pytest.raises(
        ResolutionLimitError,
        match="Maximum event count exceeded: 1",
    ):
        engine.submit(world, move)


def test_engine_gets_fresh_budget_for_each_submission() -> None:
    engine = Engine(
        resolver_config=ResolverConfig(max_events=1),
    )
    world = World({})

    first = engine.submit(world, Marker("first"))
    second = engine.submit(world, Marker("second"))

    assert first.root.status is ResolutionStatus.ACCEPTED
    assert second.root.status is ResolutionStatus.ACCEPTED


def test_rule_can_be_added_after_construction() -> None:
    observed: list[Move] = []

    def observe(
        world: World,
        event: Move,
    ) -> Iterable[Event]:
        observed.append(event)
        return ()

    engine = Engine()
    engine.add(ReactRule(Move, observe))

    world = World({})
    move = Move("player", (1, 0))

    engine.submit(world, move)

    assert observed == [move]


def test_engine_rejects_unknown_rule_at_construction() -> None:
    with pytest.raises(TypeError, match="Unknown rule type"):
        Engine([object()])  # type: ignore[list-item]


def test_engine_rejects_unknown_rule_when_added() -> None:
    engine = Engine()

    with pytest.raises(TypeError, match="Unknown rule type"):
        engine.add(object())  # type: ignore[arg-type]


def test_external_root_has_no_parent() -> None:
    engine = Engine()
    world = World({})

    trace = engine.submit(world, Move("player", (1, 0)))

    assert trace.entries[0].parent is None


def test_reaction_roots_record_parent_entry() -> None:
    def emit_siblings(
        world: World,
        event: Move,
    ) -> Iterable[Event]:
        yield Marker("a")
        yield Marker("b")

    def emit_child(
        world: World,
        event: Marker,
    ) -> Iterable[Event]:
        if event.name == "a":
            yield Marker("c")

    engine = Engine(
        [
            ReactRule(Move, emit_siblings),
            ReactRule(Marker, emit_child),
        ]
    )
    world = World({})

    trace = engine.submit(world, Move("player", (1, 0)))

    # move -> [a, b], a -> [c]
    assert [entry.parent for entry in trace.entries] == [None, 0, 0, 1]


def test_parent_disambiguates_equal_events_from_different_roots() -> None:
    def emit_marker(
        world: World,
        event: Move,
    ) -> Iterable[Event]:
        yield Marker("shared")

    def emit_move(
        world: World,
        event: Step,
    ) -> Iterable[Event]:
        yield Move(event.entity, event.destination)

    engine = Engine(
        [
            ReactRule(Step, emit_move),
            ReactRule(Move, emit_marker),
        ]
    )
    world = World({})

    trace = engine.submit(world, Step("player", (1, 0)))

    # Both Move roots emit an identical Marker, so only the parent index
    # distinguishes which transaction produced which.
    assert [entry.resolution.event for entry in trace.entries] == [
        Step("player", (1, 0)),
        Move("player", (1, 0)),
        Marker("shared"),
    ]
    assert [entry.parent for entry in trace.entries] == [None, 0, 1]


def test_limit_error_carries_committed_roots_and_failing_event() -> None:
    def cascade(
        world: World,
        event: Marker,
    ) -> Iterable[Event]:
        yield Marker(event.name + "!")

    engine = Engine(
        [ReactRule(Marker, cascade)],
        resolver_config=ResolverConfig(max_events=3),
    )
    world = World({})

    with pytest.raises(ResolutionLimitError) as error:
        engine.submit(world, Marker("a"))

    assert error.value.event == Marker("a!!!")

    trace = error.value.trace
    assert trace is not None
    assert [entry.resolution.event for entry in trace.entries] == [
        Marker("a"),
        Marker("a!"),
        Marker("a!!"),
    ]


def test_limit_error_trace_reflects_committed_state() -> None:
    def cascade(
        world: World,
        event: Marker,
    ) -> Iterable[Event]:
        yield Set("counter", event.name, True)
        yield Marker(event.name + "!")

    engine = Engine(
        [ReactRule(Marker, cascade)],
        resolver_config=ResolverConfig(max_events=4),
    )
    world = World({"counter": {}})

    with pytest.raises(ResolutionLimitError) as error:
        engine.submit(world, Marker("a"))

    trace = error.value.trace
    assert trace is not None

    committed = {
        entry.resolution.event
        for entry in trace.entries
        if isinstance(entry.resolution.event, Set)
    }

    # Every committed change is accounted for by an entry in the trace.
    assert committed == {
        Set("counter", "a", True),
        Set("counter", "a!", True),
    }
    assert world.snapshot() == {"counter": {"a": True, "a!": True}}


def test_engine_rejects_require_rule_on_rejected() -> None:
    def require_anything(
        world: World,
        event: Rejected,
    ) -> Iterable[Event]:
        return ()

    with pytest.raises(TypeError, match="Rejected is observation-only"):
        Engine([RequireRule(Rejected, require_anything)])


def test_engine_rejects_reject_rule_on_rejected() -> None:
    def reject_anything(
        world: World,
        event: Rejected,
    ) -> Rejection | None:
        return None

    with pytest.raises(TypeError, match="Rejected is observation-only"):
        Engine([RejectRule(Rejected, reject_anything)])


def test_engine_rejects_before_rule_on_rejected_subclass() -> None:
    @dataclass(frozen=True)
    class Vetoed(Rejected):
        pass

    def require_anything(
        world: World,
        event: Vetoed,
    ) -> Iterable[Event]:
        return ()

    with pytest.raises(TypeError, match="Rejected is observation-only"):
        Engine([RequireRule(Vetoed, require_anything)])


def test_engine_allows_react_rule_on_rejected() -> None:
    observed: list[Rejected] = []

    def reject_move(
        world: World,
        event: Move,
    ) -> Rejection:
        return Rejection("blocked")

    def observe(
        world: World,
        event: Rejected,
    ) -> Iterable[Event]:
        observed.append(event)
        return ()

    engine = Engine(
        [
            RejectRule(Move, reject_move),
            ReactRule(Rejected, observe),
        ]
    )
    move = Move("player", (1, 0))

    engine.submit(World({}), move)

    assert observed == [
        Rejected(
            root=move,
            event=move,
            rejection=Rejection("blocked"),
        )
    ]


def test_engine_allows_catch_all_before_rule() -> None:
    """Event is not a subclass of Rejected, so catch-alls are not dead."""

    observed: list[Event] = []

    def observe(
        world: World,
        event: Event,
    ) -> Iterable[Event]:
        observed.append(event)
        return ()

    engine = Engine([RequireRule(Event, observe)])
    move = Move("player", (1, 0))

    engine.submit(World({}), move)

    assert observed == [move]
