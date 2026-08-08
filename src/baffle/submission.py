"""External event submission and reaction processing."""

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from baffle.events import Event, Rejected
from baffle.resolution import (
    Resolution,
    ResolutionLimitError,
    Resolver,
    ResolverConfig,
)
from baffle.rules import ReactRule, Ruleset
from baffle.world import World


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

    # Index into `Trace.entries` of the transaction whose reaction produced
    # this root. `None` for the externally submitted event. Reaction alone
    # cannot identify the parent, because equal events may be emitted by
    # more than one transaction.
    parent: int | None = None


@dataclass(frozen=True)
class Trace:
    """The complete result of one externally submitted event."""

    entries: tuple[TraceEntry, ...]

    @property
    def root(self) -> Resolution:
        """Return the resolution of the externally submitted event."""

        return self.entries[0].resolution


def submit(
    world: World,
    event: Event,
    ruleset: Ruleset | None = None,
    *,
    config: ResolverConfig | None = None,
) -> Trace:
    """Submit an event and process reactions until quiescence."""

    if ruleset is None:
        ruleset = Ruleset()

    resolver = Resolver(
        ruleset.before_rules,
        config=config,
    )
    pending: deque[tuple[Event, Reaction | None, int | None]] = deque(
        [(event, None, None)]
    )
    entries: list[TraceEntry] = []

    while pending:
        root, reaction, parent = pending.popleft()

        # The index this root will occupy, so its reactions can point back.
        index = len(entries)

        try:
            resolution = resolver.resolve(world, root)
        except ResolutionLimitError as error:
            # Roots resolved before the limit stay committed. Report them so
            # the caller can see how the world reached its current state.
            error.trace = Trace(entries=tuple(entries))
            raise

        entries.append(
            TraceEntry(
                resolution=resolution,
                reaction=reaction,
                parent=parent,
            )
        )
        pending.extend(
            (emitted, origin, index)
            for emitted, origin in _collect_reactions(
                world,
                resolution,
                ruleset.react_rules,
            )
        )

    return Trace(entries=tuple(entries))


def _collect_reactions(
    world: World,
    resolution: Resolution,
    rules: tuple[ReactRule[Any], ...],
) -> tuple[tuple[Event, Reaction], ...]:
    if resolution.accepted:
        observed: Iterable[Event] = _accepted_events(resolution)
    else:
        observed = (_rejected_event(resolution),)

    emitted: list[tuple[Event, Reaction]] = []

    for source in observed:
        for rule in rules:
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


def _accepted_events(resolution: Resolution) -> Iterable[Event]:
    """Yield accepted events in execution order."""

    for requirement in resolution.requirements:
        yield from _accepted_events(requirement.resolution)

    yield resolution.event


def _rejected_event(resolution: Resolution) -> Rejected:
    """Describe the direct rejection within a root resolution."""

    rejected = resolution.rejected_resolution

    # A REJECTED resolution always carries its rejection, so this only fires
    # if an unsuccessful resolution was built without one.
    if rejected is None or rejected.rejection is None:
        raise ValueError("Unsuccessful resolution contains no direct rejection")

    return Rejected(
        root=resolution.event,
        event=rejected.event,
        rejection=rejected.rejection,
    )
