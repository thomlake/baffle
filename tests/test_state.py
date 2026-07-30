"""Copy-on-write state, component keys, queries, and hashing."""

from __future__ import annotations

import pytest

from baffle import (
    MISSING,
    EngineFault,
    Mutation,
    Query,
    RecordLog,
    World,
    state_key,
)


def build(sealed=False, **entities):
    log = RecordLog()
    return World(entities, log=log, sealed=sealed), log


# ---------------------------------------------------------------------------
# Component access
# ---------------------------------------------------------------------------


def test_namespaced_components_are_ordinary_keys():
    """Dots namespace a key; they are not a path into anything."""
    world, _ = build(player={"inventory.keys.red": 2, "statuses.poison.turns": 3})

    assert world.value("player", "inventory.keys.red") == 2
    assert world.value("player", "statuses.poison.turns") == 3
    # There is no interior node to read: "inventory" is simply not a component here.
    assert world.value("player", "inventory", default=MISSING) is MISSING


def test_reads_raise_or_default_as_asked():
    world, _ = build(player={"hp": 3})

    with pytest.raises(EngineFault):
        world.value("player", "mp")
    assert world.value("player", "mp", default=0) == 0
    assert world.value("player", "mp", default=MISSING) is MISSING
    assert "mp" not in world["player"]
    assert "hp" in world["player"]


def test_reading_a_missing_entity_raises_or_defaults():
    world, _ = build(player={"hp": 3})

    with pytest.raises(EngineFault, match="No such entity"):
        world.value("ghost", "hp")
    assert world.value("ghost", "hp", default=None) is None
    assert "ghost" not in world


@pytest.mark.parametrize("key", ["", ("hp",), 1, None])
def test_a_malformed_component_key_is_a_fault(key):
    """It used to reach ``path[-1]`` and raise a bare IndexError from inside the engine."""
    world, _ = build(player={"hp": 3})

    with pytest.raises(EngineFault, match="non-empty string"):
        world.set("player", key, 1)
    with pytest.raises(EngineFault, match="non-empty string"):
        world.unset("player", key)


def test_a_namespaced_key_needs_no_parent_to_exist():
    """The trade for flat keys: there is no container above a key to be missing."""
    world, _ = build(player={"hp": 3})

    world.set("player", "inventory.keys.red", 1)

    assert world.value("player", "inventory.keys.red") == 1


# ---------------------------------------------------------------------------
# Copy-on-write
# ---------------------------------------------------------------------------


def test_only_written_entities_are_copied():
    base = {"a": {"v": 1}, "b": {"v": 2}, "c": {"v": 3}}
    world = World(base, log=RecordLog())

    world.set("b", "v", 20)
    result = world.snapshot()

    assert world.touched == {"b"}
    assert result["a"] is base["a"]
    assert result["c"] is base["c"]
    assert result["b"] is not base["b"]
    assert base["b"]["v"] == 2, "the base must be left intact for rollback"


def test_repeated_writes_copy_an_entity_once():
    base = {"a": {"v": 1}}
    world = World(base, log=RecordLog())

    world.set("a", "v", 2)
    first = world.snapshot()["a"]
    world.set("a", "v", 3)

    assert world.snapshot()["a"] is first
    assert base["a"]["v"] == 1


def test_discarding_a_working_state_leaves_the_base_untouched():
    base = {"a": {"bag": ("rope",)}}
    world = World(base, log=RecordLog())

    world.set("a", "bag", ("rope", "torch"))

    assert base["a"]["bag"] == ("rope",)


# ---------------------------------------------------------------------------
# The value domain
# ---------------------------------------------------------------------------


def test_a_list_is_stored_as_a_tuple():
    """The affordance for data decoded from JSON, where a list is what arrives."""
    world, _ = build(a={})

    world.set("a", "bag", ["rope", ["nested"]])

    assert world.value("a", "bag") == ("rope", ("nested",))


def test_stored_values_cannot_be_reached_from_outside():
    """State shares no mutable structure with anything, because it holds none.

    This needed a deep copy of every stored value when a component could hold a list or
    a dict. Immutability makes it structural instead.
    """
    world, _ = build(a={"bag": ("x",)}, b={})
    shared = ["rope"]

    world.set("b", "bag", shared)
    stored = world.value("b", "bag")

    assert stored == ("rope",)
    assert isinstance(stored, tuple)
    shared.append("torch")
    assert world.value("b", "bag") == ("rope",), "the world is unaffected"


