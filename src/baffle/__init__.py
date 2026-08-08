"""A rules engine for composable event-driven simulations."""

from baffle.events import Create, Delete, Event, Rejected, Rejection, Set
from baffle.resolution import (
    Requirement,
    Resolution,
    ResolutionLimitError,
    ResolutionStatus,
    Resolver,
    ResolverConfig,
    resolve,
)
from baffle.rules import (
    ReactRule,
    RejectRule,
    RequireRule,
    Rule,
    Ruleset,
    react,
    reject,
    require,
)
from baffle.submission import Reaction, Trace, TraceEntry, submit
from baffle.world import World

__all__ = [
    "Create",
    "Delete",
    "Event",
    "ReactRule",
    "Reaction",
    "RejectRule",
    "Rejected",
    "Rejection",
    "Requirement",
    "RequireRule",
    "Resolution",
    "ResolutionLimitError",
    "ResolutionStatus",
    "Resolver",
    "ResolverConfig",
    "Rule",
    "Ruleset",
    "Set",
    "Trace",
    "TraceEntry",
    "World",
    "react",
    "reject",
    "require",
    "resolve",
    "submit",
]
