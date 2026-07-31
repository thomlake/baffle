"""The lifecycle invariants Baffle guarantees.

These encode semantics, not implementation. The engine internals may be replaced
freely; these assertions may not change without a deliberate semantic decision.

Ordering follows the lifecycle: resolution, commit, consequence, and the observable
record of all three.
"""

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
    Frame,
    IncrementComponent,
    MoveEntity,
    Mutation,
    RecordLog,
    ReplaceRule,
    SetComponent,
    TransactionEnd,
    WithinBounds,
)
from scenarios import Push, Unlock, door_world, snapshot


def frames(result):
    return [record for record in result.records if isinstance(record, Frame)]


def commits(result):
    return [
        record
        for record in result.records
        if isinstance(record, TransactionEnd) and record.committed
    ]


def mutations(result):
    return [record for record in result.records if isinstance(record, Mutation)]


def moved(event) -> str:
    """The entity a MoveEntity concerns. Records hold `Event`, so narrow before reading."""
    assert isinstance(event, MoveEntity)
    return event.entity


def move_player(engine, state, destination=(1, 0)):
    return engine.simulate(state, MoveEntity(entity="player", destination=destination))


# ---------------------------------------------------------------------------
# 1. Nested success never commits independently.
# ---------------------------------------------------------------------------


def test_prerequisite_success_is_discarded_when_the_root_fails(
    cramped_world, push_engine
):
    """The crate move succeeds inside the transaction, then is rolled back.

    Nothing a prerequisite did may survive the failure of the event that required it.
    """
    before = snapshot(cramped_world)

    result = move_player(push_engine, cramped_world)

    assert not result.root.committed
    assert result.entities == before
    assert cramped_world == before


def test_a_committed_transaction_applies_every_frame(open_world, push_engine):
    """Crate and player both moved, under one commit, leaving the input untouched."""
    result = move_player(push_engine, open_world)

    assert result.root.committed
    assert result.entities["crate"]["position"] == (2, 0)
    assert result.entities["player"]["position"] == (1, 0)
    assert open_world["crate"]["position"] == (1, 0)
    assert open_world["player"]["position"] == (0, 0)


# ---------------------------------------------------------------------------
# 2. Commit happens exactly once, at the root boundary.
# ---------------------------------------------------------------------------


def test_a_transaction_commits_exactly_once(open_world, push_engine):
    """Two events execute -- crate then player -- under a single commit."""
    result = move_player(push_engine, open_world)

    assert len(commits(result)) == 1
    assert len(frames(result)) == 2


def test_a_failed_transaction_never_commits(cramped_world, push_engine):
    result = move_player(push_engine, cramped_world)

    assert commits(result) == []


# ---------------------------------------------------------------------------
# 3. Frames are postorder: children resolve before their parents.
# ---------------------------------------------------------------------------


def test_frames_are_recorded_in_postorder(open_world, push_engine):
    result = move_player(push_engine, open_world)

    assert [moved(frame.event) for frame in frames(result)] == ["crate", "player"]


def test_after_rules_fire_in_postorder(open_world):
    fired: list[str] = []

    class Log(AfterRule[MoveEntity]):
        name = "log"

        def do(self, world, event, result):
            fired.append(event.entity)
            return ()

    engine = Engine(rules=[Push(), Log()])
    move_player(engine, open_world)

    assert fired == ["crate", "player"]


# ---------------------------------------------------------------------------
# 4. after rules see post-commit state; fail rules see pre-transaction state.
# ---------------------------------------------------------------------------


def test_after_rules_observe_committed_state(open_world):
    observed: list[tuple[int, int]] = []

    class Observe(AfterRule[MoveEntity]):
        name = "observe"

        def do(self, world, event, result):
            observed.append(world.vector("player"))
            return ()

    engine = Engine(rules=[Observe()])
    move_player(engine, open_world)

    assert observed == [(1, 0)]


def test_fail_rules_observe_pre_transaction_state(door_state):
    """The key spend succeeded inside the doomed transaction; fail rules must not see it."""
    observed: list[int] = []

    class Veto(BeforeRule[MoveEntity]):
        name = "veto"
        run_after = ("unlock",)

        def do(self, world, event):
            return Failure("vetoed")

    class Observe(FailRule[MoveEntity]):
        name = "observe"

        def do(self, world, event, failure):
            observed.append(world.value("player", "keys"))
            return ()

    engine = Engine(rules=[Unlock(), Veto(), Observe()])
    result = move_player(engine, door_state)

    assert not result.root.committed
    assert observed == [1]


