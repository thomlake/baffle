from collections.abc import Iterable
from dataclasses import dataclass

import pytest

from baffle.events import Event, Rejection, Set
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


@dataclass(frozen=True)
class Move(Event):
    entity: str
    destination: tuple[int, int]


@dataclass(frozen=True)
class SpecialMove(Move):
    pass


@dataclass(frozen=True)
class Step(Event):
    entity: str
    destination: tuple[int, int]


@dataclass(frozen=True)
class Chain(Event):
    remaining: int


def test_event_applies_when_accepted() -> None:
    world = World({"player": {"health": 3}})

    resolution = resolve(
        world,
        Set("player", "health", 2),
    )

    assert resolution.status is ResolutionStatus.ACCEPTED
    assert resolution.accepted
    assert not resolution.rejected
    assert not resolution.aborted
    assert resolution.rejection is None
    assert resolution.rejected_by is None
    assert world.get("player", "health") == 2


def test_required_events_record_rule_provenance() -> None:
    def require_move(
        world: World,
        event: Step,
    ) -> Iterable[Event]:
        yield Move(event.entity, event.destination)

    def require_position(
        world: World,
        event: Move,
    ) -> Iterable[Event]:
        yield Set(
            event.entity,
            "position",
            event.destination,
        )

    step_rule = RequireRule(Step, require_move)
    move_rule = RequireRule(Move, require_position)

    world = World(
        {
            "player": {
                "position": (0, 0),
            }
        }
    )

    step = Step("player", (1, 0))
    move = Move("player", (1, 0))
    set_position = Set("player", "position", (1, 0))

    resolution = resolve(
        world,
        step,
        [step_rule, move_rule],
    )

    assert resolution.status is ResolutionStatus.ACCEPTED
    assert world.get("player", "position") == (1, 0)

    move_requirement = resolution.requirements[0]

    assert move_requirement.rule is step_rule
    assert move_requirement.resolution.event == move

    position_requirement = (
        move_requirement.resolution.requirements[0]
    )

    assert position_requirement.rule is move_rule
    assert position_requirement.resolution.event == set_position
    assert (
        position_requirement.resolution.status
        is ResolutionStatus.ACCEPTED
    )


def test_later_rules_see_previous_required_events() -> None:
    def update_position(
        world: World,
        event: Move,
    ) -> Iterable[Event]:
        yield Set(
            event.entity,
            "position",
            event.destination,
        )

    def reject_wrong_position(
        world: World,
        event: Move,
    ) -> Rejection | None:
        if world.get(event.entity, "position") != event.destination:
            return Rejection("position_not_updated")

        return None

    world = World(
        {
            "player": {
                "position": (0, 0),
            }
        }
    )

    resolution = resolve(
        world,
        Move("player", (1, 0)),
        [
            RequireRule(Move, update_position),
            RejectRule(Move, reject_wrong_position),
        ],
    )

    assert resolution.status is ResolutionStatus.ACCEPTED
    assert world.get("player", "position") == (1, 0)


def test_rule_order_is_shared_across_require_and_reject() -> None:
    def reject_wrong_position(
        world: World,
        event: Move,
    ) -> Rejection | None:
        if world.get(event.entity, "position") != event.destination:
            return Rejection("position_not_updated")

        return None

    def update_position(
        world: World,
        event: Move,
    ) -> Iterable[Event]:
        yield Set(
            event.entity,
            "position",
            event.destination,
        )

    rejection_rule = RejectRule(Move, reject_wrong_position)
    initial = {"player": {"position": (0, 0)}}
    world = World(initial)

    resolution = resolve(
        world,
        Move("player", (1, 0)),
        [
            rejection_rule,
            RequireRule(Move, update_position),
        ],
    )

    assert resolution.status is ResolutionStatus.REJECTED
    assert resolution.rejected
    assert resolution.rejection == Rejection(
        "position_not_updated"
    )
    assert resolution.rejected_by is rejection_rule
    assert resolution.requirements == ()
    assert world.snapshot() == initial


def test_one_rule_observes_one_world_version() -> None:
    observations: list[int] = []

    def update_health(
        world: World,
        event: Move,
    ) -> Iterable[Event]:
        health = world.get(event.entity, "health")
        assert isinstance(health, int)
        observations.append(health)

        yield Set(event.entity, "health", 2)

        health = world.get(event.entity, "health")
        assert isinstance(health, int)
        observations.append(health)

        yield Set(event.entity, "health", 1)

    world = World({"player": {"health": 3}})

    resolution = resolve(
        world,
        Move("player", (1, 0)),
        [RequireRule(Move, update_health)],
    )

    assert resolution.status is ResolutionStatus.ACCEPTED
    assert observations == [3, 3]
    assert world.get("player", "health") == 1


