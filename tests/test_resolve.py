from collections.abc import Iterable
from dataclasses import dataclass

import pytest

from baffle.events import Event, Rejection, Set
from baffle.resolve import resolve
from baffle.rules import RejectRule, RequireRule
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
    transaction = resolve(
        {"player": {"health": 3}},
        Set("player", "health", 2),
    )

    assert transaction.committed
    assert transaction.state == {
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

    step = Step("player", (1, 0))
    move = Move("player", (1, 0))

    transaction = resolve(
        {"player": {"position": (0, 0)}},
        step,
        [
            RequireRule(Step, require_move),
            RequireRule(Move, require_position),
        ],
    )

    assert transaction.committed
    assert transaction.state["player"]["position"] == (1, 0)
    assert transaction.resolution.event == step
    assert transaction.resolution.children[0].event == move
    assert transaction.resolution.children[0].children[0].event == Set(
        "player",
        "position",
        (1, 0),
    )


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

    transaction = resolve(
        {"player": {"position": (0, 0)}},
        Move("player", (1, 0)),
        [
            RequireRule(Move, update_position),
            RejectRule(Move, reject_wrong_position),
        ],
    )

    assert transaction.committed
    assert transaction.state["player"]["position"] == (1, 0)


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

    transaction = resolve(
        initial,
        Move("player", (1, 0)),
        [
            RejectRule(Move, reject_wrong_position),
            RequireRule(Move, update_position),
        ],
    )

    assert not transaction.committed
    assert transaction.state == initial
    assert transaction.resolution.children == ()
    assert transaction.resolution.rejection == Rejection(
        "position_not_updated"
    )


def test_one_emitter_observes_one_world_version() -> None:
    observations = []

    def emit_updates(
        world: World,
        event: Move,
    ) -> Iterable[Event]:
        observations.append(world.get(event.entity, "health"))
        yield Set(event.entity, "health", 2)

        observations.append(world.get(event.entity, "health"))
        yield Set(event.entity, "health", 1)

    transaction = resolve(
        {"player": {"health": 3}},
        Move("player", (1, 0)),
        [RequireRule(Move, emit_updates)],
    )

    assert observations == [3, 3]
    assert transaction.state["player"]["health"] == 1


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

    move = Move("player", (1, 0))

    transaction = resolve(
        initial,
        move,
        [
            RequireRule(Move, spend_health),
            RejectRule(Move, reject_move),
        ],
    )

    assert not transaction.committed
    assert transaction.state == initial
    assert transaction.resolution.event == move
    assert transaction.resolution.rejection == Rejection("blocked")
    assert transaction.resolution.children[0].event == Set(
        "player",
        "health",
        2,
    )


def test_child_rejection_rejects_parent() -> None:
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

    step = Step("player", (1, 0))
    move = Move("player", (1, 0))

    transaction = resolve(
        {"player": {}},
        step,
        [
            RequireRule(Step, require_move),
            RejectRule(Move, reject_move),
        ],
    )

    assert not transaction.committed
    assert transaction.resolution.rejection == Rejection(
        reason="required_event_rejected",
        cause=Rejection("blocked"),
    )

    child = transaction.resolution.children[0]

    assert child.event == move
    assert child.rejection == Rejection("blocked")


def test_rules_match_event_subclasses() -> None:
    called: list[Move] = []

    def observe_move(
        world: World,
        event: Move,
    ) -> Iterable[Event]:
        called.append(event)
        return ()

    event = SpecialMove("player", (1, 0))

    transaction = resolve(
        {"player": {}},
        event,
        [RequireRule(Move, observe_move)],
    )

    assert transaction.committed
    assert called == [event]


def test_apply_exception_propagates() -> None:
    with pytest.raises(KeyError):
        resolve(
            {},
            Set("missing", "health", 3),
        )
