from dataclasses import FrozenInstanceError, dataclass

import pytest

from baffle.events import (
    Create,
    Delete,
    Event,
    Rejected,
    Rejection,
    Set,
)
from baffle.world import World


@dataclass(frozen=True)
class Move(Event):
    entity: str
    destination: tuple[int, int]


def test_events_are_values() -> None:
    move = Move("player", (2, 1))

    assert move == Move("player", (2, 1))
    assert hash(move) == hash(Move("player", (2, 1)))


def test_events_are_immutable() -> None:
    move = Move("player", (2, 1))

    with pytest.raises(FrozenInstanceError):
        move.entity = "crate"  # type: ignore[misc]


def test_rejection_retains_reason() -> None:
    rejection = Rejection("blocked")

    assert rejection.reason == "blocked"


def test_rejected_retains_root_event_and_rejection() -> None:
    root = Move("player", (2, 1))
    event = Set("player", "position", (2, 1))
    rejection = Rejection("blocked")

    rejected = Rejected(
        root=root,
        event=event,
        rejection=rejection,
    )

    assert rejected.root is root
    assert rejected.event is event
    assert rejected.rejection is rejection


def test_rejected_does_not_change_world() -> None:
    world = World({"player": {"health": 3}})

    Rejected(
        root=Move("player", (2, 1)),
        event=Set("player", "position", (2, 1)),
        rejection=Rejection("blocked"),
    ).apply(world)

    assert world.snapshot() == {
        "player": {
            "health": 3,
        }
    }


def test_create_applies_to_world() -> None:
    world = World({})

    Create(
        entity="player",
        components={
            "health": 3,
            "position": (1, 2),
        },
    ).apply(world)

    assert world.snapshot() == {
        "player": {
            "health": 3,
            "position": (1, 2),
        }
    }


def test_delete_applies_to_world() -> None:
    world = World({"player": {"health": 3}})

    Delete("player").apply(world)

    assert world.snapshot() == {}


def test_set_applies_to_world() -> None:
    world = World({"player": {"health": 3}})

    Set("player", "health", 2).apply(world)

    assert world.get("player", "health") == 2