def test_after_rules_receive_the_operation_result(open_world):
    """apply_move computes origin and destination; after rules must be able to read it."""
    seen: list[dict] = []

    class Observe(AfterRule[MoveEntity]):
        name = "observe"

        def do(self, world, event, result):
            seen.append(result)
            return ()

    engine = Engine(rules=[Observe()])
    move_player(engine, open_world)

    assert seen == [{"origin": (0, 0), "destination": (1, 0)}]


# ---------------------------------------------------------------------------
# 5. All bindings for one rule are computed against a single snapshot.
# ---------------------------------------------------------------------------


def test_a_rule_sees_one_view_of_the_world_while_it_fans_out():
    """Both iterations observe the counter before either increment applies.

    The engine drains ``do`` completely before resolving anything it produced. A lazily
    consumed generator would see its own earlier events land mid-iteration, and every
    decision a rule takes after that point would be against a world it never chose.
    """
    observed: list[int] = []

    class Tally(Event):
        name = "test.tally"

    class TwoIncrements(BeforeRule[Tally]):
        name = "two_increments"

        def do(self, world, event):
            for amount in (1, 2):
                observed.append(world.value("counter", "value"))
                yield IncrementComponent(
                    entity="counter", component="value", value=amount
                )

    engine = Engine(rules=[TwoIncrements()])
    result = engine.simulate({"counter": {"value": 0}}, Tally())

    assert observed == [0, 0]
    assert result.entities["counter"]["value"] == 3


# ---------------------------------------------------------------------------
# 6. Rule N observes the mutations emitted by rule N-1.
# ---------------------------------------------------------------------------


def test_earlier_rules_mutations_are_visible_to_later_rules(door_state, door_engine):
    """unlock clears door.solid, so solid finds no obstruction and the move proceeds.

    This is the invariant that breaks silently if rule ordering flips: the move would
    be rejected with a plausible-looking destination_obstructed instead.
    """
    result = move_player(door_engine, door_state)

    assert result.root.committed
    assert result.entities["player"]["position"] == (1, 0)
    assert result.entities["player"]["keys"] == 0
    assert result.entities["door"]["solid"] is False


def test_a_failing_prerequisite_rolls_back_its_siblings(door_engine):
    """With no keys the spend rejects, and the unlocking is discarded with it."""
    state = door_world(keys=0)
    before = snapshot(state)

    result = move_player(door_engine, state)

    assert not result.root.committed
    assert result.root.failure.root.reason == "minimum_violated"
    assert result.entities == before


# ---------------------------------------------------------------------------
# 7. Failure causality survives wrapping.
# ---------------------------------------------------------------------------


def test_the_causal_chain_reaches_the_originating_failure(cramped_world, push_engine):
    result = move_player(push_engine, cramped_world)

    chain = []
    failure = result.root.failure
    while failure is not None:
        chain.append(failure.reason)
        failure = failure.cause

    assert chain == ["required_event_failed", "outside_grid"]
    assert result.root.failure.root.reason == "outside_grid"


def test_the_wrapping_failure_identifies_the_required_event(cramped_world, push_engine):
    """Rendering "the crate did not move" needs the event, not just its type name."""
    result = move_player(push_engine, cramped_world)

    required = result.root.failure.data["required_event"]
    assert isinstance(required, MoveEntity)
    assert required.entity == "crate"


# ---------------------------------------------------------------------------
# 8. The queue is FIFO, and consequences are ordinary root events.
# ---------------------------------------------------------------------------


def test_consequences_are_processed_breadth_first():
    """A produces D, E, F; D produces H, I. Order must be A D E F H I."""
    tree = {"A": ("D", "E", "F"), "D": ("H", "I")}
    order: list[str] = []

    class Node(Event):
        name = "test.node"
        label: str

    class Expand(AfterRule[Node]):
        name = "expand"

        def do(self, world, event, result):
            order.append(event.label)
            for label in tree.get(event.label, ()):
                yield Node(label=label)

    engine = Engine(rules=[Expand()])
    engine.simulate({}, Node(label="A"))

    assert order == ["A", "D", "E", "F", "H", "I"]


def test_a_consequence_gets_its_own_transaction(open_world):
    """An after event is indistinguishable from an external root: own copy, own commit."""

    class Bump(AfterRule[MoveEntity]):
        name = "bump"

        def do(self, world, event, result):
            if event.entity == "player":
                yield IncrementComponent(entity="player", component="moves", value=1)

    open_world["player"]["moves"] = 0
    engine = Engine(rules=[Bump()], narrate=True)
    result = move_player(engine, open_world)

    assert [transaction.committed for transaction in result.transactions] == [True, True]
    assert len(commits(result)) == 2
    assert result.entities["player"]["moves"] == 1


