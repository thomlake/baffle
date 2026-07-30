"""The built-in mutation events.

Each is an event that knows how to apply itself, so the vocabulary for changing the
world and the vocabulary for reacting to change are the same thing. A game adds
mechanics by subclassing, not by registering callbacks.

Names say verb then target -- ``SetComponent``, ``DeleteEntity``, ``AppendToList`` -- so
a rule reads as a sentence and a log line explains itself.

The base classes exist to declare **preconditions**, which is why they are named for the
state they require rather than for the thing they act on. ``CreateEntity`` and
``DeleteEntity`` both concern an entity, but they demand opposite worlds, and burying
that in a shared name is how it becomes confusing.

Which container operations exist is decided by one rule, the same rule that makes
``MoveEntity`` carry a destination rather than a direction: an event must mean the same
thing whenever it is read. ``AppendToList(value=x)`` and ``RemoveValue(value=x)`` are
specified by value, so they mean the same thing at emission and at execution. An
operation specified by *position* would not -- index 2 is whatever happens to be there
when it runs, and prerequisites resolve in between -- so there is none. A rule that wants
one reads the tuple, computes the result, and sets it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .errors import EngineFault
from .events import NO_EFFECT, Effect, Event, Failure, OperationResult
from .state import World, validate_key
from .types import ComponentPath, EntityId, JsonValue
from .vectors import Vec2


class EntityEvent(Event, abstract=True):
    """An event that names one entity. Imposes no precondition on its own."""

    entity: EntityId


class ExistingEntityEvent(EntityEvent, abstract=True):
    """Requires the entity to be there.

    Hoists a check that four separate operations used to carry their own copy of -- and
    settles once that a missing entity is a *refusal*, an ordinary gameplay outcome,
    rather than something each author has to classify correctly.
    """

    def precheck(self, world: World) -> Failure | None:
        if self.entity not in world:
            return Failure("entity_missing", {"entity": self.entity})
        return None


class NewEntityEvent(EntityEvent, abstract=True):
    """Requires the entity *not* to be there."""

    def precheck(self, world: World) -> Failure | None:
        if self.entity in world:
            return Failure("entity_exists", {"entity": self.entity})
        return None


class ComponentEvent(ExistingEntityEvent, abstract=True):
    """An event that targets one component of one existing entity."""

    component: ComponentPath

    def __post_init__(self) -> None:
        validate_key(self.component)

    def _context(self) -> dict[str, Any]:
        return {"entity": self.entity, "component": self.component}


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------


class CreateEntity(NewEntityEvent):
    """Add an entity with the given components."""

    components: Mapping[str, JsonValue]

    def apply(self, world: World) -> OperationResult:
        world.create(self.entity, self.components)
        return NO_EFFECT


class DeleteEntity(ExistingEntityEvent):
    """Remove an entity and everything it holds.

    Reports the components it held, which is what a reaction to a death needs in order
    to drop what the thing was carrying.
    """

    def apply(self, world: World) -> OperationResult:
        return Effect({"components": world.delete(self.entity)})


class MoveEntity(ExistingEntityEvent):
    """Place an entity at a concrete destination.

    The destination is concrete rather than a direction because an event must mean the
    same thing whenever it is read. Deriving it from state on demand would leave it
    ambiguous between the moment it was emitted and the moment it executes -- and those
    differ, because prerequisites resolve in between.

    Where the entity came *from* is reported in the effect, which is where an ``after``
    rule should read it. Direction-based authoring belongs in a replace rule that
    resolves a step into a destination while state is at hand.

    Whether a destination is *legal* is a rule, not part of this operation. See
    :class:`~baffle.mechanics.WithinBounds`.
    """

    destination: Vec2

    def apply(self, world: World) -> OperationResult:
        # Read rather than take `set`'s displaced value, which would be MISSING for an
        # entity that has no position -- turning "you cannot move a positionless thing"
        # from a fault into a silent create.
        origin = world.value(self.entity, "position")
        world.set(self.entity, "position", self.destination)
        return Effect({"origin": origin, "destination": self.destination})


# ---------------------------------------------------------------------------
# Scalar components
# ---------------------------------------------------------------------------


class SetComponent(ComponentEvent):
    """Replace a component value, introducing it when absent.

    ``previous`` is :data:`~baffle.state.MISSING` when the component did not exist, which
    is how an ``after`` rule tells a create from a replace.
    """

    value: JsonValue
    create: bool = True

    def apply(self, world: World) -> OperationResult:
        previous = world.set(
            self.entity, self.component, self.value, create=self.create
        )
        return Effect({"previous": previous, "current": self.value})


class RemoveComponent(ComponentEvent):
    """Drop a component entirely. Refuses when it is not there."""

    def apply(self, world: World) -> OperationResult:
        if self.component not in world[self.entity]:
            return Failure("component_missing", self._context())
        return Effect({"previous": world.unset(self.entity, self.component)})


class IncrementComponent(ComponentEvent):
    """Add to an integer component, optionally clamped.

    The clamps are what let one operation cover health, mana, ammo, keys, and cooldowns:
    crossing a bound is a refusal, which propagates and rolls back whatever required it.

    Relative rather than absolute, so it means the same thing however much a sibling
    prerequisite changed the value in between. That is what it has over reading the
    component and setting the sum.
    """

    value: int
    minimum: int | None = None
    maximum: int | None = None

    def apply(self, world: World) -> OperationResult:
        current = world.value(self.entity, self.component)
        if isinstance(current, bool) or not isinstance(current, int):
            raise EngineFault(
                "Cannot increment a non-integer component",
                event=self,
                entity=self.entity,
                component=self.component,
            )
        candidate = current + self.value
        context = {**self._context(), "current": current, "value": self.value}
        if self.minimum is not None and candidate < self.minimum:
            return Failure("minimum_violated", {**context, "minimum": self.minimum})
        if self.maximum is not None and candidate > self.maximum:
            return Failure("maximum_violated", {**context, "maximum": self.maximum})
        world.set(self.entity, self.component, candidate)
        return Effect({"previous": current, "current": candidate})


# ---------------------------------------------------------------------------
# List components
# ---------------------------------------------------------------------------
#
# A "list" component is stored as a tuple, because component values are immutable. These
# read it, build the new one, and set it -- so each records a single replacement rather
# than an in-place edit, and the state can never be caught half-changed.


class AppendToList(ComponentEvent):
    """Add one value to the end of a list component.

    The value may itself be a tuple, in which case it lands as a single element. Use
    :class:`ExtendList` to concatenate.
    """

    value: JsonValue

    def apply(self, world: World) -> OperationResult:
        target = world.value(self.entity, self.component)
        if not isinstance(target, tuple):
            return Failure("not_a_list", self._context())
        current = (*target, self.value)
        world.set(self.entity, self.component, current)
        return Effect({"previous": target, "current": current})


class ExtendList(ComponentEvent):
    """Add several values to the end of a list component."""

    values: tuple[JsonValue, ...]

    def apply(self, world: World) -> OperationResult:
        target = world.value(self.entity, self.component)
        if not isinstance(target, tuple):
            return Failure("not_a_list", self._context())
        current = (*target, *self.values)
        world.set(self.entity, self.component, current)
        return Effect({"previous": target, "current": current})


class RemoveValue(ComponentEvent):
    """Remove the first occurrence of a value from a list component."""

    value: JsonValue

    def apply(self, world: World) -> OperationResult:
        target = world.value(self.entity, self.component)
        if not isinstance(target, tuple):
            return Failure("not_a_list", self._context())
        try:
            index = target.index(self.value)
        except ValueError:
            return Failure("value_absent", {**self._context(), "value": self.value})
        current = (*target[:index], *target[index + 1 :])
        world.set(self.entity, self.component, current)
        return Effect(
            {
                "removed": self.value,
                "index": index,
                "previous": target,
                "current": current,
            }
        )