def test_direct_rejection_discards_required_changes() -> None:
    def spend_health(
        world: World,
        event: Move,
    ) -> Iterable[Event]:
        yield Set(event.entity, "health", 2)

    def reject_move(
        world: World,
        event: Move,
    ) -> Rejection:
        return Rejection("blocked")

    require_rule = RequireRule(Move, spend_health)
    reject_rule = RejectRule(Move, reject_move)

    initial = {"player": {"health": 3}}
    world = World(initial)
    move = Move("player", (1, 0))

    resolution = resolve(
        world,
        move,
        [require_rule, reject_rule],
    )

    assert resolution.status is ResolutionStatus.REJECTED
    assert resolution.rejection == Rejection("blocked")
    assert resolution.rejected_by is reject_rule
    assert world.snapshot() == initial
    assert resolution.requirements == (
        Requirement(
            rule=require_rule,
            resolution=Resolution(
                event=Set("player", "health", 2),
                status=ResolutionStatus.ACCEPTED,
            ),
        ),
    )


def test_child_rejection_aborts_parent() -> None:
    def require_move(
        world: World,
        event: Step,
    ) -> Iterable[Event]:
        yield Move(event.entity, event.destination)

    def reject_move(
        world: World,
        event: Move,
    ) -> Rejection:
        return Rejection("blocked")

    require_rule = RequireRule(Step, require_move)
    reject_rule = RejectRule(Move, reject_move)

    initial = {"player": {}}
    world = World(initial)

    step = Step("player", (1, 0))
    move = Move("player", (1, 0))

    resolution = resolve(
        world,
        step,
        [require_rule, reject_rule],
    )

    assert resolution.status is ResolutionStatus.ABORTED
    assert resolution.aborted
    assert not resolution.rejected
    assert resolution.rejection is None
    assert resolution.rejected_by is None
    assert world.snapshot() == initial

    requirement = resolution.requirements[0]
    child = requirement.resolution

    assert requirement.rule is require_rule
    assert child.event == move
    assert child.status is ResolutionStatus.REJECTED
    assert child.rejection == Rejection("blocked")
    assert child.rejected_by is reject_rule


def test_requirements_after_rejection_are_not_attempted() -> None:
    attempted: list[Event] = []

    @dataclass(frozen=True)
    class First(Event):
        pass

    @dataclass(frozen=True)
    class Second(Event):
        pass

    def require_children(
        world: World,
        event: Step,
    ) -> Iterable[Event]:
        yield First()
        yield Second()

    def reject_first(
        world: World,
        event: First,
    ) -> Rejection:
        attempted.append(event)
        return Rejection("blocked")

    def observe_second(
        world: World,
        event: Second,
    ) -> Iterable[Event]:
        attempted.append(event)
        return ()

    world = World({})

    resolution = resolve(
        world,
        Step("player", (1, 0)),
        [
            RequireRule(Step, require_children),
            RejectRule(First, reject_first),
            RequireRule(Second, observe_second),
        ],
    )

    assert resolution.status is ResolutionStatus.ABORTED
    assert attempted == [First()]
    assert [
        requirement.resolution.event
        for requirement in resolution.requirements
    ] == [First()]


def test_rules_match_event_subclasses() -> None:
    called: list[Move] = []

    def observe_move(
        world: World,
        event: Move,
    ) -> Iterable[Event]:
        called.append(event)
        return ()

    world = World({"player": {}})
    event = SpecialMove("player", (1, 0))

    resolution = resolve(
        world,
        event,
        [RequireRule(Move, observe_move)],
    )

    assert resolution.status is ResolutionStatus.ACCEPTED
    assert called == [event]


def test_apply_exception_propagates_without_committing() -> None:
    world = World({})

    with pytest.raises(KeyError):
        resolve(
            world,
            Set("missing", "health", 3),
        )

    assert world.snapshot() == {}


def test_resolver_rejects_react_rules() -> None:
    def react(
        world: World,
        event: Move,
    ) -> Iterable[Event]:
        return ()

    with pytest.raises(
        TypeError,
        match="accepts only RequireRule and RejectRule",
    ):
        Resolver(
            [ReactRule(Move, react)],  # type: ignore[list-item]
        )


