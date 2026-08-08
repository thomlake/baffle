"""Rule definitions and compiled rule collections."""

import heapq
import inspect
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import Any, Protocol, cast, get_type_hints, overload

from baffle.events import Event, Rejected, Rejection
from baffle.world import World


@dataclass(frozen=True)
class Rule[E: Event, R]:
    """A named callback that handles one family of events."""

    name: str
    event_type: type[E]
    run: Callable[[World, E], R]
    after: tuple[str, ...] = ()
    before: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Rule names must not be empty")

        if not isinstance(self.event_type, type) or not issubclass(
            self.event_type,
            Event,
        ):
            raise TypeError("Rule event types must be Event subclasses")

    def __call__(self, world: World, event: E) -> R:
        """Run the callback while keeping decorated rules callable."""

        return self.run(world, event)


@dataclass(frozen=True)
class RequireRule[E: Event](Rule[E, Iterable[Event]]):
    """Emit events that must succeed before an event may execute."""


@dataclass(frozen=True)
class RejectRule[E: Event](Rule[E, Rejection | None]):
    """Reject an event based on the current world."""


@dataclass(frozen=True)
class ReactRule[E: Event](Rule[E, Iterable[Event]]):
    """Emit subsequent events in reaction to a resolved event."""


type BeforeRule = RequireRule[Any] | RejectRule[Any]
type AnyRule = BeforeRule | ReactRule[Any]


class _RequireDecorator(Protocol):
    def __call__[E: Event](
        self,
        run: Callable[[World, E], Iterable[Event]],
    ) -> RequireRule[E]: ...


class _RejectDecorator(Protocol):
    def __call__[E: Event](
        self,
        run: Callable[[World, E], Rejection | None],
    ) -> RejectRule[E]: ...


class _ReactDecorator(Protocol):
    def __call__[E: Event](
        self,
        run: Callable[[World, E], Iterable[Event]],
    ) -> ReactRule[E]: ...


@overload
def require[E: Event](
    run: Callable[[World, E], Iterable[Event]],
    /,
) -> RequireRule[E]: ...


@overload
def require(
    *,
    name: str | None = None,
    after: tuple[str, ...] = (),
    before: tuple[str, ...] = (),
) -> _RequireDecorator: ...


def require(
    run: Callable[[World, Any], Iterable[Event]] | None = None,
    /,
    *,
    name: str | None = None,
    after: tuple[str, ...] = (),
    before: tuple[str, ...] = (),
) -> RequireRule[Any] | _RequireDecorator:
    """Create a require rule from an annotated callback."""

    def decorate(
        callback: Callable[[World, Any], Iterable[Event]],
    ) -> RequireRule[Any]:
        return RequireRule(
            _rule_name(callback, name),
            _event_type(callback),
            callback,
            after=after,
            before=before,
        )

    if run is None:
        return cast(_RequireDecorator, decorate)

    return decorate(run)


@overload
def reject[E: Event](
    run: Callable[[World, E], Rejection | None],
    /,
) -> RejectRule[E]: ...


@overload
def reject(
    *,
    name: str | None = None,
    after: tuple[str, ...] = (),
    before: tuple[str, ...] = (),
) -> _RejectDecorator: ...


def reject(
    run: Callable[[World, Any], Rejection | None] | None = None,
    /,
    *,
    name: str | None = None,
    after: tuple[str, ...] = (),
    before: tuple[str, ...] = (),
) -> RejectRule[Any] | _RejectDecorator:
    """Create a reject rule from an annotated callback."""

    def decorate(
        callback: Callable[[World, Any], Rejection | None],
    ) -> RejectRule[Any]:
        return RejectRule(
            _rule_name(callback, name),
            _event_type(callback),
            callback,
            after=after,
            before=before,
        )

    if run is None:
        return cast(_RejectDecorator, decorate)

    return decorate(run)


@overload
def react[E: Event](
    run: Callable[[World, E], Iterable[Event]],
    /,
) -> ReactRule[E]: ...


@overload
def react(
    *,
    name: str | None = None,
    after: tuple[str, ...] = (),
    before: tuple[str, ...] = (),
) -> _ReactDecorator: ...


def react(
    run: Callable[[World, Any], Iterable[Event]] | None = None,
    /,
    *,
    name: str | None = None,
    after: tuple[str, ...] = (),
    before: tuple[str, ...] = (),
) -> ReactRule[Any] | _ReactDecorator:
    """Create a react rule from an annotated callback."""

    def decorate(
        callback: Callable[[World, Any], Iterable[Event]],
    ) -> ReactRule[Any]:
        return ReactRule(
            _rule_name(callback, name),
            _event_type(callback),
            callback,
            after=after,
            before=before,
        )

    if run is None:
        return cast(_ReactDecorator, decorate)

    return decorate(run)


def _rule_name(run: Callable[..., object], name: str | None) -> str:
    """Use an explicit decorator name or derive one from its callback."""

    if name is not None:
        if not name:
            raise ValueError("Rule names must not be empty")

        return name

    try:
        return run.__name__
    except AttributeError:
        raise TypeError(
            "Rules using unnamed callables require an explicit name"
        ) from None


