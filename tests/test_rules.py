"""Rule declaration, ordering, dispatch, and the contract on what ``do`` may produce."""

from __future__ import annotations

import pytest

from baffle import (
    AfterRule,
    BeforeRule,
    Engine,
    EngineFault,
    Event,
    FailRule,
    Failure,
    IncrementComponent,
    ReplaceRule,
    Rule,
    RuleSet,
)
from baffle.naming import snake_case


class Ping(Event):
    name = "test.ping"


class Pong(Ping):
    """A subclass, to check that matching follows the hierarchy."""

    name = "test.pong"


def order_of(*rules: Rule) -> list[str]:
    return [rule.name for rule in RuleSet.compile(rules).phase("before")]


def tracer(order: list[str], rule_name: str, event=Ping):
    """A before rule that records when it ran."""

    class Traced(BeforeRule[event]):
        name = rule_name

        def do(self, world, event):
            order.append(rule_name)
            return ()

    return Traced


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------


def test_a_rule_name_defaults_to_its_class_name_in_snake_case():
    class PushTheCrate(BeforeRule[Ping]):

        def do(self, world, event):
            return ()

    assert PushTheCrate.name == "push_the_crate"


def test_rules_and_events_derive_a_name_the_same_way():
    """One convention, one function. They used to disagree -- kebab for rules, snake for
    events -- so `WithinBounds` was `within-bounds` beside `set_component`.
    """

    class Cadence(Event):
        pass

    class Cadence2(BeforeRule[Ping]):
        def do(self, world, event):
            return ()

    assert Cadence.name == "cadence"
    assert Cadence2.name == snake_case("Cadence2") == "cadence2"

    # Every capital is a boundary, which a run of them makes visible. Documented rather
    # than special-cased: a class that reads badly under the rule sets `name` itself.
    assert snake_case("HPCost") == "h_p_cost"


def test_duplicate_rule_names_are_refused_at_compile_time():
    first = tracer([], "clash")()
    second = tracer([], "clash")()

    with pytest.raises(EngineFault, match="share the name"):
        RuleSet.compile([first, second])


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------


def test_equal_rules_run_in_declaration_order():
    """They used to run alphabetically, because the ordering fields were never set."""
    zebra = tracer([], "zebra")()
    alpha = tracer([], "alpha")()

    assert order_of(zebra, alpha) == ["zebra", "alpha"]
    assert order_of(alpha, zebra) == ["alpha", "zebra"]


def test_run_before_overrides_declaration_order():
    class Second(BeforeRule[Ping]):
        name = "second"
        run_before = ("first",)

        def do(self, world, event):
            return ()

    first = tracer([], "first")()

    assert order_of(Second(), first) == ["second", "first"]


def test_run_after_overrides_declaration_order():
    class Late(BeforeRule[Ping]):
        name = "late"
        run_after = ("early",)

        def do(self, world, event):
            return ()

    early = tracer([], "early")()

    assert order_of(Late(), early) == ["early", "late"]


def test_ordering_is_transitive():
    def constrained(name, before=(), after=()):
        return type(
            "R",
            (BeforeRule,),
            {
                "name": name,
                "on": Ping,
                "run_before": before,
                "run_after": after,
                "do": lambda self, world, event: (),
            },
        )()

    rules = [
        constrained("c", after=("b",)),
        constrained("a"),
        constrained("b", after=("a",)),
    ]

    assert order_of(*rules) == ["a", "b", "c"]


def test_cyclic_constraints_fail_at_compile_time():
    def constrained(name, after):
        return type(
            "R",
            (BeforeRule,),
            {
                "name": name,
                "on": Ping,
                "run_after": after,
                "do": lambda self, world, event: (),
            },
        )()

    with pytest.raises(EngineFault, match="cyclic"):
        RuleSet.compile([constrained("x", ("y",)), constrained("y", ("x",))])


def test_constraints_naming_an_absent_rule_are_vacuous():
    """A mechanic declares where it sits without requiring its neighbours installed."""

    class Lonely(BeforeRule[Ping]):
        name = "lonely"
        run_before = ("not_installed",)

        def do(self, world, event):
            return ()

    assert order_of(Lonely()) == ["lonely"]


def test_ordering_only_applies_within_a_phase():
    """A before rule cannot be sequenced against an after rule; they never interleave."""

    class Early(BeforeRule[Ping]):
        name = "early"
        run_after = ("reaction",)

        def do(self, world, event):
            return ()

    class Reaction(AfterRule[Ping]):
        name = "reaction"

        def do(self, world, event, result):
            return ()

    compiled = RuleSet.compile([Early(), Reaction()])
    assert [rule.name for rule in compiled.phase("before")] == ["early"]
    assert [rule.name for rule in compiled.phase("after")] == ["reaction"]


def test_declared_order_decides_the_outcome_of_the_canonical_pair():
    """Push must precede solid, or the move is rejected instead of pushing.

    The failure mode without declared ordering is a plausible-looking wrong answer,
    not an error, which is why the constraint lives on the rule.
    """
    from baffle import MoveEntity
    from scenarios import Push, Solid, push_world

    result = Engine(rules=[Solid(), Push()]).simulate(
        push_world(), MoveEntity(entity="player", destination=(1, 0))
    )

    assert result.root.committed, "declared order must win over registration order"


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def test_a_rule_matches_subclasses_of_the_event_it_declares():
    order: list[str] = []
    engine = Engine(rules=[tracer(order, "on_ping", event=Ping)()])

    engine.simulate({}, Pong())

    assert order == ["on_ping"]


