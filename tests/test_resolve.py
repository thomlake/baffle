from collections.abc import Iterable
from dataclasses import dataclass

import pytest

from baffle.events import Event, Rejection, Set
from baffle.resolve import resolve, Resolution
from baffle.rules import ReactRule, RejectRule, RequireRule
from baffle.world import World


@dataclass(frozen=True)
class Move(Event):
    entity: str
    destination: tuple[int, int]


@dataclass(frozen=True)
class SpecialMove(Move):
    pass


@dataclass(frozen=True)
class Step(Event):
    entity: str
    destination: tuple[int, int]


def test_event_applies_when_accepted() -> None:
    world = World({"player": {"health": 3}})

    resolution = resolve(
        world,
        Set("player", "health", 2),
    )

    assert resolution.accepted
    assert not resolution.rejected
    assert resolution.rejection is None
    assert world.snapshot() == {
        "player": {
            "health": 2,
        }
    }


def test_required_events_resolve_before_parent() -> None:
    def require_move(
        world: World,
        event: Step,
    ) -> Iterable[Event]:
        yield Move(event.entity, event.destination)

    def require_position(
        world: World,
        event: Move,
    ) -> Iterable[Event]:
        yield Set(
            event.entity,
            "position",
            event.destination,
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

    resolution = resolve(
        world,
        step,
        [
            RequireRule(Step, require_move),
            RequireRule(Move, require_position),
        ],
    )

    assert resolution.accepted
    assert world.get("player", "position") == (1, 0)

    assert resolution.event == step
    assert resolution.children[0].event == move
    assert resolution.children[0].children[0].event == set_position


def test_later_rules_see_previous_required_events() -> None:
    def update_position(
        world: World,
        event: Move,
    ) -> Iterable[Event]:
        yield Set(
            event.entity,
            "position",
            event.destination,
        )

    def reject_wrong_position(
        world: World,
        event: Move,
    ) -> Rejection | None:
        if world.get(event.entity, "position") != event.destination:
            return Rejection("position_not_updated")

        return None

    world = World(
        {
            "player": {
                "position": (0, 0),
            }
        }
    )

    resolution = resolve(
        world,
        Move("player", (1, 0)),
        [
            RequireRule(Move, update_position),
            RejectRule(Move, reject_wrong_position),
        ],
    )

    assert resolution.accepted
    assert world.get("player", "position") == (1, 0)


def test_rule_order_is_shared_across_require_and_reject() -> None:
    def reject_wrong_position(
        world: World,
        event: Move,
    ) -> Rejection | None:
        if world.get(event.entity, "position") != event.destination:
            return Rejection("position_not_updated")

        return None

    def update_position(
        world: World,
        event: Move,
    ) -> Iterable[Event]:
        yield Set(
            event.entity,
            "position",
            event.destination,
        )

    initial = {
        "player": {
            "position": (0, 0),
        }
    }
    world = World(initial)

    resolution = resolve(
        world,
        Move("player", (1, 0)),
        [
            RejectRule(Move, reject_wrong_position),
            RequireRule(Move, update_position),
        ],
    )

    assert not resolution.accepted
    assert resolution.rejected
    assert resolution.rejection == Rejection("position_not_updated")
    assert resolution.children == ()
    assert world.snapshot() == initial


def test_one_rule_observes_one_world_version() -> None:
    observations: list[int] = []

    def update_health(
        world: World,
        event: Move,
    ) -> Iterable[Event]:
        health = world.get(event.entity, "health")
        assert isinstance(health, int)
        observations.append(health)

        yield Set(event.entity, "health", 2)

        health = world.get(event.entity, "health")
        assert isinstance(health, int)
        observations.append(health)

        yield Set(event.entity, "health", 1)

    world = World(
        {
            "player": {
                "health": 3,
            }
        }
    )

    resolution = resolve(
        world,
        Move("player", (1, 0)),
        [RequireRule(Move, update_health)],
    )

    assert resolution.accepted
    assert observations == [3, 3]
    assert world.get("player", "health") == 1


def test_direct_rejection_discards_required_changes() -> None:
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

    initial = {
        "player": {
            "health": 3,
        }
    }
    world = World(initial)
    move = Move("player", (1, 0))

    resolution = resolve(
        world,
        move,
        [
            RequireRule(Move, spend_health),
            RejectRule(Move, reject_move),
        ],
    )

    assert not resolution.accepted
    assert resolution.rejected
    assert resolution.rejection == Rejection("blocked")
    assert world.snapshot() == initial

    assert resolution.children == (
        Resolution(
            event=Set("player", "health", 2),
        ),
    )


def test_child_rejection_makes_parent_unsuccessful() -> None:
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

    initial = {"player": {}}
    world = World(initial)

    step = Step("player", (1, 0))
    move = Move("player", (1, 0))

    resolution = resolve(
        world,
        step,
        [
            RequireRule(Step, require_move),
            RejectRule(Move, reject_move),
        ],
    )

    assert not resolution.accepted
    assert not resolution.rejected
    assert resolution.rejection is None
    assert world.snapshot() == initial

    assert len(resolution.children) == 1

    child = resolution.children[0]

    assert child.event == move
    assert not child.accepted
    assert child.rejected
    assert child.rejection == Rejection("blocked")


def test_requirements_after_rejection_are_not_attempted() -> None:
    attempted: list[Event] = []

    @dataclass(frozen=True)
    class First(Event):
        pass

    @dataclass(frozen=True)
    class Second(Event):
        pass

    def require_children(
        world: World,
        event: Step,
    ) -> Iterable[Event]:
        yield First()
        yield Second()

    def reject_first(
        world: World,
        event: First,
    ) -> Rejection:
        attempted.append(event)
        return Rejection("blocked")

    def observe_second(
        world: World,
        event: Second,
    ) -> Iterable[Event]:
        attempted.append(event)
        return ()

    world = World({})

    resolution = resolve(
        world,
        Step("player", (1, 0)),
        [
            RequireRule(Step, require_children),
            RejectRule(First, reject_first),
            RequireRule(Second, observe_second),
        ],
    )

    assert not resolution.accepted
    assert attempted == [First()]
    assert [child.event for child in resolution.children] == [First()]


def test_rules_match_event_subclasses() -> None:
    called: list[Move] = []

    def observe_move(
        world: World,
        event: Move,
    ) -> Iterable[Event]:
        called.append(event)
        return ()

    world = World({"player": {}})
    event = SpecialMove("player", (1, 0))

    resolution = resolve(
        world,
        event,
        [RequireRule(Move, observe_move)],
    )

    assert resolution.accepted
    assert called == [event]


def test_apply_exception_propagates_without_committing() -> None:
    world = World({})

    with pytest.raises(KeyError):
        resolve(
            world,
            Set("missing", "health", 3),
        )

    assert world.snapshot() == {}


def test_resolve_rejects_react_rules() -> None:
    def react(
        world: World,
        event: Move,
    ) -> Iterable[Event]:
        return ()

    world = World({"player": {}})

    with pytest.raises(
        TypeError,
        match="accepts only RequireRule and RejectRule",
    ):
        resolve(
            world,
            Move("player", (1, 0)),
            [ReactRule(Move, react)],  # type: ignore[list-item]
        )


def test_resolve_rejects_unknown_rule_types() -> None:
    world = World({"player": {}})

    with pytest.raises(
        TypeError,
        match="accepts only RequireRule and RejectRule",
    ):
        resolve(
            world,
            Move("player", (1, 0)),
            [object()],  # type: ignore[list-item]
        )
