from collections.abc import Iterable
from dataclasses import dataclass

from baffle.events import Event, Rejection, Set
from baffle.rules import ReactRule, RejectRule, RequireRule
from baffle.world import World


@dataclass(frozen=True)
class Move(Event):
    entity: str
    destination: tuple[int, int]


def require_position(world: World, event: Move) -> Iterable[Event]:
    yield Set(event.entity, "position", event.destination)


def reject_blocked_move(world: World, event: Move) -> Rejection | None:
    if world.get(event.entity, "blocked", default=False):
        return Rejection("blocked")

    return None


def react_to_move(world: World, event: Move) -> Iterable[Event]:
    return ()


def test_require_rule_stores_event_type_and_emitter() -> None:
    rule = RequireRule(Move, require_position)

    emitted = tuple(
        rule.run(
            World({"player": {}}),
            Move("player", (1, 2)),
        )
    )

    assert rule.event_type is Move
    assert emitted == (
        Set("player", "position", (1, 2)),
    )


def test_reject_rule_returns_rejection() -> None:
    rule = RejectRule(Move, reject_blocked_move)
    move = Move("player", (1, 2))

    rejection = rule.run(
        World({"player": {"blocked": True}}),
        move,
    )

    assert rule.event_type is Move
    assert rejection == Rejection("blocked")


def test_reject_rule_may_allow_event() -> None:
    rule = RejectRule(Move, reject_blocked_move)
    move = Move("player", (1, 2))

    rejection = rule.run(
        World({"player": {"blocked": False}}),
        move,
    )

    assert rejection is None


def test_react_rule_stores_event_type_and_emitter() -> None:
    rule = ReactRule(Move, react_to_move)

    emitted = tuple(
        rule.run(
            World({}),
            Move("player", (1, 2)),
        )
    )

    assert rule.event_type is Move
    assert emitted == ()
