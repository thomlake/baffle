"""The observable record of a simulation.

One append-only stream per :func:`~baffle.simulate.simulate` call, serving three
consumers with different needs:

* **Rendering** wants a linear transcript, including attempts that were later rolled
  back -- "the crate moved and then it did not" is exactly what a player reads a log
  to find out.
* **Hashing and diffing** want mutations with their previous values, from committed
  transactions only.
* **Debugging** wants everything, with rule attribution.

Two rules make that work. Attempts are recorded on the way *in* and frames on the
way *out*, so the stream is a tree traversal rather than a flat postorder list. And a
discarded transaction is **marked**, never truncated, because the renderer needs what
the hasher must ignore.

Mutations are recorded unconditionally -- rollback and incremental hashing depend on
them. Everything else is narration, gated by a single flag, since in a search loop
that overhead is multiplied by millions of nodes.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from .events import Effect, Event, Failure
from .types import ComponentPath, EntityId


class Record:
    """Base for every log entry.

    ``rolled_back`` is a plain attribute rather than a field so that marking a span
    costs one assignment and does not participate in equality.
    """

    rolled_back: bool = False


@dataclass
class TransactionBegin(Record):
    index: int
    event: Event


@dataclass
class TransactionEnd(Record):
    index: int
    event: Event
    committed: bool
    failure: Failure | None = None


@dataclass
class Attempt(Record):
    """Recorded before resolution, so a transcript can say "attempted to"."""

    event: Event
    depth: int
    emitted_by: str | None = None


@dataclass
class RuleFired(Record):
    """Rule attribution. "The player pushed the crate" names a rule, not an event.

    ``produced`` is what the rule emitted, which is what a transcript needs to name the
    thing acted upon -- the crate, in that sentence.
    """

    rule: str
    event: Event
    produced: tuple[Event, ...] = ()


@dataclass
class Replaced(Record):
    before: Event
    after: Event
    by_rule: str


@dataclass
class Mutation(Record):
    """A single state change.

    ``old`` is what makes incremental (Zobrist-style) hashing and cheap diffing
    possible: XOR the previous value out, the new value in. ``kind`` distinguishes
    replacement from container insertion and removal, which do not have both sides.
    """

    entity: EntityId
    path: ComponentPath
    old: Any
    new: Any
    kind: str  # "replace" | "insert" | "remove"


@dataclass
class Frame(Record):
    """One event that was resolved, and how it turned out.

    Two roles, one object. The resolver accumulates frames unconditionally, because
    deciding which ``after`` or ``fail`` rules to run is exactly a question about them;
    and it notes the same object to the log, where narration picks it up. Recorded on the
    way out, so frames come out in postorder.

    Carries its outcome rather than being split into success and failure variants, so a
    prerequisite that succeeded before a later refusal is still in the list. Dropping
    those was how "the crate moved and then it did not" became unreportable.

    Mutable, and not frozen, so :meth:`RecordLog.mark_rolled_back` can flag it. Nothing
    is lost by that: :class:`~baffle.events.Effect` holds a dict, so a frame was never
    hashable.
    """

    event: Event
    depth: int
    effect: Effect | None = None
    failure: Failure | None = None

    @property
    def succeeded(self) -> bool:
        return self.failure is None


class RecordLog:
    """Append-only, with a mark/rollback span protocol."""

    __slots__ = ("_records", "narrate")

    def __init__(self, *, narrate: bool = False) -> None:
        self._records: list[Record] = []
        self.narrate = narrate

    def mutation(self, record: Mutation) -> None:
        """Unconditional: rollback and hashing both depend on these."""
        self._records.append(record)

    def note(self, record: Record) -> None:
        """Narration, dropped entirely when disabled."""
        if self.narrate:
            self._records.append(record)

    def mark(self) -> int:
        """The current end of the stream, for later use as a span start."""
        return len(self._records)

    def mark_rolled_back(self, start: int) -> None:
        """Flag every record from `start` onwards as discarded, without removing it."""
        for record in self._records[start:]:
            record.rolled_back = True

    def committed_mutations(self) -> Iterator[Mutation]:
        """Mutations that survived, in order. The hasher's view."""
        for record in self._records:
            if isinstance(record, Mutation) and not record.rolled_back:
                yield record

    def __iter__(self) -> Iterator[Record]:
        return iter(self._records)

    def __len__(self) -> int:
        return len(self._records)

    def __getitem__(self, index: int) -> Record:
        return self._records[index]

    @property
    def records(self) -> tuple[Record, ...]:
        return tuple(self._records)
