"""Transactional event resolution."""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from baffle.events import Event, Rejection
from baffle.rules import RejectRule, RequireRule
from baffle.world import World


type BeforeRule = RequireRule[Any] | RejectRule[Any]


class ResolutionLimitError(RuntimeError):
    """Raised when event resolution exceeds a configured limit."""


@dataclass(frozen=True)
class ResolverConfig:
    """Limits applied across one resolver's lifetime."""

    max_depth: int = 100
    max_events: int = 1_000

    def __post_init__(self) -> None:
        if self.max_depth < 0:
            raise ValueError("max_depth must be non-negative")

        if self.max_events < 1:
            raise ValueError("max_events must be positive")


class ResolutionStatus(StrEnum):
    """The outcome of resolving one event."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    ABORTED = "aborted"


@dataclass(frozen=True)
class Requirement:
    """A required child event and the rule that emitted it."""

    rule: RequireRule[Any]
    resolution: "Resolution"


@dataclass(frozen=True)
class Resolution:
    """The causal result of resolving one event."""

    event: Event
    status: ResolutionStatus
    requirements: tuple[Requirement, ...] = ()
    rejection: Rejection | None = None
    rejected_by: RejectRule[Any] | None = None

    @property
    def accepted(self) -> bool:
        return self.status is ResolutionStatus.ACCEPTED

    @property
    def rejected(self) -> bool:
        return self.status is ResolutionStatus.REJECTED

    @property
    def aborted(self) -> bool:
        return self.status is ResolutionStatus.ABORTED


class Resolver:
    """Resolve events atomically using a shared event budget."""

    def __init__(
        self,
        rules: Iterable[BeforeRule] = (),
        *,
        config: ResolverConfig | None = None,
    ) -> None:
        self._rules = tuple(rules)
        self._config = ResolverConfig() if config is None else config
        self._remaining_events = self._config.max_events

        for rule in self._rules:
            if not isinstance(rule, (RequireRule, RejectRule)):
                raise TypeError(
                    "Resolver accepts only RequireRule and RejectRule; "
                    f"received {type(rule).__name__}"
                )

    def resolve(
        self,
        world: World,
        event: Event,
    ) -> Resolution:
        """Atomically resolve one event and its requirements."""

        working = world.copy()
        resolution = self._resolve(
            working,
            event,
            depth=0,
        )

        if resolution.accepted:
            world._replace(working)

        return resolution

    def _resolve(
        self,
        world: World,
        event: Event,
        *,
        depth: int,
    ) -> Resolution:
        self._check_depth(depth)
        self._consume_event()

        requirements: list[Requirement] = []

        for rule in self._rules:
            if not isinstance(event, rule.event_type):
                continue

            if isinstance(rule, RequireRule):
                # Drain one rule invocation before resolving its emissions so
                # that the callback observes one stable world state.
                required_events = tuple(rule.run(world, event))

                for required_event in required_events:
                    child = self._resolve(
                        world,
                        required_event,
                        depth=depth + 1,
                    )
                    requirements.append(
                        Requirement(
                            rule=rule,
                            resolution=child,
                        )
                    )

                    if not child.accepted:
                        return Resolution(
                            event=event,
                            status=ResolutionStatus.ABORTED,
                            requirements=tuple(requirements),
                        )

            elif isinstance(rule, RejectRule):
                rejection = rule.run(world, event)

                if rejection is not None:
                    return Resolution(
                        event=event,
                        status=ResolutionStatus.REJECTED,
                        requirements=tuple(requirements),
                        rejection=rejection,
                        rejected_by=rule,
                    )

            else:
                # Constructor validation should make this unreachable.
                raise TypeError(
                    f"Unknown before rule: {type(rule).__name__}"
                )

        event.apply(world)

        return Resolution(
            event=event,
            status=ResolutionStatus.ACCEPTED,
            requirements=tuple(requirements),
        )

    def _check_depth(self, depth: int) -> None:
        if depth > self._config.max_depth:
            raise ResolutionLimitError(
                "Maximum requirement depth exceeded: "
                f"{self._config.max_depth}"
            )

    def _consume_event(self) -> None:
        if self._remaining_events <= 0:
            raise ResolutionLimitError(
                "Maximum event count exceeded: "
                f"{self._config.max_events}"
            )

        self._remaining_events -= 1


def resolve(
    world: World,
    event: Event,
    rules: Iterable[BeforeRule] = (),
    *,
    config: ResolverConfig | None = None,
) -> Resolution:
    """Resolve one event using a fresh resolver and event budget."""

    return Resolver(
        rules,
        config=config,
    ).resolve(world, event)
