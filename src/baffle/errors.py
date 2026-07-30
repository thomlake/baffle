from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from .events import Event


class EngineFault(RuntimeError):
    """A broken invariant: a malformed rule, a bad event, or an engine bug.

    Distinct from :class:`~baffle.events.Failure`, which is an expected gameplay
    rejection. A fault means someone's code is wrong.

    Context is kept as fields rather than baked into the message so that tooling --
    a debugger, a rule loader, an error overlay -- can report the offending rule
    without parsing prose.
    """

    def __init__(
        self,
        message: str,
        *,
        rule: str | None = None,
        event: Event | None = None,
        entity: str | None = None,
        component: str | None = None,
        chain: tuple[Event, ...] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        #: The simulation up to the point of the fault, when one was in progress.
        #: A fault mid-cascade used to discard every transaction that had committed.
        self.partial: Any = None
        self.rule = rule
        self.event = event
        self.entity = entity
        self.component = component
        self.chain = chain

    def __str__(self) -> str:
        parts = [self.message]
        if self.rule is not None:
            parts.append(f"rule={self.rule!r}")
        if self.event is not None:
            parts.append(f"event={self.event.name!r}")
        if self.entity is not None:
            parts.append(f"entity={self.entity!r}")
        if self.component is not None:
            parts.append(f"component={self.component!r}")
        if self.chain:
            trail = " -> ".join(event.name for event in self.chain)
            parts.append(f"chain={trail}")
        return "; ".join(parts)
