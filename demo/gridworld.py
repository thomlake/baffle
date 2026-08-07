from dataclasses import dataclass
from enum import StrEnum
from typing import cast

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


type Vec2 = tuple[int, int]

def scale(a: int, x: Vec2) -> Vec2:
    return a*x[0], a*x[1]


def _get_sign(a: int):
    if a > 0:
        return 1
    elif a < 0:
        return -1
    else:
        return 0


def direction(a: Vec2) -> Vec2:
    return _get_sign(a[0]), _get_sign(a[1])


def add(a: Vec2, b: Vec2) -> Vec2:
    return a[0] + b[0], a[1] + b[1]


def subtract(a: Vec2, b: Vec2) -> Vec2:
    return a[0] - b[0], a[1] - b[1]


class Direction(StrEnum):
    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"


DIRECTIONS: dict[Direction, Vec2] = {
    Direction.UP: (0, -1),
    Direction.DOWN: (0, 1),
    Direction.LEFT: (-1, 0),
    Direction.RIGHT: (1, 0),
}

CONTROLS = {
    "w": Direction.UP,
    "a": Direction.LEFT,
    "s": Direction.DOWN,
    "d": Direction.RIGHT,
}


# Entities
GAME = "game"
PLAYER = "player"

# Components
WIDTH = "width"
HEIGHT = "height"
STATUS = "status"
STATUS_RUNNING = "running"
STATUS_SUCCESS = "success"
STATUS_FAILURE = "failure"

POSITION = "position"
SOLID = "solid"
PUSHABLE = "pushable"
PUSHER = "pusher"
SYMBOL = "symbol"


@dataclass(frozen=True)
class Move(Event):
    entity: str
    destination: Vec2


@dataclass(frozen=True)
class EnterTile(Event):
    entity: str
    destination: tuple[int, int]
    direction: tuple[int, int] | None = None


def get_grid_bounds(world: World) -> Vec2:
    w = cast(int, world.get(GAME, WIDTH))
    h = cast(int, world.get(GAME, HEIGHT))
    return w, h


def game_over(world: World, event: Event):
    if (status := world.get(GAME, STATUS)) != STATUS_RUNNING:
        return Rejection(f"GAME OVER (status: {status})")


def enter_tile(world: World, event: Move):
    position: Vec2 = world.get(event.entity, POSITION, default=None)  # type: ignore
    direction = subtract(event.destination, position) if position else None
    yield EnterTile(
        entity=event.entity,
        destination=event.destination,
        direction=direction,
    )


def push(world: World, event: EnterTile):
    if not (
        event.direction
        and world.get(event.entity, PUSHER, default=None)
        and world.get(event.entity, SOLID, default=None)
    ):
        return

    for entity, components in world.entities.items():
        if (
            SOLID in components
            and PUSHABLE in components
            and (position := cast(Vec2, components.get(POSITION)))
            and position == event.destination
        ):
            yield Move(entity, add(position, event.direction))


def solid(world: World, event: EnterTile):
    for entity, component in world.entities.items():
        if (
            SOLID in component
            and (position := component.get(POSITION))
            and position == event.destination
        ):
            return Rejection(f"solid {entity} at position {position}")


def grid_bounds(world: World, event: EnterTile):
    w, h = get_grid_bounds(world)
    x, y = event.destination

    if 0 <= x < w and 0 <= y < h:
        return

    return Rejection(f"tile {x},{y} out of bounds for {w}x{h} (WxH) grid")


def set_position(world: World, event: EnterTile):
    yield Set(
        entity=event.entity,
        component="position",
        value=event.destination,
    )


def record_failed_move(world: World, event: Rejected):
    if isinstance(event.root, Move):
        yield Set(
            entity=event.root.entity,
            component="last_move_failed",
            value=True,
        )


def format_world(world: World):
    w, h = get_grid_bounds(world)
    grid: list[list[str]] = [w*['.'] for _ in range(h)]
    legend: dict[str, list[str]] = {}

    for entity, components in reversed(list(world.entities.items())):
        if (
            (position := cast(Vec2 | None, components.get(POSITION)))
            and (symbol := cast(str | None, components.get(SYMBOL)))
        ):
            x, y = position
            grid[y][x] = symbol
            if symbols := legend.get(symbol):
                symbols.append(entity)
            else:
                legend[symbol] = [entity]

    grid_text = '\n'.join(''.join(row) for row in grid)
    return grid_text


def step(world: World, engine: Engine):
    text = ''
    while True:
        text = input('').lower()
        if text in CONTROLS:
            break

        print(f"input: {text!r} is invalid, select from {','.join(CONTROLS)}")

    direction = DIRECTIONS[CONTROLS[text]]
    position = cast(Vec2, world.get(PLAYER, POSITION))
    event = Move(PLAYER, destination=add(position, direction))
    trace = engine.submit(world, event)
    return trace


def main():
    engine = Engine(
        [
            RejectRule(Event, game_over),
            RequireRule(Move, enter_tile),
            RequireRule(EnterTile, push),
            RejectRule(EnterTile, solid),
            RejectRule(EnterTile, grid_bounds),
            RequireRule(EnterTile, set_position),
            ReactRule(Rejected, record_failed_move),
        ]
    )

    world = World(
        {
            GAME: {
                WIDTH: 9,
                HEIGHT: 5,
                STATUS: STATUS_RUNNING,
            },
            PLAYER: {
                SYMBOL: "@",
                POSITION: (1, 1),
                SOLID: True,
                PUSHER: True,
            },
            "block-1": {
                POSITION: (2, 1),
                SYMBOL: "#",
                SOLID: True,
                PUSHABLE: True,
            },
        },
    )

    turn = 1
    print("Turn:", turn)
    print(format_world(world))
    while True:
        trace = step(world, engine)
        print(trace)
        print("Turn:", turn)
        print(format_world(world))


if __name__ == '__main__':
    main()
