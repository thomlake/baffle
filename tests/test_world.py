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


def test_contains_reports_entity_presence() -> None:
    world = World({"player": {}})

    assert "player" in world
    assert "door" not in world


def test_iteration_yields_entities() -> None:
    world = World({"player": {}, "door": {}})

    assert list(world) == ["player", "door"]
    assert len(world) == 2


def test_has_reports_component_presence() -> None:
    world = World({"player": {"health": 3}})

    assert world.has("player", "health")
    assert not world.has("player", "position")
    assert not world.has("door", "health")


def test_components_returns_read_only_view() -> None:
    world = World({"player": {"health": 3}})

    components = world.components("player")

    assert dict(components) == {"health": 3}

    with pytest.raises(TypeError):
        components["health"] = 1  # type: ignore[index]


def test_components_view_reflects_later_changes() -> None:
    world = World({"player": {}})

    components = world.components("player")
    world.set("player", "health", 3)

    assert dict(components) == {"health": 3}


def test_components_raises_for_missing_entity() -> None:
    world = World({})

    with pytest.raises(KeyError):
        world.components("player")


def test_replace_replaces_world_state() -> None:
    world = World({"player": {"health": 3}})

    world_copy = world.copy()
    world_copy.set("player", "health", 1)
    assert world.get("player", "health") == 3

    world.replace(world_copy)
    assert world.get("player", "health") == 1