def test_fail_events_reenter_the_queue_as_roots(cramped_world):
    """A fail rule's output is an ordinary root event and commits on its own."""

    class Note(FailRule[MoveEntity]):
        name = "note"

        def do(self, world, event, failure):
            if event.entity == "player":
                yield SetComponent(entity="player", component="blocked", value=True)

    cramped_world["player"]["blocked"] = False
    engine = Engine(rules=[WithinBounds(), Push(), Note()], narrate=True)
    result = move_player(engine, cramped_world)

    assert not result.root.committed
    assert result.entities["player"]["blocked"] is True
    assert [transaction.committed for transaction in result.transactions] == [
        False,
        True,
    ]


# ---------------------------------------------------------------------------
# 9. Replacement applies at every nesting level, not only at the root.
# ---------------------------------------------------------------------------


def test_replacement_applies_to_required_events(open_world):
    """A prerequisite emitted by a before rule is still subject to replace rules."""

    class Confuse(ReplaceRule[MoveEntity]):
        name = "confuse"

        def do(self, world, event):
            if event.entity != "crate":
                return event
            return SetComponent(entity="crate", component="confused", value=True)

    open_world["crate"]["confused"] = False
    engine = Engine(rules=[Push(), Confuse()])
    result = move_player(engine, open_world)

    assert result.root.committed
    assert result.entities["crate"]["confused"] is True
    assert result.entities["crate"]["position"] == (1, 0)
    assert result.entities["player"]["position"] == (1, 0)


def test_replacement_is_an_intercept_not_a_chain():
    """Each replace rule applies at most once, in declared order.

    Chaining works only because the ordering is declared. It is not a guarantee of
    the phase, and rules must not rely on a replacement being re-examined.
    """

    class Alpha(Event):
        name = "test.alpha"

    class Beta(Event):
        name = "test.beta"

    class AlphaToBeta(ReplaceRule[Alpha]):
        name = "alpha_to_beta"

        def do(self, world, event):
            return Beta()

    class BetaToIncrement(ReplaceRule[Beta]):
        name = "beta_to_increment"
        run_after = ("alpha_to_beta",)

        def do(self, world, event):
            return IncrementComponent(entity="counter", component="value", value=1)

    engine = Engine(rules=[AlphaToBeta(), BetaToIncrement()])
    result = engine.simulate({"counter": {"value": 0}}, Alpha())

    assert result.entities["counter"]["value"] == 1


# ---------------------------------------------------------------------------
# Guards. Cycle detection is gone; depth and work budgets replace it.
# ---------------------------------------------------------------------------


def test_a_repeated_prerequisite_is_not_a_cycle():
    """The same event twice on one ancestor path is legal when the chain terminates.

    Descending costs a coin per level. ``Descend`` appears at depth 0, 1 and 2 with
    identical data, so identity-based cycle detection rejected this outright -- yet it
    terminates, because each level's first prerequisite empties the purse a little.

    An earlier sibling's effects are visible to a later one, which is what lets the
    guard eventually go false. That is the property doing the work here.
    """

    class Descend(Event):
        name = "test.descend"

    class Toll(BeforeRule[Descend]):
        name = "toll"

        def do(self, world, event):
            if world.value("purse", "coins") < 1:
                return
            yield IncrementComponent(
                entity="purse", component="coins", value=-1, minimum=0
            )
            yield Descend()

    engine = Engine(rules=[Toll()])
    result = engine.simulate({"purse": {"coins": 2}}, Descend())

    assert result.root.committed
    assert result.entities["purse"]["coins"] == 0


def test_runaway_recursion_reports_its_chain():
    """With identity detection gone, a diagnosable depth fault is the only guard."""

    class Loop(Event):
        name = "test.loop"

    class Forever(BeforeRule[Loop]):
        name = "forever"

        def do(self, world, event):
            yield Loop()

    engine = Engine(rules=[Forever()], max_depth=10)
    with pytest.raises(EngineFault) as excinfo:
        engine.simulate({}, Loop())

    assert excinfo.value.chain is not None
    assert len(excinfo.value.chain) == 10
    assert "forever" in str(excinfo.value)


def test_unbounded_fan_out_is_bounded_by_the_work_budget():
    """Depth alone permitted 8191 events in one transaction at depth 12."""

    class Split(Event):
        name = "test.split"
        depth: int = 0

    class Fan(BeforeRule[Split]):
        name = "fan"

        def do(self, world, event):
            if event.depth < 8:
                yield Split(depth=event.depth + 1)
                yield Split(depth=event.depth + 1)

    engine = Engine(rules=[Fan()], max_events_per_transaction=50)
    with pytest.raises(EngineFault, match="work budget"):
        engine.simulate({}, Split())


