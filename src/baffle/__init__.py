"""A rules engine for composable event-driven simulations."""

from baffle.engine import Engine, Reaction, Trace, TraceEntry
from baffle.events import Create, Delete, Event, Rejected, Rejection, Set
from baffle.resolve import (
    Requirement,
    Resolution,
    ResolutionLimitError,
    ResolutionStatus,
    Resolver,
    ResolverConfig,
    resolve,
)
from baffle.rules import ReactRule, RejectRule, RequireRule
from baffle.world import World

__all__ = [
    "Create",
    "Delete",
    "Engine",
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
    "Set",
    "Trace",
    "TraceEntry",
    "World",
    "resolve",
]
