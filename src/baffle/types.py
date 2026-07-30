"""The shared vocabulary.

Everything here is a name for a shape, with no behavior attached. It lives in its own
module at the bottom of the import graph so that the meaning of "a world" does not
depend on which module happened to be written first.
"""

from collections.abc import Mapping

#: Values a component may hold. Deliberately no float: exact comparison and stable
#: hashing matter more here than fractional arithmetic, and integer ratios cover the
#: cases games actually need.
type JsonScalar = None | bool | int | str

# A component value is **immutable**, all the way down. That single constraint is what
# makes a transaction cheap: copying an entity is `dict(components)` rather than a deep
# copy, hashing a world only has to settle ordering, and no value can be shared into
# state and then mutated from outside. Tuples rather than lists because a tuple is
# hashable, which events rely on -- an event is a frozen dataclass that gets hashed for
# transposition tables, so a list-valued field would make it unhashable.
#
# Lazily evaluated, so the recursion needs no string quoting.
type JsonValue = JsonScalar | tuple[JsonValue, ...]

type EntityId = str

#: A component key. Flat, and namespaced with dots: ``"position"``,
#: ``"inventory.keys.red"``.
#:
#: Flat rather than a path into nested containers, because a key that cannot address
#: *into* a value is a key that never has to be walked, split, or type-dispatched. Tuples
#: are opaque leaves: there is no ``"order.0"``. A rule that wants to change one element
#: reads the tuple, computes the new one, and sets it -- which also keeps the resulting
#: event meaningful independently of when it executes.
type ComponentPath = str

#: Everything one entity holds, keyed by component.
type Components = dict[str, JsonValue]

#: A whole world: every entity, by id. Named for its shape rather than its role, so
#: ``self._entities: Entities`` reads as what it is.
type Entities = dict[EntityId, Components]

#: What a caller may hand in. `dict` is invariant in its value type, so a literal
#: ``{"player": {"hp": 3}}`` is a ``dict[str, dict[str, int]]`` and *not* an `Entities` --
#: which would make the first line anyone writes a type error. `Mapping` is covariant, so
#: this accepts the obvious thing. The engine copies it on the way in regardless.
type EntitiesLike = Mapping[EntityId, Mapping[str, JsonValue]]

#: The reserved entity holding world-level configuration -- grid bounds, turn counter,
#: whatever is global rather than owned by a thing in the world. An ordinary entity, so
#: copy-on-write, hashing, and queries all stay single-path. It carries no position, so
#: positional queries skip it without needing a special case.
WORLD: EntityId = "world"
