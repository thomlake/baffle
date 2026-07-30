"""Copy-on-write world state.

A transaction shallow-copies the top-level entity mapping and copies an entity only
when something first writes to it. Most transactions touch one or two entities,
so the cost of starting one is proportional to what changes rather than to the size of
the world -- which is what makes running the engine inside a search loop viable.

Copying an entity is `dict(components)`, and that is the whole reason component values
are immutable (see :data:`~baffle.types.JsonValue`). Nothing here has to walk a value,
normalise a container, or worry about two entities sharing one.

The consequence is **structural sharing**: an untouched entity is the same object
across state generations. State handed out by the engine is therefore immutable by
convention. Never mutate a state you did not build yourself; emit an event instead.
"""

from __future__ import annotations

from collections.abc import Hashable, Iterator, Mapping, Sequence
from types import MappingProxyType
from typing import Any, cast

from .errors import EngineFault
from .query import Query
from .records import Mutation, RecordLog
from .types import (
    ComponentPath,
    Components,
    Entities,
    EntitiesLike,
    EntityId,
    JsonValue,
)
from .vectors import Vec2


class _Missing:
    """Sentinel for "there was nothing here", distinct from a stored ``None``."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover
        return "MISSING"

    def __bool__(self) -> bool:
        return False


MISSING = _Missing()


class _NoDefault:
    """Distinct from :data:`MISSING`, which callers may legitimately pass as a default.

    Conflating the two makes absence impossible to probe for: asking for MISSING and
    asking to raise would be the same request.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover
        return "<no default>"


_NO_DEFAULT = _NoDefault()

#: The component key a whole-entity mutation records against, since create and delete
#: change an entity rather than anything inside one.
WHOLE_ENTITY = ""


# ---------------------------------------------------------------------------
# The write boundary
# ---------------------------------------------------------------------------


def validate_key(key: ComponentPath) -> ComponentPath:
    """Reject a malformed component key once, rather than in every operation."""
    if not isinstance(key, str) or not key:
        raise EngineFault(
            f"A component key must be a non-empty string, got {key!r}"
        )
    return key


def normalize_value(value: JsonValue) -> JsonValue:
    """Settle what a component may hold, on its way into the world.

    Validation rather than copying: the result is immutable, so nothing outside the world
    can reach in and change it afterwards. That is what makes the aliasing hazard
    structural rather than something every operation has to remember.

    Lists become tuples, which is the affordance for data loaded from JSON at runtime --
    where the types say nothing and a list is what a decoder produces. Scalars and tuples
    of scalars return unchanged, which is the overwhelming majority of writes.

    Rejecting an unsupported type here, at the write, beats rejecting it later at the
    hash, where the author has no way to connect the complaint to the line that caused it.
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, tuple):
        if all(item is None or isinstance(item, (bool, int, str)) for item in value):
            return value  # a position, or any flat tuple: the hot path
        return tuple(normalize_value(item) for item in value)
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return tuple(normalize_value(item) for item in value)
    raise EngineFault(f"A component cannot hold a {type(value).__name__}: {value!r}")


def normalize_components(components: Mapping[str, JsonValue]) -> Components:
    """Validate a whole entity's worth of components, for :meth:`World.create`."""
    return {
        validate_key(str(key)): normalize_value(value)
        for key, value in components.items()
    }


def own(entities: EntitiesLike) -> Entities:
    """Take a caller's world as the engine's own top-level mapping.

    A cast rather than a per-entity copy. `Mapping` at the boundary is variance
    accommodation -- the runtime shape is the same dicts either way -- and copying every
    entity to satisfy the checker would defeat copy-on-write, whose whole point is that
    cost tracks what changes rather than how much world there is.
    """
    return cast(Entities, dict(entities))


# ---------------------------------------------------------------------------
# Canonical hashing
# ---------------------------------------------------------------------------


