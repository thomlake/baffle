import pytest

from baffle.types import ComponentValue
from baffle.world import World


def test_world_copies_input_state() -> None:
    state: dict[str, dict[str, ComponentValue]] = {
        "player": {
            "health": 3,
            "position": (1, 2),
        }
    }

    world = World(state)

    state["player"]["health"] = 1

    assert world.get("player", "health") == 3


def test_snapshot_copies_world_state() -> None:
    world = World(
        {
            "player": {
                "health": 3,
                "position": (1, 2),
            }
        }
    )

    snapshot = world.snapshot()
    snapshot["player"]["health"] = 1

    assert world.get("player", "health") == 3


def test_get_returns_component() -> None:
    world = World({"player": {"health": 3}})

    assert world.get("player", "health") == 3


def test_get_raises_for_missing_component() -> None:
    world = World({"player": {}})

    with pytest.raises(KeyError):
        world.get("player", "health")


def test_get_raises_for_missing_entity() -> None:
    world = World({})

    with pytest.raises(KeyError):
        world.get("player", "health")


def test_get_returns_default_for_missing_component() -> None:
    world = World({"player": {}})

    assert world.get("player", "health", default=0) == 0


def test_get_returns_default_for_missing_entity() -> None:
    world = World({})

    assert world.get("player", "health", default=0) == 0


def test_create_adds_entity() -> None:
    world = World({})

    world.create(
        "player",
        {
            "health": 3,
            "position": (1, 2),
        },
    )

    assert world.get("player", "health") == 3
    assert world.get("player", "position") == (1, 2)


def test_create_copies_components() -> None:
    components: dict[str, ComponentValue] = {"health": 3}
    world = World({})

    world.create("player", components)
    components["health"] = 1

    assert world.get("player", "health") == 3


def test_create_rejects_existing_entity() -> None:
    world = World({"player": {}})

    with pytest.raises(ValueError, match="Entity already exists: player"):
        world.create("player", {"health": 3})


def test_delete_removes_entity() -> None:
    world = World({"player": {"health": 3}})

    world.delete("player")

    with pytest.raises(KeyError):
        world.get("player", "health")


def test_delete_rejects_missing_entity() -> None:
    world = World({})

    with pytest.raises(KeyError):
        world.delete("player")


def test_set_replaces_component() -> None:
    world = World({"player": {"health": 3}})

    world.set("player", "health", 2)

    assert world.get("player", "health") == 2


def test_set_creates_component() -> None:
    world = World({"player": {}})

    world.set("player", "health", 3)

    assert world.get("player", "health") == 3


def test_set_rejects_missing_entity() -> None:
    world = World({})

    with pytest.raises(KeyError):
        world.set("player", "health", 3)
