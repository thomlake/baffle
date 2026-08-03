from collections.abc import Iterable
from dataclasses import dataclass

import pytest

from baffle.engine import Engine
from baffle.events import Event, Rejected, Rejection, Set
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


def test_reaction_events_run_as_new_root_resolutions() -> None:
    def require_position(
        world: World,
        event: Move,
    ) -> Iterable[Event]:
        yield Set(event.entity, "position", event.destination)

    def mark_moved(
        world: World,
        event: Move,
    ) -> Iterable[Event]:
        yield Set(event.entity, "moved", True)

    engine = Engine(
        [
            RequireRule(Move, require_position),
            ReactRule(Move, mark_moved),
        ]
    )
    world = World(
        {
            "player": {
                "position": (0, 0),
            }
        }
    )
    move = Move("player", (1, 0))

    resolutions = engine.submit(world, move)

    assert world.snapshot() == {
        "player": {
            "position": (1, 0),
            "moved": True,
        }
    }
    assert [resolution.event for resolution in resolutions] == [
        move,
        Set("player", "moved", True),
    ]


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
    world = World(
        {
            "player": {
                "position": (0, 0),
            }
        }
    )

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
    world = World(
        {
            "player": {
                "position": (0, 0),
            }
        }
    )

    engine.submit(
        world,
        Move("player", (1, 0)),
    )

    assert observations == [(1, 0)]


def test_rejection_emits_one_rejected_event() -> None:
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

    resolutions = engine.submit(world, step)

    assert len(resolutions) == 1
    assert observed == [
        Rejected(
            root=step,
            event=move,
            rejection=Rejection("blocked"),
        )
    ]


def test_directly_rejected_root_reports_same_root_and_event() -> None:
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
    world = World({"player": {}})
    move = Move("player", (1, 0))

    engine.submit(world, move)

    assert observed == [
        Rejected(
            root=move,
            event=move,
            rejection=Rejection("blocked"),
        )
    ]


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
    world = World(
        {
            "player": {
                "health": 3,
            }
        }
    )

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
    world = World(
        {
            "player": {
                "health": 3,
            }
        }
    )

    engine.submit(
        world,
        Move("player", (1, 0)),
    )

    assert observed == []
    assert world.get("player", "health") == 3


def test_rejected_reaction_events_run_as_new_root_resolutions() -> None:
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

    engine = Engine(
        [
            RejectRule(Move, reject_move),
            ReactRule(Rejected, mark_blocked),
        ]
    )
    world = World({"player": {}})
    move = Move("player", (1, 0))

    resolutions = engine.submit(world, move)

    assert world.snapshot() == {
        "player": {
            "blocked": True,
        }
    }
    assert [resolution.event for resolution in resolutions] == [
        move,
        Set("player", "blocked", True),
    ]
    assert not resolutions[0].accepted
    assert resolutions[1].accepted


def test_reaction_roots_are_processed_fifo() -> None:
    observed: list[str] = []

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

    def observe(
        world: World,
        event: Marker,
    ) -> Iterable[Event]:
        observed.append(event.name)
        return ()

    engine = Engine(
        [
            ReactRule(Move, emit_siblings),
            ReactRule(Marker, emit_child),
            ReactRule(Marker, observe),
        ]
    )
    world = World({})
    move = Move("player", (1, 0))

    resolutions = engine.submit(world, move)

    assert [resolution.event for resolution in resolutions] == [
        move,
        Marker("a"),
        Marker("b"),
        Marker("c"),
    ]
    assert observed == ["a", "b", "c"]


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


def test_engine_accepts_rules_at_construction() -> None:
    observed: list[Move] = []

    def observe(
        world: World,
        event: Move,
    ) -> Iterable[Event]:
        observed.append(event)
        return ()

    engine = Engine(
        [
            ReactRule(Move, observe),
        ]
    )
    world = World({})
    move = Move("player", (1, 0))

    engine.submit(world, move)

    assert observed == [move]


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


def test_added_before_rules_preserve_shared_order() -> None:
    def reject_unmoved(
        world: World,
        event: Move,
    ) -> Rejection | None:
        if world.get(event.entity, "position") != event.destination:
            return Rejection("position_not_updated")

        return None

    def require_position(
        world: World,
        event: Move,
    ) -> Iterable[Event]:
        yield Set(event.entity, "position", event.destination)

    engine = Engine()
    engine.add(RejectRule(Move, reject_unmoved))
    engine.add(RequireRule(Move, require_position))

    initial = {
        "player": {
            "position": (0, 0),
        }
    }
    world = World(initial)

    resolutions = engine.submit(
        world,
        Move("player", (1, 0)),
    )

    assert not resolutions[0].accepted
    assert world.snapshot() == initial


def test_engine_rejects_unknown_rule_at_construction() -> None:
    with pytest.raises(TypeError, match="Unknown rule type"):
        Engine([object()])  # type: ignore[list-item]


def test_engine_rejects_unknown_rule_when_added() -> None:
    engine = Engine()

    with pytest.raises(TypeError, match="Unknown rule type"):
        engine.add(object())  # type: ignore[arg-type]