def state_key(entities: EntitiesLike) -> Hashable:
    """A canonical, order-independent key for a whole world, for transposition tables.

    Component values are immutable and therefore already hashable, so this only has to
    settle ordering. Keys within an entity are unique strings, so sorting pairs never
    reaches the values and cannot trip over comparing an int with a tuple.
    """
    return tuple(
        (entity_id, tuple(sorted(entities[entity_id].items())))
        for entity_id in sorted(entities)
    )


# ---------------------------------------------------------------------------
# World
# ---------------------------------------------------------------------------


class World:
    """The working state of one transaction, and the ``world`` a rule receives.

    Reads are cheap and unconditional. Writes go through :meth:`_own`, which is what
    keeps the base state intact for rollback, and record a :class:`~baffle.records.Mutation`.
    """

    __slots__ = ("_entities", "_log", "_owned", "_strict")

    def __init__(
        self,
        base: EntitiesLike,
        *,
        log: RecordLog,
        strict: bool = True,
    ) -> None:
        self._entities: Entities = own(base)
        self._owned: set[EntityId] = set()
        self._log = log
        self._strict = strict

    # -- reads ------------------------------------------------------------

    def __getitem__(self, entity_id: EntityId) -> Mapping[str, JsonValue]:
        try:
            components = self._entities[entity_id]
        except KeyError:
            raise EngineFault("No such entity", entity=entity_id) from None
        # Under copy-on-write, writing to a component dict the engine has not yet
        # copied would reach committed state and survive rollback. In strict mode the
        # mistake fails at the point it happens instead of corrupting a later turn.
        #
        # One proxy is total coverage, because the values inside are immutable. When a
        # component could hold a nested dict this guard was only one level deep, and a
        # rule could reach through it into the caller's own state.
        return MappingProxyType(components) if self._strict else components

    def __contains__(self, entity_id: object) -> bool:
        return entity_id in self._entities

    def __iter__(self) -> Iterator[EntityId]:
        return iter(sorted(self._entities))

    def __len__(self) -> int:
        return len(self._entities)

    def value(
        self, entity_id: EntityId, key: ComponentPath, default: Any = _NO_DEFAULT
    ) -> Any:
        """Read a component, defaulting rather than raising when asked to.

        Conditions usually want a default; reading a value an operation is about to
        change usually does not.
        """
        components = self._entities.get(entity_id)
        if components is None:
            if default is not _NO_DEFAULT:
                return default
            raise EngineFault("No such entity", entity=entity_id, component=key)
        try:
            return components[key]
        except KeyError:
            if default is not _NO_DEFAULT:
                return default
            raise EngineFault(
                "Missing component", entity=entity_id, component=key
            ) from None

    def vector(self, entity_id: EntityId, key: ComponentPath = "position") -> Vec2:
        """Read a component as a two-integer vector.

        A component holds a :data:`~baffle.types.JsonValue`, so passing one straight to
        grid arithmetic is untyped. This validates and narrows in one step, which is the
        difference between a checked rule and a rule full of casts.
        """
        value = self.value(entity_id, key)
        if isinstance(value, tuple) and len(value) == 2:
            x, y = value
            if all(isinstance(n, int) and not isinstance(n, bool) for n in (x, y)):
                return (x, y)  # type: ignore[return-value]
        raise EngineFault(
            f"Component is not a two-integer vector: {value!r}",
            entity=entity_id,
            component=key,
        )

    def query(
        self,
        *truthy: str,
        falsy: tuple[str, ...] = (),
        **equals: JsonValue,
    ) -> tuple[EntityId, ...]:
        """Entities whose components satisfy every predicate, in sorted order.

        Sorted because rule firing order affects outcomes, and replay and search both
        need identical reruns.

        `truthy` and `falsy` read a component truthily, so a flag disabled with
        ``value=False`` counts as absent. See :class:`~baffle.query.Query`.

        Two shapes this signature cannot express, both consequences of using keyword
        arguments for `equals`: a component literally named ``falsy``, and a dotted key,
        which is not a valid identifier. Construct a :class:`~baffle.query.Query` and
        call :meth:`~baffle.query.Query.run` for either.

        Declaring constraints here rather than filtering afterwards is what leaves room
        for an index later -- a predicate the engine can read is one it can satisfy
        without a scan.
        """
        return Query(
            truthy=truthy, falsy=falsy, equals=tuple(equals.items())
        ).run(self._entities)

    # -- copy-on-write ----------------------------------------------------

    def _own(self, entity_id: EntityId) -> Components:
        """Return a copy of `entity_id` that this transaction may mutate.

        A shallow `dict` copy is sufficient, and around twenty times faster than the deep
        copy this needed when a component could hold a mutable container. Immutable
        values are safe to share between generations.
        """
        if entity_id in self._owned:
            return self._entities[entity_id]
        try:
            original = self._entities[entity_id]
        except KeyError:
            raise EngineFault("No such entity", entity=entity_id) from None
        copy = dict(original)
        self._entities[entity_id] = copy
        self._owned.add(entity_id)
        return copy

    def _record(
        self,
        entity_id: EntityId,
        key: ComponentPath,
        old: Any,
        new: Any,
        kind: str,
    ) -> None:
        self._log.mutation(
            Mutation(entity=entity_id, path=key, old=old, new=new, kind=kind)
        )

    # -- writes -----------------------------------------------------------
    #
    # Each write returns what it displaced, which it had to compute anyway in order to
    # record the mutation. Handing it back rather than dropping it is what lets an
    # operation report a previous value without reading the component a second time --
    # and an ``after`` rule learn what changed without scanning the record stream.
    # :data:`MISSING` means there was nothing there, which is how a create is told from
    # a replace.

    def set(
        self,
        entity_id: EntityId,
        key: ComponentPath,
        value: JsonValue,
        *,
        create: bool = True,
    ) -> Any:
        """Replace the value at `key`, introducing it when absent. Returns the old one."""
        validate_key(key)
        components = self._own(entity_id)
        value = normalize_value(value)
        if not create and key not in components:
            raise EngineFault(
                "Cannot set an absent component", entity=entity_id, component=key
            )
        old = components.get(key, MISSING)
        components[key] = value
        self._record(
            entity_id, key, old, value, "insert" if old is MISSING else "replace"
        )
        return old

    def unset(self, entity_id: EntityId, key: ComponentPath) -> Any:
        """Drop `key`. Returns the value that was there."""
        validate_key(key)
        components = self._own(entity_id)
        if key not in components:
            raise EngineFault(
                "Cannot unset an absent component", entity=entity_id, component=key
            )
        old = components.pop(key)
        self._record(entity_id, key, old, MISSING, "remove")
        return old

    def create(self, entity_id: EntityId, components: Mapping[str, JsonValue]) -> None:
        """Add an entity. Displaces nothing, so there is nothing to return."""
        if entity_id in self._entities:
            raise EngineFault("Entity already exists", entity=entity_id)
        owned = normalize_components(components)
        self._entities[entity_id] = owned
        self._owned.add(entity_id)
        self._record(entity_id, WHOLE_ENTITY, MISSING, owned, "insert")

    def delete(self, entity_id: EntityId) -> Components:
        """Remove an entity. Returns everything it held."""
        try:
            old = self._entities.pop(entity_id)
        except KeyError:
            raise EngineFault("No such entity", entity=entity_id) from None
        self._owned.discard(entity_id)
        self._record(entity_id, WHOLE_ENTITY, old, MISSING, "remove")
        return old

    # -- boundary ---------------------------------------------------------

    def snapshot(self) -> Entities:
        """The resulting state.

        A fresh top-level mapping, so the caller can hold it independently. Untouched
        entities are shared with earlier generations, which is the point of
        copy-on-write and the reason state is immutable by convention.
        """
        return dict(self._entities)

    @property
    def touched(self) -> frozenset[EntityId]:
        """Entities this transaction copied. The changed set, for free."""
        return frozenset(self._owned)