def test_resolver_config_has_default_limits() -> None:
    config = ResolverConfig()

    assert config.max_depth == 100
    assert config.max_events == 1_000


def test_resolver_uses_default_config_when_none() -> None:
    resolver = Resolver(config=None)
    world = World({"player": {"health": 3}})

    resolution = resolver.resolve(
        world,
        Set("player", "health", 2),
    )

    assert resolution.status is ResolutionStatus.ACCEPTED


def test_max_depth_zero_allows_root_event() -> None:
    world = World({"player": {"health": 3}})

    resolution = resolve(
        world,
        Set("player", "health", 2),
        config=ResolverConfig(max_depth=0),
    )

    assert resolution.status is ResolutionStatus.ACCEPTED


def test_max_depth_zero_rejects_required_child() -> None:
    def require_position(
        world: World,
        event: Move,
    ) -> Iterable[Event]:
        yield Set(
            event.entity,
            "position",
            event.destination,
        )

    initial = {"player": {"position": (0, 0)}}
    world = World(initial)

    with pytest.raises(
        ResolutionLimitError,
        match="Maximum requirement depth exceeded: 0",
    ):
        resolve(
            world,
            Move("player", (1, 0)),
            [RequireRule(Move, require_position)],
            config=ResolverConfig(max_depth=0),
        )

    assert world.snapshot() == initial


def test_max_depth_allows_event_at_configured_depth() -> None:
    def require_chain(
        world: World,
        event: Chain,
    ) -> Iterable[Event]:
        if event.remaining > 0:
            yield Chain(event.remaining - 1)

    world = World({})

    resolution = resolve(
        world,
        Chain(2),
        [RequireRule(Chain, require_chain)],
        config=ResolverConfig(max_depth=2),
    )

    assert resolution.status is ResolutionStatus.ACCEPTED

    child = resolution.requirements[0].resolution
    grandchild = child.requirements[0].resolution

    assert child.event == Chain(1)
    assert grandchild.event == Chain(0)


def test_max_events_counts_required_events() -> None:
    def require_position(
        world: World,
        event: Move,
    ) -> Iterable[Event]:
        yield Set(
            event.entity,
            "position",
            event.destination,
        )

    initial = {"player": {"position": (0, 0)}}
    world = World(initial)

    with pytest.raises(
        ResolutionLimitError,
        match="Maximum event count exceeded: 1",
    ):
        resolve(
            world,
            Move("player", (1, 0)),
            [RequireRule(Move, require_position)],
            config=ResolverConfig(max_events=1),
        )

    assert world.snapshot() == initial


def test_resolver_shares_event_budget_across_calls() -> None:
    resolver = Resolver(
        config=ResolverConfig(max_events=2),
    )
    world = World({"player": {"health": 3}})

    first = resolver.resolve(
        world,
        Set("player", "health", 2),
    )
    second = resolver.resolve(
        world,
        Set("player", "health", 1),
    )

    assert first.status is ResolutionStatus.ACCEPTED
    assert second.status is ResolutionStatus.ACCEPTED

    with pytest.raises(
        ResolutionLimitError,
        match="Maximum event count exceeded: 2",
    ):
        resolver.resolve(
            world,
            Set("player", "health", 0),
        )

    assert world.get("player", "health") == 1


def test_convenience_resolve_uses_fresh_event_budget() -> None:
    world = World({"player": {"health": 3}})
    config = ResolverConfig(max_events=1)

    first = resolve(
        world,
        Set("player", "health", 2),
        config=config,
    )
    second = resolve(
        world,
        Set("player", "health", 1),
        config=config,
    )

    assert first.status is ResolutionStatus.ACCEPTED
    assert second.status is ResolutionStatus.ACCEPTED


@pytest.mark.parametrize("max_depth", [-1, -10])
def test_resolver_config_rejects_negative_max_depth(
    max_depth: int,
) -> None:
    with pytest.raises(
        ValueError,
        match="max_depth must be non-negative",
    ):
        ResolverConfig(max_depth=max_depth)


@pytest.mark.parametrize("max_events", [0, -1])
def test_resolver_config_rejects_non_positive_max_events(
    max_events: int,
) -> None:
    with pytest.raises(
        ValueError,
        match="max_events must be positive",
    ):
        ResolverConfig(max_events=max_events)
