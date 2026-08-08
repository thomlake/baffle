# Baffle

> A rules engine for composable event-driven simulations

Baffle is a Python library for building interactive environments whose behavior emerges from ordered rules. It is designed for grid worlds, games, agent evaluations, controlled post-training environments, and other systems where actions may require additional events, be rejected by the current state, or trigger subsequent effects.

The core model is intentionally small:

* **Require rules** emit events that must succeed in the same atomic transaction.
* **Reject rules** prevent an event based on the current world.
* **React rules** emit new root events after a transaction commits or rolls back.

Baffle does not prescribe turns, grids, rendering, rewards, observations, or agent APIs. Those can be built in ordinary Python on top of the event resolution model.

## Example

```python
from collections.abc import Iterable
from dataclasses import dataclass

from baffle import (
    Event,
    Rejected,
    Rejection,
    ResolutionStatus,
    Ruleset,
    Set,
    World,
    react,
    reject,
    require,
    submit,
)


@dataclass(frozen=True)
class Move(Event):
    entity: str
    destination: tuple[int, int]


@dataclass(frozen=True)
class EnterTile(Event):
    entity: str
    destination: tuple[int, int]


@require
def require_entry(world: World, event: Move) -> Iterable[Event]:
    yield EnterTile(
        entity=event.entity,
        destination=event.destination,
    )


@reject
def reject_solid_tiles(world: World, event: EnterTile) -> Rejection | None:
    if event.destination == (1, 0):
        return Rejection("solid")

    return None


@require(after=("reject_solid_tiles",))
def set_position(world: World, event: EnterTile) -> Iterable[Event]:
    yield Set(
        entity=event.entity,
        component="position",
        value=event.destination,
    )


@react
def record_failed_move(world: World, event: Rejected) -> Iterable[Event]:
    if isinstance(event.root, Move):
        yield Set(
            entity=event.root.entity,
            component="last_move_failed",
            value=True,
        )


ruleset = Ruleset(
    [require_entry, reject_solid_tiles, set_position, record_failed_move]
)

world = World(
    {
        "player": {
            "position": (0, 0),
        }
    }
)

trace = submit(
    world,
    Move("player", (1, 0)),
    ruleset,
)

assert trace.root.status is ResolutionStatus.ABORTED
assert world.snapshot() == {
    "player": {
        "position": (0, 0),
        "last_move_failed": True,
    }
}
```

The submitted `Move` requires `EnterTile`. The tile-entry event is rejected, so the entire transaction rolls back. Submission processing then exposes the failed transaction as a `Rejected` event, allowing a reaction to record the failed move in a new transaction.

## Why Baffle?

Many small environment engines start with a direct implementation of each mechanic:

```python
def move_player(...):
    if blocked:
        ...
    if pushing:
        ...
    if standing_on_switch:
        ...
    if entering_hazard:
        ...
    if carrying_key:
        ...
```

This works initially, but mechanics quickly become entangled. Adding one new way to move may require updating every mechanic that cares about movement. Adding one new obstacle may require changing every action that can cross a tile.

Baffle is designed around a different goal:

> Mechanics should compose by responding to shared events, rather than knowing about each other.

A high-level event can require lower-level events:

```text
Move
└── EnterTile
    └── Set position
```

Different mechanics attach to the level they actually understand.

A movement rule knows that moving requires entering a tile:

```python
@require
def require_entry(world: World, event: Move): ...
```

A solids rule knows that some tile entries are invalid:

```python
@reject
def reject_solids(world: World, event: EnterTile): ...
```

A pressure-plate rule knows that successful tile entry may change a switch:

```python
@react
def update_pressure_plate(world: World, event: EnterTile): ...
```

None of these rules needs to know about the others.

### One mechanic applies to many causes

Suppose an entity can change position through:

* walking;
* pushing;
* teleportation;
* knockback;
* scripted movement.

A direct implementation often duplicates occupancy checks and tile effects across all five paths.

With Baffle, those higher-level events can all require the same lower-level event:

```text
Move ────────┐
Push ────────┤
Teleport ────┼──> EnterTile
Knockback ───┤
PatrolStep ──┘
```

The solids rule only handles `EnterTile`. A hazard rule only reacts to `EnterTile`. A pressure plate only reacts to occupancy changes.

Adding teleportation does not require editing solids, hazards, or pressure plates.

### One cause can participate in many mechanics

The same event may be interpreted by several independent rules.

```text
EnterTile
├── reject if solid
├── require opening an unlocked door
├── require pushing an occupant
├── react by triggering a hazard
├── react by updating a pressure plate
└── react by recording exploration
```

Each rule contributes one piece of behavior. The resulting mechanic emerges from their ordered composition.

