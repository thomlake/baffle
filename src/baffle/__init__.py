"""Baffle: a transactional rules engine for discrete games.

Sokoban, board games, turn-based battles. "Discrete" is about cause and effect, not
about simulated time -- turns and rounds are component state that rules advance.

The central invariant:

    An operation may mutate the transaction's working world, but only the root
    transaction commits the authoritative one.

Everything else follows from that. Prerequisites resolve inside their parent's
transaction, so a chain either happens whole or not at all. Consequences re-enter the
same queue as ordinary root events, so they get the same treatment with no special
casing.
"""

from .engine import (
    MAX_DEPTH,
    MAX_EVENTS_PER_TRANSACTION,
    MAX_TRANSACTIONS,
    Engine,
    SimulationResult,
)
from .errors import EngineFault
from .events import (
    NO_EFFECT,
    Effect,
    Event,
    Failure,
    OperationResult,
    emit,
    lookup,
    registered,
)
from .mechanics import WithinBounds
from .operations import (
    AppendToList,
    ComponentEvent,
    CreateEntity,
    DeleteEntity,
    EntityEvent,
    ExistingEntityEvent,
    ExtendList,
    IncrementComponent,
    MoveEntity,
    NewEntityEvent,
    RemoveComponent,
    RemoveValue,
    SetComponent,
)
from .query import Query
from .records import (
    Attempt,
    Frame,
    Mutation,
    Record,
    RecordLog,
    Replaced,
    RuleFired,
    TransactionBegin,
    TransactionEnd,
)
from .resolve import Resolution, Resolver, Transaction
from .rules import AfterRule, BeforeRule, FailRule, ReplaceRule, Rule, RuleSet
from .state import MISSING, World, state_key
from .types import (
    WORLD,
    ComponentPath,
    Components,
    Entities,
    EntityId,
    JsonScalar,
    JsonValue,
)
from .vectors import EAST, NORTH, SOUTH, WEST, Vec2, delta, manhattan, scale, shift

__all__ = [
    # Engine, and the limits it runs under
    "Engine",
    "SimulationResult",
    "MAX_TRANSACTIONS",
    "MAX_DEPTH",
    "MAX_EVENTS_PER_TRANSACTION",
    # Events, and the two things an operation returns
    "Event",
    "Effect",
    "Failure",
    "OperationResult",
    "NO_EFFECT",
    "emit",
    "lookup",
    "registered",
    # Event base classes, named for the precondition they impose
    "EntityEvent",
    "ExistingEntityEvent",
    "NewEntityEvent",
    "ComponentEvent",
    # Built-in operations
    "CreateEntity",
    "DeleteEntity",
    "MoveEntity",
    "SetComponent",
    "RemoveComponent",
    "IncrementComponent",
    "AppendToList",
    "ExtendList",
    "RemoveValue",
    # Rules the engine ships
    "WithinBounds",
    # Rules
    "Rule",
    "ReplaceRule",
    "BeforeRule",
    "AfterRule",
    "FailRule",
    "RuleSet",
    # The world
    "World",
    "Query",
    "WORLD",
    "MISSING",
    "state_key",
    # Records
    "Record",
    "RecordLog",
    "TransactionBegin",
    "TransactionEnd",
    "Attempt",
    "RuleFired",
    "Replaced",
    "Mutation",
    "Frame",
    # Resolution, for tooling and tests
    "Resolver",
    "Resolution",
    "Transaction",
    # Geometry
    "Vec2",
    "NORTH",
    "SOUTH",
    "EAST",
    "WEST",
    "shift",
    "delta",
    "scale",
    "manhattan",
    # Vocabulary and errors
    "EngineFault",
    "Entities",
    "Components",
    "EntityId",
    "ComponentPath",
    "JsonValue",
    "JsonScalar",
]
