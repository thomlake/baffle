"""The record stream, checked by rendering it.

The engine emits structure and stays out of the phrasing business, so the only honest
test of "does the log carry enough" is to write a renderer and see whether the
transcript comes out. The two transcripts below are the ones the design set out to
produce.

This also exercises the direction-based authoring path: a ``Step`` carries a direction,
and a replace rule resolves it into a concrete ``MoveEntity`` while the world is at hand.
That is how movement stays readable without an event holding a state-dependent field.
"""

from __future__ import annotations

import pytest

from baffle import (
    WORLD,
    Attempt,
    BeforeRule,
    Engine,
    Event,
    Frame,
    MoveEntity,
    Replaced,
    ReplaceRule,
    RuleFired,
    TransactionBegin,
    TransactionEnd,
    Vec2,
    delta,
    shift,
)
from scenarios import Solid

WEST: Vec2 = (-1, 0)

DIRECTION_NAMES = {(-1, 0): "left", (1, 0): "right", (0, -1): "up", (0, 1): "down"}


class Step(Event):
    """Travel one square in a direction, resolved into a MoveEntity before anything runs."""

    name = "test.step"
    entity: str
    direction: Vec2


class ResolveStep(ReplaceRule[Step]):
    """Turn a direction into a destination, while state is available to do it."""

    name = "resolve-step"

    def do(self, world, event):
        return MoveEntity(
            entity=event.entity,
            destination=shift(world.vector(event.entity), event.direction),
        )


class Push(BeforeRule[MoveEntity]):
    """Displace a pushable obstruction, as a step in the same direction."""

    name = "push"
    run_before = ("solid",)

    def do(self, world, event):
        direction = delta(world.vector(event.entity), event.destination)
        for entity_id in world.query("pushable", position=event.destination):
            yield Step(entity=entity_id, direction=direction)


# ---------------------------------------------------------------------------
# A renderer, of the kind a game would own
# ---------------------------------------------------------------------------


def render(records) -> list[str]:
    lines: list[str] = []
    directions: dict[str, str] = {}

    for record in records:
        if isinstance(record, Replaced) and isinstance(record.before, Step):
            directions[record.before.entity] = DIRECTION_NAMES[record.before.direction]

        elif isinstance(record, Attempt) and isinstance(record.event, MoveEntity):
            entity = record.event.entity
            heading = directions.get(entity, "somewhere")
            lines.append(f"The {entity} attempted to move {heading}.")

        elif isinstance(record, RuleFired) and record.rule == "push":
            pushed = ", ".join(_subject(e) for e in record.produced)
            lines.append(f"The {_subject(record.event)} pushed the {pushed}.")

        elif isinstance(record, Frame) and isinstance(record.event, MoveEntity):
            entity = record.event.entity
            if record.succeeded:
                lines.append(f"The {entity} move succeeded.")
            else:
                lines.append(f"The {entity} move failed because {_because(record.failure)}.")

    return lines


def _subject(event) -> str:
    """The entity an event concerns. Records hold `Event`, so narrow before reading."""
    assert isinstance(event, MoveEntity | Step)
    return event.entity


def _because(failure) -> str:
    if failure.reason == "destination_obstructed":
        return f"it was blocked by the {failure.data['obstruction']}"
    if failure.reason == "required_event_failed":
        return f"the {failure.data['required_event'].entity} did not move"
    return failure.reason


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def blocked_world():
    """A crate with a wall behind it: the push cannot go through."""
    return {
        WORLD: {"width": 4, "height": 1},
        "player": {"position": (2, 0), "solid": True},
        "crate": {"position": (1, 0), "solid": True, "pushable": True},
        "wall": {"position": (0, 0), "solid": True},
    }


def open_world():
    """Room behind the crate."""
    return {
        WORLD: {"width": 4, "height": 1},
        "player": {"position": (2, 0), "solid": True},
        "crate": {"position": (1, 0), "solid": True, "pushable": True},
    }


def run(state):
    engine = Engine(rules=[ResolveStep(), Push(), Solid()], narrate=True)
    return engine.simulate(state, Step(entity="player", direction=WEST))


# ---------------------------------------------------------------------------
# The transcripts
# ---------------------------------------------------------------------------


def test_a_failed_push_reads_as_a_transcript():
    result = run(blocked_world())

    assert not result.root.committed
    assert render(result.records) == [
        "The player attempted to move left.",
        "The player pushed the crate.",
        "The crate attempted to move left.",
        "The crate move failed because it was blocked by the wall.",
        "The player move failed because the crate did not move.",
    ]


def test_a_successful_push_reads_as_a_transcript():
    result = run(open_world())

    assert result.root.committed
    assert render(result.records) == [
        "The player attempted to move left.",
        "The player pushed the crate.",
        "The crate attempted to move left.",
        "The crate move succeeded.",
        "The player move succeeded.",
    ]


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------


def test_attempts_are_preorder_and_frames_are_postorder():
    """A flat postorder frame list cannot produce the transcripts above."""
    records = list(run(open_world()).records)
    attempts = [_subject(r.event) for r in records if isinstance(r, Attempt)]
    frames = [_subject(r.event) for r in records if isinstance(r, Frame)]

    assert attempts == ["player", "crate"]
    assert frames == ["crate", "player"]


def test_a_transaction_is_bracketed():
    records = list(run(open_world()).records)

    assert isinstance(records[0], TransactionBegin)
    ends = [r for r in records if isinstance(r, TransactionEnd)]
    assert len(ends) == 1 and ends[0].committed


def test_the_brackets_survive_a_rollback():
    """Marking the span must not tar the bookends, or a transcript loses its frame."""
    records = list(run(blocked_world()).records)

    assert not records[0].rolled_back
    end = next(r for r in records if isinstance(r, TransactionEnd))
    assert not end.rolled_back
    assert not end.committed


def test_narration_is_dropped_when_disabled():
    """The search path pays for mutations only."""
    engine = Engine(rules=[ResolveStep(), Push(), Solid()], narrate=False)
    result = engine.simulate(open_world(), Step(entity="player", direction=WEST))

    assert result.root.committed
    assert render(result.records) == []
    assert [type(r).__name__ for r in result.records] == ["Mutation", "Mutation"]


def test_replacement_is_recorded_with_its_author():
    records = list(run(open_world()).records)
    replacements = [r for r in records if isinstance(r, Replaced)]

    assert [r.by_rule for r in replacements] == ["resolve-step", "resolve-step"]
    assert isinstance(replacements[0].before, Step)
    assert isinstance(replacements[0].after, MoveEntity)


@pytest.mark.parametrize("state_factory", [open_world, blocked_world])
def test_every_record_carries_a_rollback_flag(state_factory):
    """The renderer reads it; the hasher filters on it."""
    for record in run(state_factory()).records:
        assert isinstance(record.rolled_back, bool)
