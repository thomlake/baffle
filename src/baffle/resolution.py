"""Transactional event resolution."""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from baffle.events import Event, Rejection
from baffle.rules import BeforeRule, RejectRule, RequireRule
from baffle.world import World

if TYPE_CHECKING:
    from baffle.submission import Trace


class ResolutionLimitError(RuntimeError):
    """Raised when event resolution exceeds a configured limit.

    A limit can be exceeded partway through a submission, after earlier root
    transactions have already committed. The attached context describes what
    happened so callers are not left with an advanced world and no record of
    how it got there.
    """

    def __init__(
        self,
        message: str,
        *,
        event: Event | None = None,
        trace: "Trace | None" = None,
    ) -> None:
        super().__init__(message)

        # The event whose resolution hit the limit. Set by the resolver.
        self.event = event

        # Root transactions that committed before the limit. Submission
        # processing tracks these and attaches them on the way out.
        self.trace = trace


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

    @property
    def rejected_resolution(self) -> "Resolution | None":
        """The resolution a rule directly rejected, or None if accepted.

        Returns `self` when this resolution is the rejected one, so callers
        read `rejection` and `rejected_by` the same way whether a rule
        rejected this event or one of its requirements.

        Requirements are ordered and short-circuiting, so an unsuccessful
        resolution has exactly one unsuccessful requirement to follow.
        """

        if self.status is ResolutionStatus.REJECTED:
            return self

        for requirement in self.requirements:
            if not requirement.resolution.accepted:
                return requirement.resolution.rejected_resolution

        return None


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

        world_copy = world.copy()
        resolution = self._resolve(
            world_copy,
            event,
            depth=0,
        )

        if resolution.accepted:
            world.replace(world_copy)

        return resolution

    def _resolve(
        self,
        world: World,
        event: Event,
        *,
        depth: int,
    ) -> Resolution:
        self._check_depth(event, depth)
        self._consume_event(event)

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

    def _check_depth(self, event: Event, depth: int) -> None:
        if depth > self._config.max_depth:
            raise ResolutionLimitError(
                "Maximum requirement depth exceeded: "
                f"{self._config.max_depth}",
                event=event,
            )

    def _consume_event(self, event: Event) -> None:
        if self._remaining_events <= 0:
            raise ResolutionLimitError(
                "Maximum event count exceeded: "
                f"{self._config.max_events}",
                event=event,
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
