"""The engine facade, and the consequence cascade it drives.

Stateless with respect to the world: state goes in, state comes out. That is what makes
:meth:`Engine.speculate` possible, and with it tree search -- an engine that owned the
world could not be asked "what would happen if" without being asked twice.

Every event is a root transaction. When one commits, its ``after`` rules produce events;
when one is discarded, its ``fail`` rules do. Either way the products go on the back of
the same queue and are processed exactly like the event the caller submitted -- own
working copy, own prerequisite chain, own commit or discard. That is the whole mechanism
for consequences. There is no second path.

The limits live here, declared once each, because this is the only place that constructs
a :class:`~baffle.resolve.Resolver`.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from .errors import EngineFault
from .events import Event
from .records import Record, RecordLog
from .resolve import Resolver, Transaction
from .rules import Rule, RuleSet
from .state import own
from .types import Entities, EntitiesLike

#: How many root transactions one :meth:`Engine.simulate` may run before the cascade is
#: declared runaway.
MAX_TRANSACTIONS = 1_000

#: How deep a prerequisite chain may go within one transaction.
MAX_DEPTH = 64

#: How many events one transaction may resolve in total. Depth alone is not enough: it
#: permits exponential fan-out, which was how one transaction resolved 8191 events at
#: depth 12.
MAX_EVENTS_PER_TRANSACTION = 1_024


@dataclass(frozen=True)
class SimulationResult:
    """Everything that happened, and the world it left behind."""

    entities: Entities
    transactions: tuple[Transaction, ...]
    records: tuple[Record, ...]

    @property
    def root(self) -> Transaction:
        """The transaction for the event the caller submitted."""
        return self.transactions[0]

    @property
    def committed(self) -> bool:
        return self.root.committed


class Engine:
    """A compiled rule set plus the limits it runs under.

    Parameters
    ----------
    rules:
        Rule instances. Order matters only as the tiebreak between rules whose relative
        order is otherwise unconstrained.
    narrate:
        Record attempts, rule firings, replacements, and frames. On for play and
        debugging; off in a search loop, where the overhead is paid per node. Mutations
        are recorded either way, because rollback and hashing need them.
    strict:
        Hand rules a read-only view of each entity. Catches a rule writing to state
        directly, which under copy-on-write would reach committed state and survive a
        rollback. Turn it off once a rule set is trusted and throughput matters.
    """

    __slots__ = (
        "_compiled",
        "_rules",
        "max_depth",
        "max_events_per_transaction",
        "max_transactions",
        "narrate",
        "strict",
    )

    def __init__(
        self,
        rules: Iterable[Rule] = (),
        *,
        narrate: bool = False,
        strict: bool = True,
        max_transactions: int = MAX_TRANSACTIONS,
        max_depth: int = MAX_DEPTH,
        max_events_per_transaction: int = MAX_EVENTS_PER_TRANSACTION,
    ) -> None:
        self._rules: list[Rule] = list(rules)
        self._compiled: RuleSet | None = None
        self.narrate = narrate
        self.strict = strict
        self.max_transactions = max_transactions
        self.max_depth = max_depth
        self.max_events_per_transaction = max_events_per_transaction

    def add(self, *rules: Rule) -> Engine:
        """Register rules. Returns self, so calls chain."""
        self._rules.extend(rules)
        self._compiled = None
        return self

    @property
    def rules(self) -> Sequence[Rule]:
        return tuple(self._rules)

    def compile(self) -> RuleSet:
        """Order the rule set and index it by event class. Cached."""
        if self._compiled is None:
            self._compiled = RuleSet.compile(self._rules)
        return self._compiled

    def _resolver(self, log: RecordLog) -> Resolver:
        return Resolver(
            self.compile(),
            log,
            max_depth=self.max_depth,
            max_events_per_transaction=self.max_events_per_transaction,
            strict=self.strict,
        )

    def simulate(self, entities: EntitiesLike, event: Event) -> SimulationResult:
        """Run `event` and every consequence it produces, to quiescence.

        Consequences are processed breadth-first: ``A`` producing ``D, E, F``, with ``D``
        producing ``H, I``, runs as ``A D E F H I``.
        """
        log = RecordLog(narrate=self.narrate)
        resolver = self._resolver(log)

        # A fresh top-level mapping, so what comes back is never the caller's own dict --
        # even for a cascade in which nothing commits. Components are still shared with
        # the input, which is copy-on-write working as intended: a world handed out by
        # the engine is immutable by convention.
        current: Entities = own(entities)
        queue: deque[Event] = deque([event])
        transactions: list[Transaction] = []

        while queue:
            if len(transactions) >= self.max_transactions:
                fault = EngineFault(
                    f"Cascade exceeded the maximum of {self.max_transactions} "
                    f"transactions"
                )
                fault.partial = _result(current, transactions, log)
                raise fault
            root = queue.popleft()
            try:
                transaction = resolver.run(current, root, len(transactions))
            except EngineFault as fault:
                # A fault used to lose every transaction that had already committed. The
                # partial result is attached so the cascade is still inspectable.
                fault.partial = _result(current, transactions, log)
                raise
            transactions.append(transaction)
            current = transaction.entities
            queue.extend(transaction.consequences)

        return _result(current, transactions, log)

    def speculate(self, entities: EntitiesLike, event: Event) -> Transaction:
        """Resolve one transaction and report it, committing nothing.

        `entities` is untouched, so this is the state-transition function a search needs:
        expand a node, read the outcome, discard it. Consequences are *not* cascaded --
        a search wants one ply at a time.
        """
        return self._resolver(RecordLog(narrate=self.narrate)).run(
            own(entities), event, 0
        )


def _result(
    entities: Entities, transactions: Sequence[Transaction], log: RecordLog
) -> SimulationResult:
    return SimulationResult(
        entities=entities, transactions=tuple(transactions), records=log.records
    )