# ---------------------------------------------------------------------------
# Ownership. result.entities is never the caller's dict.
# ---------------------------------------------------------------------------


def test_the_callers_state_is_never_returned(cramped_world, push_engine):
    """A failing cascade used to hand back the caller's own dict."""
    before = snapshot(cramped_world)

    result = move_player(push_engine, cramped_world)

    assert result.entities is not cramped_world
    assert cramped_world == before

    # Structural sharing is the point of copy-on-write: an entity nothing wrote to is
    # the same object across generations. Writing to the result must still not reach
    # the caller's mapping.
    result.entities["intruder"] = {}
    assert "intruder" not in cramped_world


def test_untouched_entities_are_shared_between_generations(open_world, push_engine):
    """The copy-on-write contract, stated as a test so it is not "fixed" by accident.

    State handed out by the engine is immutable by convention. Cheap transactions
    depend on it, and so does snapshotting for search.
    """
    result = move_player(push_engine, open_world)

    assert result.root.touched == {"player", "crate"}
    assert result.entities["world"] is open_world["world"]
    assert result.entities["crate"] is not open_world["crate"]


# ---------------------------------------------------------------------------
# Mutation records back rollback, incremental hashing, and rendering.
# ---------------------------------------------------------------------------


def test_mutations_record_the_previous_value(open_world, push_engine):
    """old is what makes incremental hashing and cheap diffing possible."""
    result = move_player(push_engine, open_world)

    recorded = {(record.entity, record.path): record for record in mutations(result)}
    crate = recorded[("crate", "position")]

    assert crate.old == (1, 0)
    assert crate.new == (2, 0)
    assert crate.kind == "replace"


def _vetoed_after_unlocking(state):
    """Prerequisites succeed, then the event that required them is rejected."""

    class Veto(BeforeRule[MoveEntity]):
        name = "veto"
        run_after = ("unlock",)

        def do(self, world, event):
            return Failure("vetoed")

    engine = Engine(rules=[Unlock(), Veto()], narrate=True)
    return engine.simulate(state, MoveEntity(entity="player", destination=(1, 0)))


def test_rolled_back_mutations_are_marked_not_erased(door_state):
    """The renderer needs the discarded work; the hasher needs to skip it."""
    result = _vetoed_after_unlocking(door_state)

    assert not result.root.committed
    assert mutations(result), "the discarded prerequisites must still be recorded"
    assert all(record.rolled_back for record in mutations(result))
    assert list(result.records[0:1]) and not result.records[0].rolled_back


def test_a_rolled_back_success_is_still_recorded(door_state):
    """The key was spent and then it was not. A transcript has to be able to say so.

    The old frame model dropped these entirely: a prerequisite that succeeded before a
    later failure left no trace anywhere, so a player-facing log could not explain
    what happened.
    """
    result = _vetoed_after_unlocking(door_state)

    succeeded = [frame for frame in frames(result) if frame.succeeded]
    failed = [frame for frame in frames(result) if not frame.succeeded]

    assert succeeded, "prerequisites that succeeded before the failure must survive"
    assert all(frame.rolled_back for frame in succeeded)
    assert [type(frame.event).__name__ for frame in failed] == ["MoveEntity"]


@pytest.mark.parametrize("narrate", [True, False])
def test_frames_are_marked_rolled_back_whether_or_not_narrating(narrate, door_state):
    """`Transaction.frames` is populated either way, so its flags mean the same thing.

    Marking used to work only through the record log's span, which frames enter only when
    narrating -- so whether a discarded frame knew it had been discarded depended on a
    debug flag, and a search loop (narration off) saw every frame reporting success.
    """

    class Veto(BeforeRule[MoveEntity]):
        name = "veto"
        run_after = ("unlock",)

        def do(self, world, event):
            return Failure("vetoed")

    engine = Engine(rules=[Unlock(), Veto()], narrate=narrate)
    result = move_player(engine, door_state)

    assert not result.root.committed
    assert result.root.frames, "the discarded work must still be reported"
    assert all(frame.rolled_back for frame in result.root.frames)


def test_committed_mutations_exclude_discarded_work(door_state):
    """The hasher's view: only what survived, in order."""
    result = _vetoed_after_unlocking(door_state)
    log = RecordLog()
    for record in result.records:
        if isinstance(record, Mutation):
            log.mutation(record)

    assert list(log.committed_mutations()) == []
