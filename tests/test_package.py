import baffle


def test_public_names_are_importable() -> None:
    missing = [
        name
        for name in baffle.__all__
        if not hasattr(baffle, name)
    ]

    assert missing == []


def test_readme_example_runs() -> None:
    """Guard the documented entry point against export drift."""

    from collections.abc import Iterable
    from dataclasses import dataclass

    from baffle import (
        Engine,
        Event,
        Rejected,
        Rejection,
        RejectRule,
        RequireRule,
        ReactRule,
        ResolutionStatus,
        Set,
        World,
    )

    @dataclass(frozen=True)
    class Move(Event):
        entity: str
        destination: tuple[int, int]

    @dataclass(frozen=True)
    class EnterTile(Event):
        entity: str
        destination: tuple[int, int]

    def require_entry(world: World, event: Move) -> Iterable[Event]:
        yield EnterTile(
            entity=event.entity,
            destination=event.destination,
        )

    def reject_solid_tiles(
        world: World,
        event: EnterTile,
    ) -> Rejection | None:
        if event.destination == (1, 0):
            return Rejection("solid")

        return None

    def update_position(world: World, event: EnterTile) -> Iterable[Event]:
        yield Set(
            entity=event.entity,
            component="position",
            value=event.destination,
        )

    def record_failed_move(world: World, event: Rejected) -> Iterable[Event]:
        if isinstance(event.root, Move):
            yield Set(
                entity=event.root.entity,
                component="last_move_failed",
                value=True,
            )

    engine = Engine(
        [
            RequireRule(Move, require_entry),
            RejectRule(EnterTile, reject_solid_tiles),
            RequireRule(EnterTile, update_position),
            ReactRule(Rejected, record_failed_move),
        ]
    )

    world = World({"player": {"position": (0, 0)}})

    trace = engine.submit(world, Move("player", (1, 0)))

    assert trace.root.status is ResolutionStatus.ABORTED
    assert world.snapshot() == {
        "player": {
            "position": (0, 0),
            "last_move_failed": True,
        }
    }