@pytest.mark.parametrize("value", [1.5, object(), {"nested": 1}, {"a", "b"}], ids=type)
def test_a_component_cannot_hold_a_mutable_or_inexact_value(value):
    """Rejected where it is stored, rather than later when something tries to hash it."""
    world, _ = build(a={"v": None})

    with pytest.raises(EngineFault, match="cannot hold"):
        world.set("a", "v", value)  # type: ignore[arg-type]


def test_a_sealed_world_refuses_direct_mutation():
    """Under copy-on-write this write would reach committed state and survive rollback."""
    world, _ = build(sealed=True, player={"hp": 3})

    with pytest.raises(TypeError):
        # Deliberately wrong: the read-only view is half of what sealing means.
        world["player"]["hp"] = 99  # type: ignore[index]


def test_a_sealed_world_refuses_the_write_methods_too():
    """The other half, and the one a rule reaches by accident.

    ``world["p"]["hp"] = 99`` is caught by the view, but ``world.set(...)`` is public API
    on the very object a rule is handed. Writing through it used to commit with no frame
    and no operation, or -- from a reaction -- log a mutation whose write was discarded.
    """
    world, _ = build(sealed=True, player={"hp": 3})

    for write in (
        lambda: world.set("player", "hp", 99),
        lambda: world.unset("player", "hp"),
        lambda: world.create("orc", {"hp": 1}),
        lambda: world.delete("player"),
    ):
        with pytest.raises(EngineFault, match="may not write"):
            write()

    assert world.value("player", "hp") == 3, "nothing got through"


def test_unsealing_is_what_lets_an_operation_write():
    """The resolver opens the world for exactly one operation, then closes it again."""
    world, _ = build(sealed=True, player={"hp": 3})

    world.unseal()
    world.set("player", "hp", 5)
    world.seal()

    assert world.value("player", "hp") == 5
    with pytest.raises(EngineFault, match="may not write"):
        world.set("player", "hp", 7)


def test_a_sealed_world_has_nothing_to_reach_through():
    """The proxy used to be one level deep, so a nested container leaked the real object.

    A rule could take ``world["player"]["inventory"]`` -- a live dict, possibly the
    caller's own -- and write to it, reaching committed state and surviving rollback.
    Immutable values mean there is no second level to hand out.
    """
    world, _ = build(sealed=True, player={"bag": ("rope",), "hp": 3})

    for value in world["player"].values():
        assert not isinstance(value, (list, dict, set))


# ---------------------------------------------------------------------------
# Mutation records
# ---------------------------------------------------------------------------


def test_replacement_records_both_sides():
    world, log = build(player={"hp": 3})

    world.set("player", "hp", 5)

    (record,) = [r for r in log if isinstance(r, Mutation)]
    assert (record.entity, record.path, record.old, record.new, record.kind) == (
        "player",
        "hp",
        3,
        5,
        "replace",
    )


def test_creating_a_key_records_an_insert():
    world, log = build(player={"hp": 3})

    world.set("player", "mp", 7)

    (record,) = [r for r in log if isinstance(r, Mutation)]
    assert record.kind == "insert"
    assert record.old is MISSING


def test_removal_records_only_the_old_side():
    world, log = build(player={"hp": 3})

    world.unset("player", "hp")

    (record,) = [r for r in log if isinstance(r, Mutation)]
    assert (record.kind, record.path, record.old, record.new) == (
        "remove",
        "hp",
        3,
        MISSING,
    )


def test_a_sequence_change_records_one_whole_replacement():
    """Values are immutable, so there is no in-place edit to record.

    Both sides are present, which is what incremental (Zobrist-style) hashing needs:
    XOR the old tuple out, the new one in.
    """
    world, log = build(player={"bag": ("rope", "torch")})

    world.set("player", "bag", ("rope", "torch", "map"))

    (record,) = [r for r in log if isinstance(r, Mutation)]
    assert (record.kind, record.path) == ("replace", "bag")
    assert (record.old, record.new) == (("rope", "torch"), ("rope", "torch", "map"))


def test_entity_lifecycle_records_the_whole_entity():
    world, log = build(orc={"hp": 5})

    world.create("goblin", {"hp": 2})
    world.delete("orc")

    created, deleted = [r for r in log if isinstance(r, Mutation)]
    assert (created.kind, created.path, created.new) == ("insert", "", {"hp": 2})
    assert (deleted.kind, deleted.path, deleted.old) == ("remove", "", {"hp": 5})


def test_a_whole_entity_record_is_a_snapshot_not_a_live_view():
    """It used to hold the live dict, so a later write rewrote recorded history.

    Every *component* value is immutable, which is what makes the other records honest.
    A whole-entity record had no such guarantee: create an entity, write to it, and the
    insert retroactively claimed the new value was the one inserted.
    """
    world, log = build(player={"hp": 3})

    world.create("goblin", {"hp": 2})
    world.set("goblin", "hp", 99)

    created = next(r for r in log if isinstance(r, Mutation) and r.kind == "insert")
    assert created.new == {"hp": 2}, "the record must say what was actually inserted"
    with pytest.raises(TypeError):
        created.new["hp"] = 0  # type: ignore[index]


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


