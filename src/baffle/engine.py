"""External event submission and reaction processing."""

from collections import deque
from collections.abc import Iterable
from typing import Any

from baffle.events import Event, Rejected, Rejection
from baffle.resolve import BeforeRule, Resolution, resolve
from baffle.rules import ReactRule, RejectRule, RequireRule
from baffle.world import World


type Rule = RequireRule[Any] | RejectRule[Any] | ReactRule[Any]


class Engine:
    """A configured collection of event-processing rules."""

    def __init__(self, rules: Iterable[Rule] = ()) -> None:
        self._before_rules: list[BeforeRule] = []
        self._react_rules: list[ReactRule[Any]] = []

        for rule in rules:
            self.add(rule)

    def add(self, rule: Rule) -> None:
        """Add a rule while preserving its relative phase order."""

        if isinstance(rule, (RequireRule, RejectRule)):
            self._before_rules.append(rule)
        elif isinstance(rule, ReactRule):
            self._react_rules.append(rule)
        else:
            raise TypeError(
                f"Unknown rule type: {type(rule).__name__}"
            )

    def submit(
        self,
        world: World,
        event: Event,
    ) -> tuple[Resolution, ...]:
        """Submit an event and process reactions until quiescence."""

        pending = deque([event])
        resolutions: list[Resolution] = []

        while pending:
            root = pending.popleft()

            resolution = resolve(
                world,
                root,
                self._before_rules,
            )
            resolutions.append(resolution)

            pending.extend(
                self._run_reactions(world, resolution)
            )

        return tuple(resolutions)

    def _run_reactions(
        self,
        world: World,
        resolution: Resolution,
    ) -> tuple[Event, ...]:
        if resolution.accepted:
            observed: Iterable[Event] = _accepted_events(resolution)
        else:
            observed = (_rejected_event(resolution),)

        emitted: list[Event] = []

        for event in observed:
            for rule in self._react_rules:
                if isinstance(event, rule.event_type):
                    emitted.extend(rule.run(world, event))

        return tuple(emitted)


def _accepted_events(
    resolution: Resolution,
) -> Iterable[Event]:
    for child in resolution.children:
        yield from _accepted_events(child)

    yield resolution.event


def _rejected_event(
    resolution: Resolution,
) -> Rejected:
    event, rejection = _find_rejection(resolution)

    return Rejected(
        root=resolution.event,
        event=event,
        rejection=rejection,
    )


def _find_rejection(
    resolution: Resolution,
) -> tuple[Event, Rejection]:
    if resolution.rejection is not None:
        return resolution.event, resolution.rejection

    for child in resolution.children:
        if not child.accepted:
            return _find_rejection(child)

    raise ValueError(
        "Unsuccessful resolution contains no direct rejection"
    )
