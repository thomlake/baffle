"""Event resolution and the transaction boundary.

The lifecycle, in one place:

1. Replace rules rewrite the event. This happens at every nesting level, not only at
   the root.
2. Before rules run in declared order. Each may require events, which resolve
   recursively *in this transaction*, or refuse. Because they resolve immediately, their
   effects are visible to the before rules that run after them.
3. The event's operation executes against the working world.
4. A frame is recorded. Children before parents, so frames come out in postorder.

The root event's outcome decides one commit or one discard. Nothing nested commits on
its own, no matter how deep or how successful.

:class:`Resolution` and :class:`Transaction` are deliberately the same shape -- frames,
plus a failure that is None when things worked. One way of saying "it did or it did not",
at both levels.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from .errors import EngineFault
from .events import Effect, Event, Failure, OperationResult
from .records import (
    Attempt,
    Frame,
    RecordLog,
    Replaced,
    RuleFired,
    TransactionBegin,
    TransactionEnd,
)
from .rules import AFTER, BEFORE, FAIL, REPLACE, RuleSet, drain
from .state import World
from .types import Entities, EntityId

#: Per reaction phase: what a frame hands the rule, or None for a frame this phase has no
#: business with, and why a refusal at this point is incoherent. The two phases differ in
#: exactly this much, so :meth:`Resolver.react` serves both.
_REACTIONS: dict[str, tuple[Callable[[Frame], Any], str]] = {
    AFTER: (
        lambda frame: frame.effect.details if frame.effect is not None else None,
        "An after rule cannot refuse; the transaction has already committed",
    ),
    FAIL: (
        lambda frame: frame.failure,
        "A fail rule cannot refuse; the transaction has already been discarded",
    ),
}


@dataclass(frozen=True)
class Resolution:
    """What resolving one event produced, including everything beneath it."""

    frames: tuple[Frame, ...]
    failure: Failure | None = None

    @property
    def succeeded(self) -> bool:
        return self.failure is None


@dataclass(frozen=True)
class Transaction:
    """The outcome of one root event: exactly one commit, or exactly one discard."""

    index: int
    event: Event
    entities: Entities
    frames: tuple[Frame, ...] = ()
    failure: Failure | None = None
    consequences: tuple[Event, ...] = ()
    touched: frozenset[EntityId] = field(default_factory=frozenset)

    @property
    def committed(self) -> bool:
        return self.failure is None


class Resolver:
    """Resolves events and owns the transaction boundary.

    Holds the limits so that recursion carries only what changes: the event, its depth,
    and the chain that produced it.
    """

    __slots__ = (
        "_spent",
        "log",
        "max_depth",
        "max_events_per_transaction",
        "rules",
    )

    def __init__(
        self,
        rules: RuleSet,
        log: RecordLog,
        *,
        max_depth: int,
        max_events_per_transaction: int,
    ) -> None:
        self.rules = rules
        self.log = log
        self.max_depth = max_depth
        self.max_events_per_transaction = max_events_per_transaction
        self._spent = 0

    # -- transaction ------------------------------------------------------

    def run(self, base: Entities, event: Event, index: int) -> Transaction:
        """Resolve one root event against `base` and decide its fate."""
        self._spent = 0
        self.log.note(TransactionBegin(index=index, event=event))
        # The span covers only what the doomed attempt produced, so marking it does not
        # tar the transaction's own bookends or the fail rules that legitimately ran.
        span = self.log.mark()

        working = World(base, log=self.log, sealed=True)
        resolution = self.resolve(working, event, depth=0, chain=(), emitted_by=None)

        if not resolution.succeeded:
            self.log.mark_rolled_back(span)
            # The frames themselves, not only the log's copy of them: a frame reaches the
            # log only when narrating, while `Transaction.frames` is populated either way,
            # so the span alone would make the flag depend on a debug setting.
            for frame in resolution.frames:
                frame.rolled_back = True
            self.log.note(
                TransactionEnd(
                    index=index,
                    event=event,
                    committed=False,
                    failure=resolution.failure,
                )
            )
            # Fail rules observe the world as it was before any of this happened.
            unchanged = World(base, log=self.log, sealed=True)
            return Transaction(
                index=index,
                event=event,
                entities=base,
                frames=resolution.frames,
                failure=resolution.failure,
                consequences=self.react(FAIL, unchanged, resolution.frames),
            )

        committed = working.snapshot()
        touched = working.touched
        self.log.note(
            TransactionEnd(index=index, event=event, committed=True, failure=None)
        )
        after = World(committed, log=self.log, sealed=True)
        return Transaction(
            index=index,
            event=event,
            entities=committed,
            frames=resolution.frames,
            consequences=self.react(AFTER, after, resolution.frames),
            touched=touched,
        )

    # -- resolution -------------------------------------------------------

    def resolve(
        self,
        world: World,
        submitted: Event,
        *,
        depth: int,
        chain: tuple[Event, ...],
        emitted_by: str | None,
    ) -> Resolution:
        self._spend(submitted, depth, chain, emitted_by)

        event = self.replace(world, submitted)
        self.log.note(Attempt(event=event, depth=depth, emitted_by=emitted_by))
        active = (*chain, event)

        frames: list[Frame] = []

        for rule in self.rules.for_event(BEFORE, event):
            required, failure = drain(rule, rule.do(world, event))
            if required or failure is not None:
                self.log.note(
                    RuleFired(rule=rule.name, event=event, produced=required)
                )
            if failure is not None:
                return self._refuse(event, failure, depth, frames)

            for prerequisite in required:
                child = self.resolve(
                    world,
                    prerequisite,
                    depth=depth + 1,
                    chain=active,
                    emitted_by=rule.name,
                )
                frames.extend(child.frames)
                if not child.succeeded:
                    return self._refuse(
                        event,
                        Failure(
                            "required_event_failed",
                            {"required_event": prerequisite, "rule": rule.name},
                            cause=child.failure,
                        ),
                        depth,
                        frames,
                    )

        refusal = event.precheck(world)
        if refusal is not None:
            return self._refuse(event, refusal, depth, frames)

        outcome = self._execute(event, world)
        if isinstance(outcome, Failure):
            return self._refuse(event, outcome, depth, frames)
        if not isinstance(outcome, Effect):
            raise EngineFault(
                f"apply() returned {outcome!r}, expected an Effect or a Failure",
                event=event,
            )

        frames.append(self._frame(Frame(event=event, depth=depth, effect=outcome)))
        return Resolution(frames=tuple(frames))

    def _execute(self, event: Event, world: World) -> OperationResult:
        """Run the event's operation. The only point at which the world accepts a write.

        Everything else the engine hands a rule -- this world before and after, the
        pre-transaction world fail rules see, the committed world after rules see -- stays
        sealed, so an event is the only way state changes.
        """
        world.unseal()
        try:
            return event.apply(world)
        finally:
            world.seal()

    def _refuse(
        self,
        event: Event,
        failure: Failure,
        depth: int,
        frames: Sequence[Frame],
    ) -> Resolution:
        refused = self._frame(Frame(event=event, depth=depth, failure=failure))
        return Resolution(frames=(*frames, refused), failure=failure)

    def _frame(self, frame: Frame) -> Frame:
        """Note a frame to the log and hand it back for the resolution.

        One object in two places. The resolution needs every frame -- it is what decides
        which reaction rules run -- while the log holds the same frames only when
        narrating. That asymmetry is why :meth:`run` marks a discarded transaction's
        frames directly instead of relying on the log's span to reach them.
        """
        self.log.note(frame)
        return frame

    def _spend(
        self,
        event: Event,
        depth: int,
        chain: tuple[Event, ...],
        emitted_by: str | None,
    ) -> None:
        """Charge one event against both budgets.

        Depth alone is not enough: it permits exponential fan-out, which was how one
        transaction resolved 8191 events at depth 12.
        """
        if depth >= self.max_depth:
            raise EngineFault(
                f"Prerequisite chain exceeded the maximum depth of {self.max_depth}",
                rule=emitted_by,
                event=event,
                chain=chain,
            )
        self._spent += 1
        if self._spent > self.max_events_per_transaction:
            raise EngineFault(
                f"Transaction exceeded its work budget of "
                f"{self.max_events_per_transaction} events",
                rule=emitted_by,
                event=event,
                chain=chain,
            )

    def replace(self, world: World, event: Event) -> Event:
        """Apply each matching replace rule at most once, in declared order.

        An intercept, not a fixed point. A rule that already ran does not get to
        re-examine a later rewrite.
        """
        current = event
        for rule in self.rules.phase(REPLACE):
            if not rule.matches(current):
                continue
            produced = rule.do(world, current)
            if not isinstance(produced, Event):
                raise EngineFault(
                    f"A replace rule must return one event, got {produced!r}",
                    rule=rule.name,
                    event=current,
                )
            if produced is not current:
                self.log.note(
                    Replaced(before=current, after=produced, by_rule=rule.name)
                )
                current = produced
        return current

    # -- consequences -----------------------------------------------------

    def react(
        self, phase: str, world: World, frames: Sequence[Frame]
    ) -> tuple[Event, ...]:
        """Run one reaction phase over the frames it concerns.

        ``after`` receives what each operation computed, which is the information an event
        deliberately does not carry; ``fail`` receives the refusal. A frame the phase has
        no business with is skipped -- for ``fail`` that means one which succeeded and was
        then rolled back with the transaction, leaving nothing for a reaction to observe.
        It stays in the record stream, marked, for anyone rendering what happened.

        Neither phase can refuse: the transaction has already been decided.
        """
        context, refusal = _REACTIONS[phase]
        produced: list[Event] = []
        for frame in frames:
            argument = context(frame)
            if argument is None:
                continue
            for rule in self.rules.for_event(phase, frame.event):
                events, failure = drain(rule, rule.do(world, frame.event, argument))
                if failure is not None:
                    raise EngineFault(refusal, rule=rule.name, event=frame.event)
                if events:
                    self.log.note(
                        RuleFired(rule=rule.name, event=frame.event, produced=events)
                    )
                produced.extend(events)
        return tuple(produced)
