from collections.abc import Iterable
from dataclasses import dataclass

import pytest

from baffle.events import Event, Rejection, Set
from baffle.rules import (
    ReactRule,
    RejectRule,
    RequireRule,
    Rule,
    Ruleset,
    react,
    reject,
    require,
    sort_rules,
)
from baffle.world import World


@dataclass(frozen=True)
class Move(Event):
    entity: str
    destination: tuple[int, int]


def require_position(world: World, event: Move) -> Iterable[Event]:
    yield Set(event.entity, "position", event.destination)


def reject_blocked_move(world: World, event: Move) -> Rejection | None:
    if world.get(event.entity, "blocked", default=False):
        return Rejection("blocked")

    return None


def react_to_move(world: World, event: Move) -> Iterable[Event]:
    return ()


def test_require_rule_stores_event_type_and_emitter() -> None:
    rule = RequireRule("require_position", Move, require_position)

    emitted = tuple(
        rule.run(
            World({"player": {}}),
            Move("player", (1, 2)),
        )
    )

    assert rule.event_type is Move
    assert emitted == (
        Set("player", "position", (1, 2)),
    )


def test_reject_rule_returns_rejection() -> None:
    rule = RejectRule("reject_blocked_move", Move, reject_blocked_move)
    move = Move("player", (1, 2))

    rejection = rule.run(
        World({"player": {"blocked": True}}),
        move,
    )

    assert rule.event_type is Move
    assert rejection == Rejection("blocked")


def test_reject_rule_may_allow_event() -> None:
    rule = RejectRule("reject_blocked_move", Move, reject_blocked_move)
    move = Move("player", (1, 2))

    rejection = rule.run(
        World({"player": {"blocked": False}}),
        move,
    )

    assert rejection is None


def test_react_rule_stores_event_type_and_emitter() -> None:
    rule = ReactRule("react_to_move", Move, react_to_move)

    emitted = tuple(
        rule.run(
            World({}),
            Move("player", (1, 2)),
        )
    )

    assert rule.event_type is Move
    assert emitted == ()


def test_direct_rule_stores_required_name() -> None:
    rule = RequireRule("require_position", Move, require_position)

    assert rule.name == "require_position"


def test_direct_rule_accepts_name_distinct_from_callback() -> None:
    rule = RequireRule("movement.position", Move, require_position)

    assert rule.name == "movement.position"


def test_direct_rule_rejects_empty_name() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        RequireRule("", Move, require_position)


def test_rule_rejects_non_event_type() -> None:
    with pytest.raises(TypeError, match="must be Event subclasses"):
        RequireRule("require_position", str, require_position)  # type: ignore[arg-type]


def test_bare_decorators_infer_event_type_and_rule_kind() -> None:
    @require
    def required(world: World, event: Move) -> Iterable[Event]:
        return ()

    @reject
    def rejected(world: World, event: Move) -> Rejection | None:
        return None

    @react
    def reacted(world: World, event: Move) -> Iterable[Event]:
        return ()

    assert isinstance(required, RequireRule)
    assert isinstance(rejected, RejectRule)
    assert isinstance(reacted, ReactRule)
    assert required.event_type is Move
    assert rejected.event_type is Move
    assert reacted.event_type is Move
    assert required.name == "required"
    assert rejected.name == "rejected"
    assert reacted.name == "reacted"


def test_configured_decorator_sets_ordering_metadata() -> None:
    @require(
        name="position",
        after=("movement",),
        before=("collision",),
    )
    def position(world: World, event: Move) -> Iterable[Event]:
        return ()

    assert position.name == "position"
    assert position.after == ("movement",)
    assert position.before == ("collision",)


def test_decorated_rule_remains_callable() -> None:
    @reject
    def blocked(world: World, event: Move) -> Rejection | None:
        return Rejection("blocked")

    assert blocked(World({}), Move("player", (1, 2))) == Rejection("blocked")


def test_decorator_requires_event_annotation() -> None:
    with pytest.raises(TypeError, match="must be annotated"):

        @require
        def missing(world: World, event) -> Iterable[Event]:
            return ()


def test_decorator_requires_two_positional_parameters() -> None:
    with pytest.raises(TypeError, match="exactly two positional arguments"):

        @require
        def too_many(
            world: World,
            event: Move,
            extra: object,
        ) -> Iterable[Event]:
            return ()


def test_sort_rules_returns_empty_tuple() -> None:
    assert sort_rules([]) == ()


def test_sort_rules_preserves_unconstrained_input_order() -> None:
    first = RequireRule("first", Move, require_position)
    second = RequireRule("second", Move, require_position)
    third = RequireRule("third", Move, require_position)

    assert sort_rules([second, first, third]) == (second, first, third)


