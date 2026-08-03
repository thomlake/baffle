"""Transactional event resolution."""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from baffle.events import Event, Rejection
from baffle.rules import RejectRule, RequireRule
from baffle.world import World


type BeforeRule = RequireRule[Any] | RejectRule[Any]


@dataclass(frozen=True)
class Resolution:
    """The causal result of resolving one event."""

    event: Event
    children: tuple[Resolution, ...] = ()
    rejection: Rejection | None = None

    @property
    def accepted(self) -> bool:
        return (
            self.rejection is None
            and all(child.accepted for child in self.children)
        )

    @property
    def rejected(self) -> bool:
        return self.rejection is not None


def resolve(
    world: World,
    event: Event,
    rules: Iterable[BeforeRule] = (),
) -> Resolution:
    """Atomically resolve one event and its requirements."""

    before_rules = tuple(rules)

    for rule in before_rules:
        if not isinstance(rule, (RequireRule, RejectRule)):
            raise TypeError(
                "resolve() accepts only RequireRule and RejectRule; "
                f"received {type(rule).__name__}"
            )

    working = world.copy()
    resolution = _resolve(working, event, before_rules)

    if resolution.accepted:
        world._replace(working)

    return resolution


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
            required_events = tuple(rule.run(world, event))

            for required_event in required_events:
                child = _resolve(world, required_event, rules)
                children.append(child)

                if not child.accepted:
                    return Resolution(
                        event=event,
                        children=tuple(children),
                    )

        elif isinstance(rule, RejectRule):
            rejection = rule.run(world, event)

            if rejection is not None:
                return Resolution(
                    event=event,
                    children=tuple(children),
                    rejection=rejection,
                )

        else:
            raise TypeError(
                f"Unknown before rule: {type(rule).__name__}"
            )

    event.apply(world)

    return Resolution(
        event=event,
        children=tuple(children),
    )
