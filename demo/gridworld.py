from dataclasses import dataclass
from enum import Enum, StrEnum

import readchar

from baffle import (
    Engine,
    Event,
    ReactRule,
    Rejection,
    RejectRule,
    RequireRule,
    Set,
    World,
)


# ------------ #
# Vector + Ops #
# ------------ #

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


# ---------- #
# Game Vocab #
# ---------- #

class Entity(StrEnum):
    GAME = "game"
    PLAYER = "player"


class Component(StrEnum):
    WIDTH = "width"
    HEIGHT = "height"
    STATUS = "status"
    SYMBOL = "symbol"
    POSITION = "position"
    SOLID = "solid"
    PUSHABLE = "pushable"
    PUSHER = "pusher"
    COLLECTABLE = "collectable"
    COLLECTOR = "collector"
    KEY = "key"


class Status(StrEnum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILURE = "failure"


# ------ #
# Events #
# ------ #

@dataclass(frozen=True)
class Move(Event):
    entity: str
    direction: Vec2


@dataclass(frozen=True)
class Wait(Event):
    entity: str


@dataclass(frozen=True)
class EnterTile(Event):
    entity: str
    position: tuple[int, int]
    direction: tuple[int, int] | None = None


@dataclass(frozen=True)
class Collect(Event):
    taker: str
    giver: str
    item: str
    count: int = 1


# ------- #
# Helpers #
# ------- #

def get_grid_bounds(world: World) -> Vec2:
    w: int = world.get(Entity.GAME, Component.WIDTH)  # type: ignore
    h: int = world.get(Entity.GAME, Component.HEIGHT)  # type: ignore
    return w, h


# ----- #
# Rules #
# ----- #

def game_over(world: World, event: Event):
    if (status := world.get(Entity.GAME, Component.STATUS)) != Status.RUNNING:
        return Rejection(f"GAME OVER (status: {status})")


def enter_tile(world: World, event: Move):
    position: Vec2 = world.get(event.entity, Component.POSITION)  # type: ignore
    destination = add(event.direction, position)
    yield EnterTile(
        entity=event.entity,
        position=destination,
        direction=event.direction,
    )


def push(world: World, event: EnterTile):
    if not (
        event.direction
        and world.get(event.entity, Component.PUSHER, default=None)
        and world.get(event.entity, Component.SOLID, default=None)
    ):
        return

    for entity, components in world.entities.items():
        if (
            components.get(Component.POSITION) == event.position
            and components.get(Component.SOLID)
            and components.get(Component.PUSHABLE)
        ):
            yield Move(entity=entity, direction=event.direction)


def solid(world: World, event: EnterTile):
    for entity, components in world.entities.items():
        position = components.get(Component.POSITION)
        if position == event.position and components.get(Component.SOLID):
            return Rejection(f"solid {entity} at position {position}")


def collect(world: World, event: EnterTile):
    if not world.get(event.entity, Component.COLLECTOR, default=None):
        return

    for entity, components in world.entities.items():
        position = components.get(Component.POSITION)
        collectable: str = components.get(Component.COLLECTABLE)  # type: ignore
        if position == event.position and collectable:
            yield Collect(taker=event.entity, giver=entity, item=collectable)


def grid_bounds(world: World, event: EnterTile):
    w, h = get_grid_bounds(world)
    x, y = event.position

    if 0 <= x < w and 0 <= y < h:
        return

    return Rejection(f"tile {x},{y} out of bounds for {w}x{h} (WxH) grid")


def set_position(world: World, event: EnterTile):
    yield Set(
        entity=event.entity,
        component="position",
        value=event.position,
    )


def collect_item(world: World, event: Collect):
    if event.taker in world and event.giver in world:
        taker_current: int = world.get(event.taker, event.item, default=0)  # type: ignore
        giver_current: int = world.get(event.giver, event.item, default=0)  # type: ignore
        if giver_current > 0:
            yield Set(entity=event.taker, component=event.item, value=taker_current + 1)
            yield Set(entity=event.taker, component=event.item, value=giver_current - 1)


# --------- #
# Interface #
# --------- #

class Direction(Enum):
    UP = (0, -1)
    DOWN = (0, 1)
    LEFT = (-1, 0)
    RIGHT = (1, 0)


CONTROLS = {
    readchar.key.UP: ("move", Direction.UP),
    readchar.key.DOWN: ("move", Direction.DOWN),
    readchar.key.LEFT: ("move", Direction.LEFT),
    readchar.key.RIGHT: ("move", Direction.RIGHT),
    "q": ("quit",),
    "w": ("wait",),
}

CONTROL_DISPLAY = {
    readchar.key.UP: "⏶",
    readchar.key.DOWN: "⏷",
    readchar.key.LEFT: "⏵",
    readchar.key.RIGHT: "⏴",
}


def format_controls():
    controls = []
    for key, action in CONTROLS.items():
        key = CONTROL_DISPLAY.get(key, key)
        action_name, *action_rest = action
        if action_rest:
            direction, = action_rest
            action_name = f"{action_name} {direction.name.lower()}"

        controls.append(f"- {key}: {action_name}")

    return "\n".join(controls)


def format_world(world: World):
    w, h = get_grid_bounds(world)
    grid: list[list[str]] = [w*["."] for _ in range(h)]
    legend: dict[str, list[str]] = {}

    for entity, components in reversed(list(world.entities.items())):
        position: Vec2 | None = components.get(Component.POSITION)  # type: ignore
        symbol: str | None = components.get(Component.SYMBOL)  # type: ignore
        if position and symbol:
            x, y = position
            grid[y][x] = symbol
            if symbols := legend.get(symbol):
                symbols.append(entity)
            else:
                legend[symbol] = [entity]

    grid_text = "\n".join("".join(row) for row in grid)
    return grid_text


def get_input_event(world: World):
    while True:
        print("Select: ", end="", flush=True)
        key = readchar.readkey()
        # print()
        if action := CONTROLS.get(key):
            match action:
                case ("quit",):
                    return None
                case ("wait",):
                    return Wait(Entity.PLAYER)
                case ("move", direction):
                    return Move(Entity.PLAYER, direction=direction.value)
                case _:
                    raise ValueError(f"unknown action {action!r}")

        print(f"- invalid input {key!r}", flush=True)


def main():
    engine = Engine(
        [
            RejectRule(Event, game_over),
            RequireRule(Collect, collect_item),
            RequireRule(Move, enter_tile),
            RequireRule(EnterTile, push),
            RejectRule(EnterTile, solid),
            RejectRule(EnterTile, grid_bounds),
            RequireRule(EnterTile, set_position),
            ReactRule(EnterTile, collect),
        ]
    )

    world = World(
        {
            Entity.GAME: {
                Component.WIDTH: 9,
                Component.HEIGHT: 5,
                Component.STATUS: Status.RUNNING,
            },
            Entity.PLAYER: {
                Component.SYMBOL: "@",
                Component.POSITION: (1, 1),
                Component.SOLID: True,
                Component.PUSHER: True,
                Component.COLLECTOR: True,
            },
            "block-1": {
                Component.POSITION: (2, 1),
                Component.SYMBOL: "#",
                Component.SOLID: True,
                Component.PUSHER: True,
                Component.PUSHABLE: True,
            },
            "block-2": {
                Component.POSITION: (3, 1),
                Component.SYMBOL: "#",
                Component.SOLID: True,
                Component.PUSHER: True,
                Component.PUSHABLE: True,
            },
            "key-1": {
                Component.POSITION: (1, 3),
                Component.SYMBOL: "k",
                Component.COLLECTABLE: Component.KEY,
                Component.KEY: 1,
            }
        },
    )

    turn = 1
    print("Controls:")
    print(format_controls())
    print()
    print("Turn:", turn)
    print(format_world(world))
    while True:
        print()
        event = get_input_event(world)
        print(event)
        print()
        if not event:
            print("terminating...")
            return

        trace = engine.submit(world, event)
        print("Trace:")
        print(trace)
        print()
        print("Turn:", turn)
        print(format_world(world))


if __name__ == '__main__':
    main()