def test_sort_rules_applies_before_and_after_constraints() -> None:
    first = RequireRule(
        "first",
        Move,
        require_position,
        before=("second",),
    )
    second = RequireRule("second", Move, require_position)
    third = RequireRule(
        "third",
        Move,
        require_position,
        after=("second",),
    )

    assert sort_rules([third, second, first]) == (first, second, third)


def test_sort_rules_counts_duplicate_relation_once() -> None:
    first = RequireRule(
        "first",
        Move,
        require_position,
        before=("second",),
    )
    second = RequireRule(
        "second",
        Move,
        require_position,
        after=("first",),
    )

    assert sort_rules([second, first]) == (first, second)


def test_sort_rules_handles_branching_dependencies() -> None:
    publish = RequireRule(
        "publish",
        Move,
        require_position,
        after=("transform", "validate"),
    )
    audit = RequireRule("audit", Move, require_position)
    transform = RequireRule(
        "transform",
        Move,
        require_position,
        after=("load",),
    )
    validate = RequireRule(
        "validate",
        Move,
        require_position,
        after=("load",),
    )
    load = RequireRule("load", Move, require_position)

    assert sort_rules([publish, audit, transform, validate, load]) == (
        audit,
        load,
        transform,
        validate,
        publish,
    )


def test_sort_rules_rejects_duplicate_names() -> None:
    first = RequireRule("same", Move, require_position)
    second = RequireRule("same", Move, require_position)

    with pytest.raises(ValueError, match="Duplicate rule name: 'same'"):
        sort_rules([first, second])


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (("missing",), ()),
        ((), ("missing",)),
    ],
)
def test_sort_rules_rejects_unknown_reference(
    before: tuple[str, ...],
    after: tuple[str, ...],
) -> None:
    rule = RequireRule(
        "known",
        Move,
        require_position,
        before=before,
        after=after,
    )

    with pytest.raises(
        ValueError,
        match="Rule 'known' references unknown rule 'missing'",
    ):
        sort_rules([rule])


def test_sort_rules_rejects_cycle() -> None:
    first = RequireRule(
        "first",
        Move,
        require_position,
        after=("third",),
    )
    second = RequireRule(
        "second",
        Move,
        require_position,
        after=("first",),
    )
    third = RequireRule(
        "third",
        Move,
        require_position,
        after=("second",),
    )

    with pytest.raises(ValueError, match="Cyclic rule ordering"):
        sort_rules([first, second, third])


def test_ruleset_sorts_before_rules_and_react_rules_independently() -> None:
    first = RequireRule("first", Move, require_position)
    second = RejectRule(
        "second",
        Move,
        reject_blocked_move,
        after=("first",),
    )
    third = ReactRule("third", Move, react_to_move)
    fourth = ReactRule(
        "fourth",
        Move,
        react_to_move,
        before=("third",),
    )

    ruleset = Ruleset([second, third, first, fourth])

    assert ruleset.rules == (second, third, first, fourth)
    assert ruleset.before_rules == (first, second)
    assert ruleset.react_rules == (fourth, third)


def test_ruleset_preserves_unconstrained_registration_order() -> None:
    first = RequireRule("first", Move, require_position)
    second = RejectRule("second", Move, reject_blocked_move)

    ruleset = Ruleset([first, second])

    assert ruleset.before_rules == (first, second)


def test_ruleset_rejects_duplicate_names() -> None:
    with pytest.raises(ValueError, match="Duplicate rule name: 'same'"):
        Ruleset(
            [
                RequireRule("same", Move, require_position),
                ReactRule("same", Move, react_to_move),
            ]
        )


def test_ruleset_rejects_unknown_ordering_reference() -> None:
    with pytest.raises(ValueError, match="references unknown rule 'missing'"):
        Ruleset(
            [
                RequireRule(
                    "require_position",
                    Move,
                    require_position,
                    after=("missing",),
                )
            ]
        )


def test_ruleset_rejects_cross_phase_ordering() -> None:
    before_rule = RequireRule("before", Move, require_position)
    react_rule = ReactRule(
        "reaction",
        Move,
        react_to_move,
        after=("before",),
    )

    with pytest.raises(ValueError, match="different phases"):
        Ruleset([before_rule, react_rule])


def test_ruleset_rejects_ordering_cycle() -> None:
    first = RequireRule(
        "first",
        Move,
        require_position,
        after=("second",),
    )
    second = RejectRule(
        "second",
        Move,
        reject_blocked_move,
        after=("first",),
    )

    with pytest.raises(ValueError, match="Cyclic rule ordering"):
        Ruleset([first, second])


def test_rule_base_is_not_accepted_as_executable_rule() -> None:
    rule = Rule("require_position", Move, require_position)

    with pytest.raises(TypeError, match="Unknown rule type"):
        Ruleset([rule])  # type: ignore[list-item]
