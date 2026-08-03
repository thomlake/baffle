"""External event submission and reaction processing."""

from collections import deque
from collections.abc import Iterable
from typing import Any

from baffle.events import Event, Rejected, Rejection
from baffle.resolve import BeforeRule, Resolution, resolve
from baffle.rules import ReactRule, RejectRule, RequireRule
from baffle.world import World


type Rule = RequireRule[Any] | RejectRule[Any] | ReactRule[Any]


def submit(
    world: World,
    event: Event,
    rules: Iterable[Rule] = (),
) -> tuple[Resolution, ...]:
    """Submit an external event and process reactions until quiescence."""

    before_rules, react_rules = _partition_rules(rules)

    pending = deque([event])
    resolutions: list[Resolution] = []

    while pending:
        root = pending.popleft()
        resolution = resolve(world, root, before_rules)
        resolutions.append(resolution)

        pending.extend(
            _run_reactions(
                world,
                resolution,
                react_rules,
            )
        )

    return tuple(resolutions)


def _partition_rules(
    rules: Iterable[Rule],
) -> tuple[
    tuple[BeforeRule, ...],
    tuple[ReactRule[Any], ...],
]:
    before: list[BeforeRule] = []
    react: list[ReactRule[Any]] = []

    for rule in rules:
        if isinstance(rule, (RequireRule, RejectRule)):
            before.append(rule)
        elif isinstance(rule, ReactRule):
            react.append(rule)
        else:
            raise TypeError(
                f"Unknown rule type: {type(rule).__name__}"
            )

    return tuple(before), tuple(react)


def _run_reactions(
    world: World,
    resolution: Resolution,
    rules: tuple[ReactRule[Any], ...],
) -> tuple[Event, ...]:
    if resolution.accepted:
        observed: Iterable[Event] = _accepted_events(resolution)
    else:
        observed = (_rejected_event(resolution),)

    emitted: list[Event] = []

    for event in observed:
        for rule in rules:
            if isinstance(event, rule.event_type):
                emitted.extend(rule.run(world, event))

    return tuple(emitted)


def _accepted_events(
    resolution: Resolution,
) -> Iterable[Event]:
    """Yield committed events in execution order."""

    for child in resolution.children:
        yield from _accepted_events(child)

    yield resolution.event


def _rejected_event(
    resolution: Resolution,
) -> Rejected:
    """Describe the direct rejection within a root resolution."""

    event, rejection = _find_rejection(resolution)

    return Rejected(
        root=resolution.event,
        event=event,
        rejection=rejection,
    )


def _find_rejection(
    resolution: Resolution,
) -> tuple[Event, Rejection]:
    """Find the single direct rejection in an unsuccessful resolution."""

    if resolution.rejection is not None:
        return resolution.event, resolution.rejection

    for child in resolution.children:
        if not child.accepted:
            return _find_rejection(child)

    raise ValueError(
        "Unsuccessful resolution contains no direct rejection"
    )
