"""World state representation."""

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

    def copy(self) -> World:
        """Return an independent working copy."""

        return World(self._state)

    def _replace(self, other: World) -> None:
        """Replace this world with an independent copy of another world."""

        self._state = other.snapshot()

    def snapshot(self) -> dict[str, dict[str, ComponentValue]]:
        """Return an independent serializable snapshot."""

        return {
            entity: dict(components)
            for entity, components in self._state.items()
        }
