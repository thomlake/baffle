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
        Event,
        Rejected,
        Rejection,
        ResolutionStatus,
        Ruleset,
        Set,
        World,
        react,
        reject,
        require,
        submit,
    )

    @dataclass(frozen=True)
    class Move(Event):
        entity: str
        destination: tuple[int, int]

    @dataclass(frozen=True)
    class EnterTile(Event):
        entity: str
        destination: tuple[int, int]

    @require
    def require_entry(world: World, event: Move) -> Iterable[Event]:
        yield EnterTile(
            entity=event.entity,
            destination=event.destination,
        )

    @reject
    def reject_solid_tiles(
        world: World,
        event: EnterTile,
    ) -> Rejection | None:
        if event.destination == (1, 0):
            return Rejection("solid")

        return None

    @require(after=("reject_solid_tiles",))
    def update_position(world: World, event: EnterTile) -> Iterable[Event]:
        yield Set(
            entity=event.entity,
            component="position",
            value=event.destination,
        )

    @react
    def record_failed_move(world: World, event: Rejected) -> Iterable[Event]:
        if isinstance(event.root, Move):
            yield Set(
                entity=event.root.entity,
                component="last_move_failed",
                value=True,
            )

    ruleset = Ruleset(
        [require_entry, reject_solid_tiles, update_position, record_failed_move]
    )

    world = World({"player": {"position": (0, 0)}})

    trace = submit(world, Move("player", (1, 0)), ruleset)

    assert trace.root.status is ResolutionStatus.ABORTED
    assert world.snapshot() == {
        "player": {
            "position": (0, 0),
            "last_move_failed": True,
        }
    }
