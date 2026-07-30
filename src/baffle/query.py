"""Entity selection as data, not as a closure.

A rule could iterate the world itself, and for anything unusual it still can. But a
query the engine can *read* is one it can later satisfy from an index instead of a
scan, and rule selection runs once per rule per event -- multiplied by every node of a
search tree. Keeping the common predicates declarative is what leaves that door open.

The index is not built yet. This is the representation it will attach to, which is the
part that is expensive to retrofit.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .types import EntitiesLike, EntityId, JsonValue


@dataclass(frozen=True)
class Query:
    """Entities whose components satisfy every predicate.

    The predicates are named for what they actually test. :attr:`truthy` and
    :attr:`falsy` read a component *truthily* -- absent, ``False``, and ``0`` are alike
    to them -- because that is what a game wants: ``truthy=("solid",)`` should skip a
    door whose ``solid`` was set to ``False`` to unlock it, and
    ``truthy=("inventory.keys.red",)`` should mean "has at least one" without a separate
    check for the zero case.

    They used to be called ``has`` and ``exclude``, which hid both that they were a
    symmetric pair and that neither was about presence. For presence, ask the components
    mapping directly: ``key in world[entity]``.

    :attr:`equals` is a tuple of pairs rather than a mapping so the query is hashable,
    and therefore usable as a cache key once indices exist.
    """

    truthy: tuple[str, ...] = ()
    falsy: tuple[str, ...] = ()
    equals: tuple[tuple[str, JsonValue], ...] = ()

    def matches(self, components: Mapping[str, JsonValue]) -> bool:
        for name in self.truthy:
            if not components.get(name):
                return False
        for name in self.falsy:
            if components.get(name):
                return False
        for name, expected in self.equals:
            if components.get(name) != expected:
                return False
        return True

    def run(self, entities: EntitiesLike) -> tuple[EntityId, ...]:
        """Evaluate by scan, in sorted order.

        Sorted because rule firing order changes outcomes, and both replay and search
        need reruns to be identical. An index will preserve the same ordering.
        """
        return tuple(
            entity_id
            for entity_id in sorted(entities)
            if self.matches(entities[entity_id])
        )
