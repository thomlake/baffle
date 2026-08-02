"""Transactional event resolution."""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from baffle.events import Event, Rejection
from baffle.rules import RejectRule, RequireRule
from baffle.types import ComponentValue, State
from baffle.world import World


type BeforeRule = RequireRule[Any] | RejectRule[Any]


@dataclass(frozen=True)
class Resolution:
    """The causal result of resolving one event."""

    event: Event
    children: tuple["Resolution", ...] = ()
    rejection: Rejection | None = None

    @property
    def accepted(self) -> bool:
        return self.rejection is None


@dataclass(frozen=True)
class Transaction:
    """The result of resolving one root event."""

    resolution: Resolution
    state: dict[str, dict[str, ComponentValue]]

    @property
    def committed(self) -> bool:
        return self.resolution.accepted


def resolve(
    state: State,
    event: Event,
    rules: Iterable[BeforeRule] = (),
) -> Transaction:
    """Resolve one root event as an atomic transaction."""

    original = {
        entity: dict(components)
        for entity, components in state.items()
    }
    world = World(original)

    resolution = _resolve(
        world,
        event,
        tuple(rules),
    )

    return Transaction(
        resolution=resolution,
        state=world.snapshot() if resolution.accepted else original,
    )


def _resolve(
    world: World,
    event: Event,
    rules: tuple[BeforeRule, ...],
) -> Resolution:
    children: list[Resolution] = []

    for rule in rules:
        if not isinstance(event, rule.event_type):
            continue

        if isinstance(rule, RequireRule):
            # Drain the emitter before resolving anything so the rule observes
            # one stable version of the world.
            required_events = tuple(rule.run(world, event))

            for required_event in required_events:
                child = _resolve(world, required_event, rules)
                children.append(child)

                if child.rejection is not None:
                    return Resolution(
                        event=event,
                        children=tuple(children),
                        rejection=Rejection(
                            reason="required_event_rejected",
                            cause=child.rejection,
                        ),
                    )

        elif isinstance(rule, RejectRule):
            rejection = rule.run(world, event)

            if rejection is not None:
                return Resolution(
                    event=event,
                    children=tuple(children),
                    rejection=rejection,
                )

    event.apply(world)

    return Resolution(
        event=event,
        children=tuple(children),
    )
