from collections.abc import Iterable
from dataclasses import dataclass

import pytest

from baffle.engine import submit
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

    world = World(
        {
            "player": {
                "position": (0, 0),
            }
        }
    )
    move = Move("player", (1, 0))

    resolutions = submit(
        world,
        move,
        [
            RequireRule(Move, require_position),
            ReactRule(Move, mark_moved),
        ],
    )

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

    submit(
        world,
        step,
        [
            RequireRule(Step, require_move),
            RequireRule(Move, require_position),
            ReactRule(Event, observe),
        ],
    )

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

    world = World(
        {
            "player": {
                "position": (0, 0),
            }
        }
    )

    submit(
        world,
        Move("player", (1, 0)),
        [
            RequireRule(Move, require_position),
            ReactRule(Move, observe),
        ],
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

    world = World({"player": {}})

    step = Step("player", (1, 0))
    move = Move("player", (1, 0))

    resolutions = submit(
        world,
        step,
        [
            RequireRule(Step, require_move),
            RejectRule(Move, reject_move),
            ReactRule(Rejected, observe),
        ],
    )

    assert len(resolutions) == 1
    assert observed == [
        Rejected(
            root=step,
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

    world = World(
        {
            "player": {
                "health": 3,
            }
        }
    )

    submit(
        world,
        Move("player", (1, 0)),
        [
            RequireRule(Move, spend_health),
            RejectRule(Move, reject_move),
            ReactRule(Rejected, observe),
        ],
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

    world = World(
        {
            "player": {
                "health": 3,
            }
        }
    )

    submit(
        world,
        Move("player", (1, 0)),
        [
            RequireRule(Move, spend_health),
            RejectRule(Move, reject_move),
            ReactRule(Set, observe_set),
        ],
    )

    assert observed == []


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

    world = World({})

    resolutions = submit(
        world,
        Move("player", (1, 0)),
        [
            ReactRule(Move, emit_siblings),
            ReactRule(Marker, emit_child),
            ReactRule(Marker, observe),
        ],
    )

    assert [resolution.event for resolution in resolutions] == [
        Move("player", (1, 0)),
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

    world = World({"player": {}})

    submit(
        world,
        Move("player", (1, 0)),
        [
            ReactRule(Move, first),
            ReactRule(Move, second),
        ],
    )

    assert observations == [False, False]
    assert world.get("player", "marked") is True


def test_submit_rejects_unknown_rule_types() -> None:
    world = World({})

    with pytest.raises(
        TypeError,
        match="Unknown rule type",
    ):
        submit(
            world,
            Move("player", (1, 0)),
            [object()],  # type: ignore[list-item]
        )