def test_a_rule_with_no_declared_event_matches_everything():
    order: list[str] = []

    class Audit(BeforeRule):
        name = "audit"

        def do(self, world, event):
            order.append(event.name)
            return ()

    engine = Engine(rules=[Audit()])
    engine.simulate({"counter": {"value": 0}}, Ping())
    engine.simulate(
        {"counter": {"value": 0}},
        IncrementComponent(entity="counter", component="value", value=1),
    )

    assert order == ["test.ping", "increment_component"]


def test_dispatch_is_cached_per_event_class():
    compiled = RuleSet.compile([tracer([], "cached", event=Ping)()])

    first = compiled.for_event("before", Ping())
    assert compiled.for_event("before", Ping()) is first


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def test_a_rule_runs_once_per_matching_event():
    order: list[str] = []
    Engine(rules=[tracer(order, "once", event=Ping)()]).simulate({}, Ping())

    assert order == ["once"]


def test_a_rule_that_yields_nothing_has_no_effect():
    """Fanning out zero times is how a rule stays out of the way."""

    class Quiet(BeforeRule[Ping]):
        name = "quiet"

        def do(self, world, event):
            return ()

    result = Engine(rules=[Quiet()]).simulate({"counter": {"value": 0}}, Ping())

    assert result.root.committed
    assert result.entities["counter"]["value"] == 0


def test_fanning_out_needs_no_binding_mechanism():
    """A for loop, and whatever it needs per iteration is a local."""

    class Triple(BeforeRule[Ping]):
        name = "triple"

        def do(self, world, event):
            for amount in (1, 2, 3):
                yield IncrementComponent(
                    entity="counter", component="value", value=amount
                )

    result = Engine(rules=[Triple()]).simulate({"counter": {"value": 0}}, Ping())

    assert result.entities["counter"]["value"] == 6


# ---------------------------------------------------------------------------
# What do() may produce
# ---------------------------------------------------------------------------


def test_a_before_rule_may_reject_instead_of_requiring():
    class Veto(BeforeRule[Ping]):
        name = "veto"

        def do(self, world, event):
            return Failure("vetoed")

    result = Engine(rules=[Veto()]).simulate({}, Ping())

    assert not result.root.committed
    assert result.root.failure is not None
    assert result.root.failure.reason == "vetoed"


def test_a_before_rule_may_reject_by_yielding():
    class Veto(BeforeRule[Ping]):
        name = "veto"

        def do(self, world, event):
            yield Failure("vetoed")

    assert not Engine(rules=[Veto()]).simulate({}, Ping()).root.committed


def test_requiring_and_rejecting_at_once_is_incoherent():
    """Emit prerequisites or reject. Both would have no defensible meaning."""

    class Muddled(BeforeRule[Ping]):
        name = "muddled"

        def do(self, world, event):  # type: ignore[override]
            # Deliberately incoherent; the checker and the engine both object.
            yield IncrementComponent(entity="counter", component="value", value=1)
            yield Failure("changed my mind")

    with pytest.raises(EngineFault, match="not both"):
        Engine(rules=[Muddled()]).simulate({"counter": {"value": 0}}, Ping())


def test_producing_something_that_is_neither_is_refused():
    class Junk(BeforeRule[Ping]):
        name = "junk"

        def do(self, world, event):  # type: ignore[override]
            yield 42  # deliberately not an event

    with pytest.raises(EngineFault, match="neither an event nor a failure"):
        Engine(rules=[Junk()]).simulate({}, Ping())


def test_an_after_rule_cannot_reject_a_committed_transaction():
    class TooLate(AfterRule[Ping]):
        name = "too_late"

        def do(self, world, event, result):  # type: ignore[override]
            # An after rule refusing is meaningless; the engine rejects it at runtime.
            yield Failure("second thoughts")

    with pytest.raises(EngineFault, match="already committed"):
        Engine(rules=[TooLate()]).simulate({}, Ping())


def test_a_fail_rule_cannot_reject_a_discarded_transaction():
    class Veto(BeforeRule[Ping]):
        name = "veto"

        def do(self, world, event):
            return Failure("vetoed")

    class TooLate(FailRule[Ping]):
        name = "too_late"

        def do(self, world, event, failure):  # type: ignore[override]
            yield Failure("again")  # as above, for fail rules

    with pytest.raises(EngineFault, match="already been discarded"):
        Engine(rules=[Veto(), TooLate()]).simulate({}, Ping())


def test_a_replace_rule_must_return_exactly_one_event():
    class Sloppy(ReplaceRule[Ping]):
        name = "sloppy"

        def do(self, world, event):  # type: ignore[override]
            return [event]  # must be one event, not a list

    with pytest.raises(EngineFault, match="must return one event"):
        Engine(rules=[Sloppy()]).simulate({}, Ping())


def test_a_replace_rule_declines_by_returning_the_event():
    """There is no way to express an ambiguous replacement: `do` returns one event."""

    class Picky(ReplaceRule[Ping]):
        name = "picky"

        def do(self, world, event):
            return event

    result = Engine(rules=[Picky()]).simulate({}, Ping())

    assert result.root.committed
    assert isinstance(result.root.event, Ping)
