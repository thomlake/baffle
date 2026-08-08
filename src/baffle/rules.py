"""Rule definitions."""

import heapq
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Protocol

from baffle.events import Event, Rejection
from baffle.world import World


@dataclass(frozen=True)
class RequireRule[E: Event]:
    """Emit events that must succeed before an event may execute."""

    event_type: type[E]
    run: Callable[[World, E], Iterable[Event]]
    name: str = ""
    after: tuple[str, ...] = ()
    before: tuple[str, ...] = ()


@dataclass(frozen=True)
class RejectRule[E: Event]:
    """Reject an event based on the current world."""

    event_type: type[E]
    run: Callable[[World, E], Rejection | None]
    name: str = ""
    after: tuple[str, ...] = ()
    before: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReactRule[E: Event]:
    """Emit subsequent events in reaction to a resolved event."""

    event_type: type[E]
    run: Callable[[World, E], Iterable[Event]]
    name: str = ""
    after: tuple[str, ...] = ()
    before: tuple[str, ...] = ()


class Rule(Protocol):
    name: str
    after: tuple[str, ...]
    before: tuple[str, ...]


def sort_rules(rules: Iterable[Rule]) -> tuple[Rule, ...]:
    """Sort rules according to their `after` and `before` constraints.

    Among rules not ordered by any constraint, input order is preferred.

    Raises:
        ValueError: If names are duplicated, a referenced rule is missing,
            or the ordering constraints contain a cycle.
    """
    rules = tuple(rules)
    index_by_name: dict[str, int] = {}

    for index, rule in enumerate(rules):
        if rule.name in index_by_name:
            raise ValueError(f"Duplicate rule name: {rule.name!r}")
        index_by_name[rule.name] = index

    # An edge source -> target means source must precede target.
    outgoing: list[set[int]] = [set() for _ in rules]
    indegree = [0] * len(rules)

    def add_edge(source_name: str, target_name: str) -> None:
        try:
            source = index_by_name[source_name]
        except KeyError:
            raise ValueError(
                f"Rule {target_name!r} references unknown rule {source_name!r}"
            ) from None

        try:
            target = index_by_name[target_name]
        except KeyError:
            raise ValueError(
                f"Rule {source_name!r} references unknown rule {target_name!r}"
            ) from None

        # Avoid counting the same relation twice.
        if target not in outgoing[source]:
            outgoing[source].add(target)
            indegree[target] += 1

    for rule in rules:
        # A in B.after => A precedes B.
        for name in rule.after:
            add_edge(name, rule.name)

        # A in B.before => B precedes A.
        for name in rule.before:
            add_edge(rule.name, name)

    # Original indexes provide deterministic tie-breaking.
    ready = [index for index, degree in enumerate(indegree) if degree == 0]
    heapq.heapify(ready)

    result: list[Rule] = []

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
            details.append(
                f"{rules[child].name!r} depends on {blockers!r}"
            )

        raise ValueError(
            "Cyclic rule ordering:\n  " + "\n  ".join(details)
        )

    return tuple(result)