For example, pushing a block onto a pressure plate can work without a dedicated "push block onto pressure plate" implementation:

```text
Move player
└── EnterTile player
    └── Push block
        └── EnterTile block
            └── Set block position

after commit:
EnterTile block
└── update pressure plate
```

The pushing mechanic and pressure-plate mechanic remain independent. They compose because both use the same event vocabulary.

### Rejection is compositional too

A failed action often has two useful meanings:

```text
Move
└── EnterTile
    └── rejected: occupied
```

A scripted policy may care that its root `Move` failed and choose another action.

A physical mechanic may care that `EnterTile` was rejected and produce a bump, attack, or door interaction.

Baffle preserves both:

```python
Rejected(
    root=move,
    event=enter_tile,
    rejection=Rejection("occupied"),
)
```

This avoids coupling fallback behavior to the exact internal chain that caused the action to fail.

### Atomicity makes composition safer

Compositional rules may produce several dependent changes:

```text
OpenDoor
├── consume key
├── mark door open
└── enter doorway
```

If the final requirement fails, partially consuming the key would be incorrect.

Baffle resolves all requirements against a working copy and commits only if the entire root succeeds. Rules can therefore compose without each mechanic implementing its own rollback logic.

### Causality remains inspectable

Compositional systems can become difficult to debug if the engine only exposes the final state.

Baffle retains the causal structure:

```text
Move
└── required by require_entry
    └── EnterTile
        └── rejected by reject_solids
```

Reaction-generated roots also record which rule emitted them and which event that rule observed.

The aim is not merely to make mechanics extensible. It is to make emergent behavior understandable after it happens.

### The intended tradeoff

Baffle does not try to infer a perfect universal event vocabulary. Environment authors still choose the events that form useful composition boundaries.

Good events usually describe reusable state transitions or meaningful attempts:

```text
EnterTile
Set
Damage
Acquire
Open
OccupancyChanged
```

Poor boundaries tend to encode an entire mechanic:

```text
PushBlockOntoPressurePlateWhileDoorIsOpen
```

The core design bet is that a small set of well-chosen events, combined through require, reject, and react rules, is easier to extend than a growing collection of directly implemented actions and special cases.


## Execution model

Submitting one event may produce several atomic root transactions.

```text
submit external event
│
├── resolve root transaction
│   │
│   ├── run matching require and reject rules in order
│   ├── recursively resolve required events
│   ├── apply accepted events
│   └── commit everything or roll everything back
│
├── run reactions against the committed or rolled-back world
├── enqueue emitted events as new root transactions
└── repeat until no events remain
```

Required events execute before the event that required them.

```text
Move
└── EnterTile
    └── Set position
```

The execution order is:

```text
Set position
EnterTile
Move
```

React rules observe accepted events in this same order.

## Events

Events are frozen dataclasses.

```python
from dataclasses import dataclass

from baffle import Event


@dataclass(frozen=True)
class Damage(Event):
    entity: str
    amount: int
```

An event may directly modify the world by overriding `apply`:

```python
@dataclass(frozen=True)
class IncrementTurn(Event):
    def apply(self, world: World) -> None:
        turn = world.get("game", "turn")
        assert isinstance(turn, int)

        world.set("game", "turn", turn + 1)
```

Most domain events can remain no-ops. Their behavior can instead be expressed through required events:

```python
@dataclass(frozen=True)
class Move(Event):
    entity: str
    destination: tuple[int, int]
```

Baffle includes three primitive state-changing events:

```python
Create(entity, components)
Delete(entity)
Set(entity, component, value)
```

## Rules

Rules match events using `isinstance`, so a rule registered for a base event class also handles subclasses.

Rule decorators infer the event type and default rule name from the callback. Decorated rules remain callable, and their original callback is available as `rule.run`.

Direct construction requires both values explicitly:

```python
rule = RequireRule("require_position_update", Move, require_position_update)
```

### Require rules

A require rule emits events that must resolve before the current event may execute.

```python
@require
def require_position_update(
    world: World,
    event: Move,
) -> Iterable[Event]:
    yield Set(
        event.entity,
        "position",
        event.destination,
    )
```

All events emitted by one rule invocation are collected before any are resolved. The callback therefore observes one stable version of the world.

Required events resolve sequentially. Later rules may observe changes made by earlier requirements.

### Reject rules

A reject rule returns a `Rejection` or `None`.

```python
@reject
def reject_missing_energy(
    world: World,
    event: Move,
) -> Rejection | None:
    energy = world.get(event.entity, "energy")

    if not isinstance(energy, int) or energy <= 0:
        return Rejection("insufficient_energy")

    return None
```

A rejection rolls back the entire current root transaction.