def test_queries_match_on_presence_and_equality():
    world, _ = build(
        player={"position": (1, 0), "solid": True},
        crate={"position": (1, 0), "solid": True, "pushable": True},
        ghost={"position": (1, 0)},
        wall={"position": (2, 0), "solid": True},
    )

    assert world.query(position=(1, 0)) == ("crate", "ghost", "player")
    assert world.query("solid", position=(1, 0)) == ("crate", "player")
    assert world.query("pushable", position=(1, 0)) == ("crate",)
    assert world.query(position=(9, 9)) == ()


def test_a_namespaced_component_is_queryable_like_any_other():
    world, _ = build(a={"inventory.keys.red": 1}, b={"inventory.keys.red": 0}, c={})

    # Truthy, so a count of zero reads as "has none" without a separate check.
    assert world.query("inventory.keys.red") == ("a",)
    assert Query(equals=(("inventory.keys.red", 0),)).run(
        {"a": world["a"], "b": world["b"], "c": world["c"]}
    ) == ("b",)


def test_queries_are_sorted_so_reruns_are_identical():
    """Rule firing order changes outcomes; replay and search need determinism."""
    forwards, _ = build(a={"tag": 1}, b={"tag": 1}, c={"tag": 1})
    backwards, _ = build(c={"tag": 1}, b={"tag": 1}, a={"tag": 1})

    assert forwards.query(tag=1) == backwards.query(tag=1) == ("a", "b", "c")


def test_the_world_entity_needs_no_special_casing():
    """It has no position, so a positional query never picks it up."""
    world, _ = build(world={"width": 5, "height": 5}, player={"position": (0, 0)})

    assert world.query(position=(0, 0)) == ("player",)


def test_a_query_is_hashable_data():
    """The representation an index will attach to, and cache on."""
    query = Query(truthy=("solid",), equals=(("position", (1, 0)),))

    assert hash(query) == hash(Query(truthy=("solid",), equals=(("position", (1, 0)),)))
    assert query.matches({"solid": True, "position": (1, 0)})
    assert not query.matches({"position": (1, 0)})


def test_falsy_narrows_a_query():
    """Reachable from the method now; it used to exist only on Query itself."""
    world, _ = build(
        crate={"position": (1, 0), "pushable": True},
        wall={"position": (1, 0), "fixed": True},
        ruin={"position": (1, 0), "fixed": False},
    )

    assert world.query(falsy=("fixed",), position=(1, 0)) == ("crate", "ruin")
    assert Query(equals=(("position", (1, 0)),), falsy=("fixed",)).run(
        {name: world[name] for name in ("crate", "wall", "ruin")}
    ) == ("crate", "ruin")


def test_truthy_and_falsy_read_a_component_truthily():
    """A flag disabled with ``value=False`` counts as absent, which is the point.

    ``unlock`` clears a door by setting ``solid`` to False rather than removing it, and
    ``solid`` must stop matching for the move to proceed.
    """
    world, _ = build(
        open_door={"solid": False, "locked": False},
        shut_door={"solid": True, "locked": True},
        gap={},
    )

    assert world.query("solid") == ("shut_door",)
    assert world.query(falsy=("solid",)) == ("gap", "open_door")


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------


def test_hashing_ignores_key_order():
    assert state_key({"a": {"x": 1, "y": 2}}) == state_key({"a": {"y": 2, "x": 1}})


def test_identical_worlds_reached_differently_hash_alike():
    """What a transposition table depends on."""
    one = {"p": {"position": (1, 1)}, "q": {"position": (2, 2)}}
    two = {"q": {"position": (2, 2)}, "p": {"position": (1, 1)}}

    assert state_key(one) == state_key(two)
    assert hash(state_key(one)) == hash(state_key(two))


def test_differing_worlds_do_not_collide():
    assert state_key({"a": {"v": 1}}) != state_key({"a": {"v": 2}})
    assert state_key({"a": {"v": (1, 2)}}) != state_key({"a": {"v": (2, 1)}})
    assert state_key({"a": {"v": 1}}) != state_key({"b": {"v": 1}})


def test_mixed_value_types_within_an_entity_hash_without_comparing_values():
    """Keys are unique strings, so sorting pairs never reaches an int-versus-tuple."""
    assert state_key({"a": {"hp": 1, "pos": (0, 0), "name": "x", "dead": False}})
