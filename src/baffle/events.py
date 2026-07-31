"""Events, and the two things an operation can produce.

An event is an immutable value object describing an intent. It carries only what its
author supplied -- never anything derived from state -- so it means the same thing
whenever it is read: when it is emitted, when it executes, when it is hashed for a
transposition table, when it is replayed from a log.

What an operation *computes* goes in the :class:`Effect`, which ``after`` rules receive.
That split is what keeps events honest.

An operation returns ``Effect | Failure``. There is no third wrapper type: a rejection
is a :class:`Failure`, the same class a ``before`` rule returns to refuse an event, so
"no" has one name everywhere.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, ClassVar, dataclass_transform

from .errors import EngineFault
from .naming import snake_case

if TYPE_CHECKING:  # pragma: no cover
    from .state import World

_REGISTRY: dict[str, type[Event]] = {}


@dataclass_transform(
    frozen_default=True,
    field_specifiers=(dataclasses.field, dataclasses.Field),
)
@dataclasses.dataclass(frozen=True)
class Event:
    """Base class for every event.

    Subclasses become frozen dataclasses automatically and register under their
    :attr:`name`, so two classes claiming one name fail at import rather than becoming a
    silent no-op at runtime.

    The :func:`~typing.dataclass_transform` marker is what makes that visible to a type
    checker. :meth:`__init_subclass__` synthesizes ``__init__`` at *runtime*, which static
    analysis cannot see -- without the marker every event appears to take no arguments, so
    a typo in a field name looks exactly like a correct call. Since checked construction is
    the whole reason rules are authored in Python, the marker is load-bearing.

    Pass ``abstract=True`` for a base class that exists only to be subclassed and
    matched against::

        class EntityEvent(Event, abstract=True):
            entity: EntityId
    """

    name: ClassVar[str]
    abstract: ClassVar[bool] = True

    def __init_subclass__(cls, *, abstract: bool = False, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # Applied without slots, which would return a new class this hook cannot bind.
        dataclasses.dataclass(frozen=True)(cls)
        cls.abstract = abstract
        if abstract:
            return
        if "name" not in cls.__dict__:
            cls.name = snake_case(cls.__name__)
        existing = _REGISTRY.get(cls.name)
        if existing is not None and existing is not cls:
            raise EngineFault(
                f"Two event classes claim the name {cls.name!r}: "
                f"{existing.__qualname__} and {cls.__qualname__}"
            )
        _REGISTRY[cls.name] = cls

    def precheck(self, world: World) -> Failure | None:
        """Refuse before :meth:`apply` runs, or return None to proceed.

        Exists so a check shared by many events is declared once on a base class rather
        than hand-rolled in each operation. See
        :class:`~baffle.operations.ExistingEntityEvent`.
        """
        return None

    def apply(self, world: World) -> OperationResult:
        """Change the world, or refuse.

        The default is a pure signal: it succeeds and changes nothing. Events exist to
        be reacted to as much as to do work.
        """
        return NO_EFFECT


def lookup(name: str) -> type[Event]:
    """Resolve a registered event name, for replay and data-driven emission."""
    try:
        return _REGISTRY[name]
    except KeyError:
        raise EngineFault(f"No event registered under the name {name!r}") from None


def registered() -> Mapping[str, type[Event]]:
    return dict(_REGISTRY)


def emit(name: str, **fields: Any) -> Event:
    """Construct a registered event by name.

    The escape hatch for data-driven events, where the class is not known at authoring
    time. Prefer calling the class directly -- that is checked statically.
    """
    return lookup(name)(**fields)


@dataclasses.dataclass(frozen=True)
class Failure:
    """A refusal. Not an error: a legal action that did not work.

    Returned by an operation that will not run and by a ``before`` rule that vetoes an
    event -- one class for both, because they mean the same thing.

    A parent wraps a child's failure rather than replacing it, so the whole causal chain
    survives to be rendered or inspected.
    """

    reason: str
    data: dict[str, Any] = dataclasses.field(default_factory=dict)
    cause: Failure | None = None

    @property
    def direct(self) -> bool:
        """True when this is the originating failure, not a wrapper around one."""
        return self.cause is None

    @property
    def root(self) -> Failure:
        """The originating failure at the bottom of the chain."""
        failure = self
        while failure.cause is not None:
            failure = failure.cause
        return failure

    def chain(self) -> tuple[Failure, ...]:
        """Every failure from this one down to the root, outermost first."""
        trail: list[Failure] = []
        failure: Failure | None = self
        while failure is not None:
            trail.append(failure)
            failure = failure.cause
        return tuple(trail)


@dataclasses.dataclass(frozen=True)
class Effect:
    """What an operation did, beyond the change itself.

    ``after`` rules receive :attr:`details`, which is how a reaction learns where an
    entity moved *from* without the event having to carry a state-dependent field.
    """

    details: dict[str, Any] = dataclasses.field(default_factory=dict)


type OperationResult = Effect | Failure

#: Shared instance for the common "succeeded, computed nothing" case.
NO_EFFECT = Effect()