Reject rules represent modeled outcomes. Exceptions from event application or rule execution are engine or programming failures and propagate normally.

### React rules

A react rule emits events after a transaction has committed or rolled back.

```python
@react
def damage_on_entry(
    world: World,
    event: EnterTile,
) -> Iterable[Event]:
    if event.destination == (4, 2):
        yield Damage(event.entity, 1)
```

Reaction emissions are new root transactions. They are not part of the transaction that triggered them.

All reactions to one transaction run before any emitted event is submitted. They therefore observe the same world state.

Reaction roots are processed in FIFO order.

React rules are also the only way to observe a `Rejected` event. Submission processing synthesizes one to report a failed transaction and hands it to react rules; it never resolves it. A require or reject rule registered for `Rejected` would therefore never run, so `Ruleset` rejects one during construction:

```python
Ruleset([RequireRule("invalid", Rejected, ...)])
# TypeError: Rejected is observation-only; dispatch on it with ReactRule
```

## Rule ordering

Require and reject rules share one ordered pre-execution phase.

```python
@require
def push_block(world: World, event: Move): ...


@reject(after=("push_block",))
def reject_if_still_blocked(world: World, event: Move): ...
```

Here, the push requirement resolves first regardless of registration order. The rejection rule then checks the updated transactional world.

React rules run in a separate phase and are sorted independently. A rule cannot declare an ordering relationship with a rule in the other phase.

Rules without a constraint retain registration order. Names default to callback names and can be overridden when a stable or disambiguated name is needed:

```python
@require(name="movement.push", before=("collision",))
def push_block(world: World, event: Move): ...
```

`Ruleset` validates names, references, phases, and cycles while compiling its immutable rule groups.

## Rejections

A failed root transaction produces one `Rejected` event:

```python
@dataclass(frozen=True)
class Rejected(Event):
    root: Event
    event: Event
    rejection: Rejection
```

These fields distinguish:

* `root`: the root event whose transaction failed;
* `event`: the event directly rejected by a rule;
* `rejection`: the modeled reason.

For example:

```text
Move                 aborted
└── EnterTile        rejected: occupied
```

produces:

```python
Rejected(
    root=move,
    event=enter_tile,
    rejection=Rejection("occupied"),
)
```

A policy can respond to the failed root action:

```python
if isinstance(rejected.root, Move):
    yield choose_alternative_move(...)
```

A mechanic can respond to the direct physical failure:

```python
if isinstance(rejected.event, EnterTile):
    yield Bump(...)
```

Requirements are ordered and short-circuiting. Once one requirement fails, later requirements are not attempted.

An unsuccessful resolution therefore has exactly one direct rejection, reachable from any ancestor:

```python
rejected = trace.root.rejected_resolution

if rejected is not None:
    rejected.event        # the event a rule rejected
    rejected.rejection    # the modeled reason
    rejected.rejected_by  # the rule responsible
```

`rejected_resolution` returns `None` for an accepted resolution and `self` for a directly rejected one, so `rejection` and `rejected_by` are read the same way whether a rule rejected this event or one of its requirements.

## Resolution status

Every resolved event has one of three statuses:

```python
class ResolutionStatus(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    ABORTED = "aborted"
```

* **accepted**: the event and all requirements executed successfully;
* **rejected**: this event was directly rejected by a `RejectRule`;
* **aborted**: this event did not execute because a requirement was unsuccessful.

Top-level callers can inspect the submitted event through the returned trace:

```python
trace = submit(world, event, ruleset)

if trace.root.status is not ResolutionStatus.ACCEPTED:
    ...
```

## Tracing and provenance

`submit` returns a `Trace`.

```python
trace = submit(world, event, ruleset)
```

A trace contains one entry for each root transaction:

```python
@dataclass(frozen=True)
class Trace:
    entries: tuple[TraceEntry, ...]

    @property
    def root(self) -> Resolution:
        ...
```

The first entry always belongs to the externally submitted event. Later entries belong to events emitted by reactions.

Each `Resolution` contains its requirement tree:

```python
@dataclass(frozen=True)
class Requirement:
    rule: RequireRule
    resolution: Resolution
```

Direct rejections record the rule responsible:

```python
resolution.rejection
resolution.rejected_by
```

Reaction-generated roots record both the reaction rule and the event it observed:

```python
@dataclass(frozen=True)
class Reaction:
    rule: ReactRule
    source: Event
```

Each entry also records which transaction produced it:

```python
@dataclass(frozen=True)
class TraceEntry:
    resolution: Resolution
    reaction: Reaction | None = None
    parent: int | None = None
```

`parent` indexes into `Trace.entries`, and is `None` for the externally submitted event. The reaction alone cannot identify the parent, because two transactions may emit equal events. The index makes the chain of root transactions reconstructable:

```text
[0] Move      parent=None   submitted
[1] Damage    parent=0      a hazard reacted to the move
[2] Die       parent=1      death reacted to the damage
```

Entries are root transactions only. Required events are not entries; they live in the `Resolution` tree of the entry that required them.

This preserves the two causal relationships in submission processing:

```text
require rule ──> child event in the same transaction

react rule   ──> new root transaction
```

## World state

A world stores entities and their components:

```python
world = World(
    {
        "player": {
            "position": (0, 0),
            "health": 3,
        },
        "door": {
            "open": False,
        },
    }
)
```

Read and mutation operations are intentionally small:

```python
world.get(entity, component)
world.get(entity, component, default=value)
world.has(entity, component)

world.create(entity, components)
world.delete(entity)
world.set(entity, component, value)
```

A world is also a collection of entities, which is enough to scan without copying:

```python
entity in world
len(world)

occupants = [
    entity
    for entity in world
    if world.get(entity, "position", default=None) == destination
]
```

`components` returns a read-only view of one entity, not a copy:

```python
for component, value in world.components("player").items():
    ...
```

Baffle deliberately stops here. Indexes and query languages belong in code built on top of the core.

Rules should treat the world as read-only. State changes should be represented by events.

Use `snapshot` when crossing the engine boundary:

```python
state = world.snapshot()
```

The snapshot is independent of the mutable world and is suitable for persistence, observations, comparisons, and serialization.

## Atomicity

Each root event resolves against a private working copy of the world.

If the root is accepted:

```text
working world replaces current world
```

If any required event is rejected:

```text
working world is discarded
```

This is expressed as a transaction. Rolling back is simply never committing, so a discarded working copy needs no cleanup:

```python
transaction = world.transaction()
resolution = resolve_somehow(transaction.world, event)

if resolution.accepted:
    transaction.commit()
```

A committed transaction hands its state over rather than copying it again, so it must not be reused afterwards.

Reaction-generated events are separate root transactions. Earlier roots may remain committed if a later root raises an exception or exceeds an execution limit.

## Execution limits

Recursive requirements and reactions can otherwise continue indefinitely.

```python
from baffle import ResolverConfig, submit


trace = submit(
    world,
    event,
    ruleset,
    config=ResolverConfig(
        max_depth=100,
        max_events=1_000,
    ),
)
```

* `max_depth` limits requirement nesting. The root event has depth zero.
* `max_events` limits event-resolution attempts across one submission, including required events and reaction-generated roots.

Exceeding either limit raises `ResolutionLimitError`.

Each call to `submit` receives a fresh event budget.

A limit can be exceeded partway through a submission, after earlier root transactions have already committed. The error carries what happened, so a runaway cascade can be diagnosed rather than guessed at:

```python
try:
    submit(world, event, ruleset, config=config)
except ResolutionLimitError as error:
    error.event  # the event whose resolution hit the limit
    error.trace  # the root transactions that committed before it
```

The transaction that hit the limit is not among them. It is discarded like any other unsuccessful root, so `error.trace` accounts for exactly the changes visible in the world.

## Lower-level resolution

`submit` is the normal external entry point.

For tests or lower-level use, one event can be resolved without running reactions:

```python
from baffle import resolve


resolution = resolve(
    world,
    event,
    before_rules,
)
```

A reusable `Resolver` shares its event budget across calls:

```python
from baffle import Resolver, ResolverConfig


resolver = Resolver(
    before_rules,
    config=ResolverConfig(max_events=100),
)

first = resolver.resolve(world, event_a)
second = resolver.resolve(world, event_b)
```

## Design principles

Baffle favors:

* deterministic execution;
* explicit causal structure;
* atomic state transitions;
* ordinary Python events and rules;
* compositional mechanics;
* serialization-friendly state;
* small core abstractions.

Baffle deliberately avoids:

* a generic event bus;
* a full entity-component-system framework;
* hidden mutation by rules;
* hard-coded game concepts;
* separate operation and action hierarchies;
* special-case movement phases;
* asynchronous or concurrent resolution.

## V1 scope

The current V1 core includes:

* world state;
* plain dataclass events;
* primitive create, delete, and set events;
* require, reject, and react rules;
* atomic recursive resolution;
* rejection handling;
* reaction queues;
* execution limits;
* causal tracing and provenance;
* a typed public API exported from `baffle`.

Planned V1 work includes:

* explicit before/after rule priority;
* a component value type that does not assume grid coordinates;
* a movement, solids, and pushing example;
* serialization and deterministic replay;
* additional documentation.

Turn scheduling, rendering, observations, rewards, async execution, generic query systems, and copy-on-write optimization are intentionally outside the initial core.
