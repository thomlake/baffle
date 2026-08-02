"""World state representation."""

from typing import cast

from baffle.types import Components, ComponentValue, State


class _Missing:
    __slots__ = ()


_MISSING = _Missing()


class World:
    """A mutable working copy of world state."""

    def __init__(self, state: State) -> None:
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
        """Return a component value.

        Missing entities and missing components both raise ``KeyError`` unless a
        default is supplied.
        """

        try:
            return self._state[entity][component]
        except KeyError:
            if default is _MISSING:
                raise

            return cast(ComponentValue, default)

    def create(self, entity: str, components: Components) -> None:
        """Create an entity.

        Raises ``ValueError`` if the entity already exists.
        """

        if entity in self._state:
            raise ValueError(f"Entity already exists: {entity}")

        self._state[entity] = dict(components)

    def delete(self, entity: str) -> None:
        """Delete an entity.

        Raises ``KeyError`` if the entity does not exist.
        """

        del self._state[entity]

    def set(
        self,
        entity: str,
        component: str,
        value: ComponentValue,
    ) -> None:
        """Set or create a component on an existing entity.

        Raises ``KeyError`` if the entity does not exist.
        """

        self._state[entity][component] = value

    def snapshot(self) -> dict[str, dict[str, ComponentValue]]:
        """Return an independent snapshot of the current state."""

        return {
            entity: dict(components)
            for entity, components in self._state.items()
        }