def _event_type(run: Callable[..., object]) -> type[Event]:
    """Read the handled event type from a rule callback."""

    parameters = tuple(inspect.signature(run).parameters.values())

    if len(parameters) != 2 or any(
        parameter.kind
        not in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )
        for parameter in parameters
    ):
        raise TypeError(
            "Rule callbacks must accept exactly two positional arguments: "
            "world and event"
        )

    event_parameter = parameters[1]
    event_type = get_type_hints(run).get(event_parameter.name)

    if not isinstance(event_type, type) or not issubclass(event_type, Event):
        raise TypeError(
            "A rule callback's event parameter must be annotated with an "
            "Event subclass"
        )

    return event_type


@dataclass(frozen=True, init=False)
class Ruleset:
    """An immutable, validated collection of rules compiled by phase."""

    rules: tuple[AnyRule, ...]
    before_rules: tuple[BeforeRule, ...]
    react_rules: tuple[ReactRule[Any], ...]

    def __init__(self, rules: Iterable[AnyRule] = ()) -> None:
        rules = tuple(rules)

        for rule in rules:
            if not isinstance(rule, (RequireRule, RejectRule, ReactRule)):
                raise TypeError(f"Unknown rule type: {type(rule).__name__}")

        _validate_unique_names(rules)

        before_rules: list[BeforeRule] = []
        react_rules: list[ReactRule[Any]] = []

        for rule in rules:
            if isinstance(rule, (RequireRule, RejectRule)):
                # Rejected is synthesized after a failed transaction and is
                # never itself resolved by submission processing.
                if issubclass(rule.event_type, Rejected):
                    raise TypeError(
                        "Rejected is observation-only; dispatch on it with "
                        f"ReactRule, not {type(rule).__name__}"
                    )

                before_rules.append(rule)
            else:
                react_rules.append(rule)

        _validate_phase_references(rules)

        object.__setattr__(self, "rules", rules)
        object.__setattr__(
            self,
            "before_rules",
            sort_rules(before_rules),
        )
        object.__setattr__(
            self,
            "react_rules",
            sort_rules(react_rules),
        )

    def __iter__(self) -> Iterator[AnyRule]:
        return iter(self.rules)

    def __len__(self) -> int:
        return len(self.rules)


def _validate_unique_names(rules: tuple[AnyRule, ...]) -> None:
    seen: set[str] = set()

    for rule in rules:
        if rule.name in seen:
            raise ValueError(f"Duplicate rule name: {rule.name!r}")

        seen.add(rule.name)


def _validate_phase_references(rules: tuple[AnyRule, ...]) -> None:
    by_name = {rule.name: rule for rule in rules}

    for rule in rules:
        for reference in (*rule.after, *rule.before):
            try:
                target = by_name[reference]
            except KeyError:
                raise ValueError(
                    f"Rule {rule.name!r} references unknown rule "
                    f"{reference!r}"
                ) from None

            rule_is_react = isinstance(rule, ReactRule)
            target_is_react = isinstance(target, ReactRule)

            if rule_is_react != target_is_react:
                raise ValueError(
                    f"Rule {rule.name!r} cannot be ordered relative to "
                    f"{reference!r} because they run in different phases"
                )


def sort_rules[R: Rule[Any, Any]](
    rules: Iterable[R],
) -> tuple[R, ...]:
    """Stably sort rules according to their ordering constraints."""

    rules = tuple(rules)
    index_by_name = {rule.name: index for index, rule in enumerate(rules)}

    if len(index_by_name) != len(rules):
        seen: set[str] = set()

        for rule in rules:
            if rule.name in seen:
                raise ValueError(f"Duplicate rule name: {rule.name!r}")

            seen.add(rule.name)

    # An edge source -> target means source must precede target.
    outgoing: list[set[int]] = [set() for _ in rules]
    indegree = [0] * len(rules)

    def add_edge(source_name: str, target_name: str) -> None:
        try:
            source = index_by_name[source_name]
        except KeyError:
            raise ValueError(
                f"Rule {target_name!r} references unknown rule "
                f"{source_name!r}"
            ) from None

        try:
            target = index_by_name[target_name]
        except KeyError:
            raise ValueError(
                f"Rule {source_name!r} references unknown rule "
                f"{target_name!r}"
            ) from None

        if target not in outgoing[source]:
            outgoing[source].add(target)
            indegree[target] += 1

    for rule in rules:
        for name in rule.after:
            add_edge(name, rule.name)

        for name in rule.before:
            add_edge(rule.name, name)

    # Input indexes make the topological ordering deterministic and preserve
    # registration order whenever constraints do not decide between rules.
    ready = [index for index, degree in enumerate(indegree) if degree == 0]
    heapq.heapify(ready)

    result: list[R] = []

    while ready:
        source = heapq.heappop(ready)
        result.append(rules[source])

        for target in outgoing[source]:
            indegree[target] -= 1
            if indegree[target] == 0:
                heapq.heappush(ready, target)

    if len(result) != len(rules):
        unresolved = {index for index, degree in enumerate(indegree) if degree > 0}
        details = []

        for child in unresolved:
            blockers = [
                rules[parent].name
                for parent in unresolved
                if child in outgoing[parent]
            ]
            details.append(f"{rules[child].name!r} depends on {blockers!r}")

        raise ValueError("Cyclic rule ordering:\n  " + "\n  ".join(details))

    return tuple(result)
