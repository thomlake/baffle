"""Rules: the four phases, and how a rule set is ordered.

A rule is a plain class with one method. ``do`` reads the world and yields events --
none to stay out of the way, one for the common case, many to fan out. The engine drains
it completely before resolving anything it produced, which is what makes every decision
a rule takes reflect a single view of the world.

There is deliberately no separate selection step and no binding mechanism. Fanning out
is an ordinary ``for`` loop, so whatever a rule needs per iteration is a local variable
rather than a dict threaded through the engine. The signature is then the same for every
rule of a phase, which is what lets a type checker infer ``world`` and ``event``
without an author annotating anything.

Ordering is **declared**, not numeric. ``run_before`` and ``run_after`` name other rules
and are topologically sorted at compile time. Numeric priorities encode correctness
dependencies as magic numbers: swapping two of them yields a plausible-looking wrong
answer rather than an error, and every rule added later silently risks reordering an
existing pair.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, ClassVar, get_args, get_origin

from .errors import EngineFault
from .events import Event, Failure
from .state import World

REPLACE = "replace"
BEFORE = "before"
AFTER = "after"
FAIL = "fail"


class Rule:
    """Base for every rule. Subclass one of the four phase classes instead."""

    #: Unique within a rule set. Defaults to the class name in kebab-case.
    name: ClassVar[str]
    #: Which events this rule matches. Derived from the phase class's type argument, so
    #: ``BeforeRule[MoveEntity]`` both declares it and types ``event`` in :meth:`do`.
    #: Subclasses of a listed class match too, so a rule can react to a whole family.
    #: None matches everything.
    on: ClassVar[type[Event] | tuple[type[Event], ...] | None] = None
    #: Names of same-phase rules this one must precede / follow.
    run_before: ClassVar[tuple[str, ...]] = ()
    run_after: ClassVar[tuple[str, ...]] = ()

    phase: ClassVar[str]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if "phase" in cls.__dict__:
            return  # one of the four phase base classes
        if "name" not in cls.__dict__:
            cls.name = _kebab(cls.__name__)
        if "on" not in cls.__dict__:
            derived = _matched_events(cls)
            if derived is not None:
                cls.on = derived

    def do(self, *args: Any, **kwargs: Any) -> Any:
        """Declared gradually here; each phase class narrows it to its own signature.

        The engine holds rules as :class:`Rule`, so it needs *some* declaration to call.
        Keeping this one untyped is what lets the four phases disagree about their third
        argument while a concrete rule still matches its phase exactly -- which is what
        makes a checker infer ``world`` and ``event`` for free.
        """
        raise NotImplementedError

    def matches(self, event: Event) -> bool:
        if self.on is None:
            return True
        return isinstance(event, self.on)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{self.phase} rule {self.name!r}>"


class ReplaceRule[E: Event](Rule):
    """Rewrite an event before anything else sees it.

    An intercept, not a rewrite chain: each replace rule applies at most once, in
    declared order, and the result is not re-examined by rules that already ran. That is
    deliberate -- a confusion effect that flips a direction must not ping-pong.

    Return `event` unchanged to decline.
    """

    phase = REPLACE

    def do(self, world: World, event: E) -> Event:
        raise NotImplementedError


class BeforeRule[E: Event](Rule):
    """Require prerequisites, or refuse.

    Yield events to require them; they resolve recursively inside this transaction, so
    they are visible to rules that run after this one and are discarded along with
    everything else if the transaction fails.

    Return or yield a single :class:`~baffle.events.Failure` to refuse instead. Yielding
    is supported because a rule that contains any ``yield`` is a generator, where
    ``return Failure(...)`` would be silently swallowed -- so a rule that loops and then
    decides to refuse has no other option.

    Producing events *and* a refusal is incoherent, and rejected at runtime. The type
    cannot express "homogeneous", so it does not pretend to.
    """

    phase = BEFORE

    def do(
        self, world: World, event: E
    ) -> Iterable[Event | Failure] | Failure | None:
        raise NotImplementedError


class AfterRule[E: Event](Rule):
    """React to an event that committed.

    ``result`` is what the operation computed -- where an entity moved from, what a
    counter changed to -- which is the information an event deliberately does not carry.
    """

    phase = AFTER

    def do(
        self, world: World, event: E, result: dict[str, Any]
    ) -> Iterable[Event] | None:
        raise NotImplementedError


class FailRule[E: Event](Rule):
    """React to an event that was refused.

    Sees the world as it was before the transaction began. Work that succeeded inside
    the doomed transaction was rolled back, and a reaction must not observe it.
    """

    phase = FAIL

    def do(
        self, world: World, event: E, failure: Failure
    ) -> Iterable[Event] | None:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def drain(rule: Rule, produced: Any) -> tuple[tuple[Event, ...], Failure | None]:
    """Normalise what ``do`` returned into events or a single refusal.

    Draining here, before anything resolves, is what gives a rule one stable view of the
    world: a lazily consumed generator would see the world change underneath it
    mid-iteration.

    Output must be homogeneous. Requiring a prerequisite *and* refusing is incoherent,
    so it is rejected rather than silently resolved one way.
    """
    if isinstance(produced, Failure):
        return (), produced
    if isinstance(produced, Event):
        return (produced,), None
    if produced is None:
        return (), None

    events: list[Event] = []
    failure: Failure | None = None
    for item in produced:
        if isinstance(item, Failure):
            if failure is not None or events:
                raise EngineFault(
                    "A rule may produce events or one refusal, not both",
                    rule=rule.name,
                )
            failure = item
        elif isinstance(item, Event):
            if failure is not None:
                raise EngineFault(
                    "A rule may produce events or one refusal, not both",
                    rule=rule.name,
                )
            events.append(item)
        else:
            raise EngineFault(
                f"A rule produced {item!r}, which is neither an event nor a failure",
                rule=rule.name,
            )
    return tuple(events), failure


# ---------------------------------------------------------------------------
# Compilation
# ---------------------------------------------------------------------------


class RuleSet:
    """Rules grouped by phase, ordered, and indexed by event class."""

    __slots__ = ("_by_phase", "_dispatch")

    def __init__(self, by_phase: Mapping[str, Sequence[Rule]]) -> None:
        self._by_phase = {phase: tuple(rules) for phase, rules in by_phase.items()}
        self._dispatch: dict[tuple[str, type[Event]], tuple[Rule, ...]] = {}

    @classmethod
    def compile(cls, rules: Iterable[Rule]) -> RuleSet:
        collected = list(rules)
        _reject_duplicate_names(collected)
        by_phase: dict[str, list[Rule]] = {
            REPLACE: [],
            BEFORE: [],
            AFTER: [],
            FAIL: [],
        }
        for rule in collected:
            phase = getattr(rule, "phase", None)
            if phase not in by_phase:
                raise EngineFault(
                    f"Rule has no recognised phase: {rule!r}",
                    rule=getattr(rule, "name", None),
                )
            by_phase[phase].append(rule)
        return cls({phase: _order(group) for phase, group in by_phase.items()})

    def for_event(self, phase: str, event: Event) -> tuple[Rule, ...]:
        """Rules of `phase` matching `event`, in order.

        Cached per event class, so subclass matching costs one dict lookup rather than
        a walk over every rule.
        """
        key = (phase, type(event))
        cached = self._dispatch.get(key)
        if cached is None:
            cached = tuple(
                rule for rule in self._by_phase[phase] if rule.matches(event)
            )
            self._dispatch[key] = cached
        return cached

    def phase(self, phase: str) -> tuple[Rule, ...]:
        return self._by_phase[phase]


def _reject_duplicate_names(rules: Sequence[Rule]) -> None:
    seen: dict[str, Rule] = {}
    for rule in rules:
        name = getattr(rule, "name", None)
        if name is None:
            raise EngineFault(f"Rule has no name: {rule!r}")
        if name in seen:
            raise EngineFault(f"Two rules share the name {name!r}")
        seen[name] = rule


def _order(rules: Sequence[Rule]) -> tuple[Rule, ...]:
    """Topologically sort one phase, breaking ties by declaration order.

    Constraints naming a rule outside this phase are vacuous rather than an error, so a
    mechanic can declare where it sits without requiring its neighbours to be installed.
    """
    index = {rule.name: position for position, rule in enumerate(rules)}
    successors: dict[str, set[str]] = {rule.name: set() for rule in rules}
    incoming: dict[str, int] = {rule.name: 0 for rule in rules}

    def edge(earlier: str, later: str) -> None:
        if later in successors[earlier]:
            return
        successors[earlier].add(later)
        incoming[later] += 1

    for rule in rules:
        for other in rule.run_before:
            if other in index:
                edge(rule.name, other)
        for other in rule.run_after:
            if other in index:
                edge(other, rule.name)

    ready = sorted(
        (name for name, count in incoming.items() if count == 0), key=index.__getitem__
    )
    ordered: list[Rule] = []
    by_name = {rule.name: rule for rule in rules}
    while ready:
        name = ready.pop(0)
        ordered.append(by_name[name])
        for follower in sorted(successors[name], key=index.__getitem__):
            incoming[follower] -= 1
            if incoming[follower] == 0:
                ready.append(follower)
        ready.sort(key=index.__getitem__)

    if len(ordered) != len(rules):
        unresolved = sorted(set(by_name) - {rule.name for rule in ordered})
        raise EngineFault(f"Rule ordering constraints are cyclic among {unresolved}")
    return tuple(ordered)


def _matched_events(cls: type) -> type[Event] | tuple[type[Event], ...] | None:
    """Read the event type out of ``BeforeRule[MoveEntity]`` and friends.

    Declaring it once, as the type argument, is what keeps ``on`` from drifting out of
    step with the type a rule's ``do`` is written against. A bare ``BeforeRule`` derives
    nothing and matches every event.
    """
    for base in getattr(cls, "__orig_bases__", ()):
        if get_origin(base) is None:
            continue
        arguments = get_args(base)
        if not arguments:
            continue
        # A union arrives as one argument with its own args: BeforeRule[Move | Step].
        members = get_args(arguments[0]) or (arguments[0],)
        if all(isinstance(member, type) and issubclass(member, Event) for member in members):
            return members[0] if len(members) == 1 else members
    return None


def _kebab(name: str) -> str:
    out: list[str] = []
    for position, char in enumerate(name):
        if char.isupper() and position:
            out.append("-")
        out.append(char.lower())
    return "".join(out)
