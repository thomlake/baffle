"""Events processed by Baffle."""

from dataclasses import dataclass, field

from baffle.types import Components, ComponentValue
from baffle.world import World


@dataclass(frozen=True)
class Event:
    """An occurrence that may be required or reacted to."""

    def apply(self, world: World) -> None:
        """Apply this event's direct state transformation.

        Most higher-level events are signals whose behavior is expressed through
        required child events, so the default transformation is a no-op.
        """


@dataclass(frozen=True)
class Rejection:
    """The reason a rule rejected an event."""

    reason: str
    cause: Rejection | None = None


@dataclass(frozen=True)
class Rejected(Event):
    """Report that an event was rejected."""

    event: Event
    rejection: Rejection


@dataclass(frozen=True)
class Create(Event):
    """Create an entity."""

    entity: str
    components: Components = field(default_factory=dict)

    def apply(self, world: World) -> None:
        world.create(self.entity, self.components)


@dataclass(frozen=True)
class Delete(Event):
    """Delete an entity."""

    entity: str

    def apply(self, world: World) -> None:
        world.delete(self.entity)


@dataclass(frozen=True)
class Set(Event):
    """Set or create a component on an existing entity."""

    entity: str
    component: str
    value: ComponentValue

    def apply(self, world: World) -> None:
        world.set(self.entity, self.component, self.value)
