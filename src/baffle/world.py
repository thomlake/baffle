"""World state representation."""

from collections.abc import Iterator, Mapping
from types import MappingProxyType
from typing import cast

from baffle.types import ComponentDict, ComponentValue, StateDict


class _Missing:
    __slots__ = ()


_MISSING = _Missing()


class World:
    """Mutable world state."""

    def __init__(self, state: StateDict) -> None:
        self._state = {
            entity: dict(components)
            for entity, components in state.items()
        }

    @property
    def entities(self) -> StateDict:
        return self._state

    def __contains__(self, entity: str) -> bool:
        return entity in self._state

    def __iter__(self) -> Iterator[str]:
        return iter(self._state)

    def __len__(self) -> int:
        return len(self._state)

    def get(
        self,
        entity: str,
        component: str,
        *,
        default: ComponentValue | _Missing = _MISSING,
    ) -> ComponentValue:
        try:
            return self._state[entity][component]
        except KeyError:
            if default is _MISSING:
                raise

            return cast(ComponentValue, default)

    def has(self, entity: str, component: str) -> bool:
        """Report whether an entity has a component."""

        components = self._state.get(entity)

        return components is not None and component in components

    def components(self, entity: str) -> Mapping[str, ComponentValue]:
        """Return a read-only view of an entity's components.

        The view is not a copy, so it reflects later changes to the entity.
        """

        return MappingProxyType(self._state[entity])

    def create(self, entity: str, components: ComponentDict) -> None:
        if entity in self._state:
            raise ValueError(f"Entity already exists: {entity}")

        self._state[entity] = dict(components)

    def delete(self, entity: str) -> None:
        del self._state[entity]

    def set(
        self,
        entity: str,
        component: str,
        value: ComponentValue,
    ) -> None:
        self._state[entity][component] = value

    def copy(self) -> "World":
        """Return an independent working copy."""

        return World(self._state)

    def replace(self, world: 'World'):
        self._state = world._state

    def snapshot(self) -> dict[str, dict[str, ComponentValue]]:
        """Return an independent serializable snapshot."""

        return {
            entity: dict(components)
            for entity, components in self._state.items()
        }
