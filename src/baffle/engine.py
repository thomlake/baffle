"""External event submission and reaction processing."""

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from baffle.events import Event, Rejected, Rejection
from baffle.resolve import (
    BeforeRule,
    Resolution,
    ResolutionStatus,
    Resolver,
    ResolverConfig,
)
from baffle.rules import ReactRule, RejectRule, RequireRule
from baffle.world import World


type Rule = RequireRule[Any] | RejectRule[Any] | ReactRule[Any]


@dataclass(frozen=True)
class Reaction:
    """The reaction rule and observed event that produced a root event."""

    rule: ReactRule[Any]
    source: Event


@dataclass(frozen=True)
class TraceEntry:
    """One root transaction and its origin."""

    resolution: Resolution
    reaction: Reaction | None = None


@dataclass(frozen=True)
class Trace:
    """The complete result of one externally submitted event."""

    entries: tuple[TraceEntry, ...]

    @property
    def root(self) -> Resolution:
        """Return the resolution of the externally submitted event."""

        return self.entries[0].resolution


class Engine:
    """A configured collection of event-processing rules."""

    def __init__(
        self,
        rules: Iterable[Rule] = (),
        *,
        resolver_config: ResolverConfig | None = None,
    ) -> None:
        self._before_rules: list[BeforeRule] = []
        self._react_rules: list[ReactRule[Any]] = []
        self._resolver_config = (
            ResolverConfig()
            if resolver_config is None
            else resolver_config
        )

        for rule in rules:
            self.add(rule)

    def add(self, rule: Rule) -> None:
        """Add a rule while preserving its phase-relative order."""

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
    ) -> Trace:
        """Submit an event and process reactions until quiescence."""

        resolver = Resolver(
            self._before_rules,
            config=self._resolver_config,
        )

        pending: deque[tuple[Event, Reaction | None]] = deque(
            [(event, None)]
        )
        entries: list[TraceEntry] = []

        while pending:
            root, reaction = pending.popleft()
            resolution = resolver.resolve(world, root)

            entries.append(
                TraceEntry(
                    resolution=resolution,
                    reaction=reaction,
                )
            )

            pending.extend(
                self._run_reactions(
                    world,
                    resolution,
                )
            )

        return Trace(entries=tuple(entries))

    def _run_reactions(
        self,
        world: World,
        resolution: Resolution,
    ) -> tuple[tuple[Event, Reaction], ...]:
        if resolution.accepted:
            observed: Iterable[Event] = _accepted_events(resolution)
        else:
            observed = (_rejected_event(resolution),)

        emitted: list[tuple[Event, Reaction]] = []

        for source in observed:
            for rule in self._react_rules:
                if not isinstance(source, rule.event_type):
                    continue

                events = tuple(rule.run(world, source))

                for event in events:
                    emitted.append(
                        (
                            event,
                            Reaction(
                                rule=rule,
                                source=source,
                            ),
                        )
                    )

        return tuple(emitted)


def _accepted_events(
    resolution: Resolution,
) -> Iterable[Event]:
    """Yield accepted events in execution order."""

    for requirement in resolution.requirements:
        yield from _accepted_events(requirement.resolution)

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

    if resolution.status is ResolutionStatus.REJECTED:
        if resolution.rejection is None:
            raise ValueError(
                "Rejected resolution has no rejection"
            )

        return resolution.event, resolution.rejection

    for requirement in resolution.requirements:
        child = requirement.resolution

        if not child.accepted:
            return _find_rejection(child)

    raise ValueError(
        "Unsuccessful resolution contains no direct rejection"
    )
