"""Rule definitions."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from baffle.events import Event, Rejection
from baffle.world import World


@dataclass(frozen=True)
class RequireRule[E: Event]:
    """Emit events that must succeed before an event may execute."""

    event_type: type[E]
    run: Callable[[World, E], Iterable[Event]]


@dataclass(frozen=True)
class RejectRule[E: Event]:
    """Reject an event based on the current world."""

    event_type: type[E]
    run: Callable[[World, E], Rejection | None]


@dataclass(frozen=True)
class ReactRule[E: Event]:
    """Emit subsequent events in reaction to a resolved event."""

    event_type: type[E]
    run: Callable[[World, E], Iterable[Event]]
