# Baffle

A transactional rules engine for discrete games — Sokoban, board games, turn-based battle systems.

"Discrete" is about cause and effect, not simulated time. There is no clock. Turns, rounds, and cooldowns are component state that rules advance, so a game decides for itself what a turn means.

The central invariant:

> An operation may mutate the transaction's working state, but only the root transaction commits authoritative state.

Everything else follows from it. Prerequisites resolve inside their parent's transaction, so a chain of them either happens whole or not at all. Consequences re-enter the same queue as the event the caller submitted, so there is exactly one mechanism for cascades and no special cases.

## Minimal example

```python
from baffle import Engine, MoveEntity, WORLD

state = {
    WORLD: {"width": 5, "height": 5},
    "player": {"position": (1, 1), "solid": True},
}

result = Engine().simulate(state, MoveEntity(entity="player", destination=(2, 1)))

assert result.entities["player"]["position"] == (2, 1)
assert state["player"]["position"] == (1, 1)   # the input is never touched
```

## Writing a rule

A rule is a plain class with one method. `do` reads the world and yields events — none to stay out of the way, one for the common case, many to fan out. The type argument says which events it matches.

```python
from baffle import BeforeRule, Failure, MoveEntity, delta, shift

class Push(BeforeRule[MoveEntity]):
    run_before = ("solid",)

    def do(self, world, event):
        offset = delta(world.vector(event.entity), event.destination)
        for entity_id in world.query("pushable", position=event.destination):
            yield MoveEntity(
                entity=entity_id,
                destination=shift(world.vector(entity_id), offset),
            )

class Solid(BeforeRule[MoveEntity]):
    def do(self, world, event):
        for entity_id in world.query("solid", position=event.destination):
            if entity_id != event.entity:   # or standing still would be illegal
                return Failure("destination_obstructed", {"obstruction": entity_id})
        return ()
```

`Engine(rules=[Push(), Solid()])` now pushes crates, and refuses to walk into walls.

There is no separate selection step and no binding mechanism. Fanning out is an ordinary `for` loop, so whatever a rule needs per iteration is a local variable rather than a dict threaded through the engine.

That is also what makes rules **checkable**. Because the signature is identical for every rule of a phase, a type checker infers both parameters with nothing annotated:

```text
world                                         -> World
event                                         -> MoveEntity
world.qeury("pushable")                       error: Cannot access attribute "qeury"
event.destinaton                              error: Cannot access attribute "destinaton"
MoveEntity(entity="x", destinaton=(1, 1))     error: No parameter named "destinaton"
```

`BeforeRule[MoveEntity]` also *is* the event declaration — `on` is derived from it — so the two cannot drift apart. Use a union to match several (`BeforeRule[MoveEntity | Step]`), `BeforeRule[Event]` to match everything, or bare `BeforeRule` as an untyped escape hatch.

The parameter is `world`, a `World`: the transaction's live, queryable view. The plain snapshot mapping — `result.entities`, `Transaction.entities`, `state_key(entities)` — keeps the name `entities`, because that is the thing you iterate as `for entity, components in entities.items()`.

## The four phases

```text
REPLACE   Rewrite the event. An intercept, not a chain. Returns one event.
BEFORE    Require events, or refuse. Requirements resolve in this transaction.
AFTER     React to a commit. Receives what the operation computed.
FAIL      React to a discard. Sees the world from before the transaction.
```

`AfterRule` and `FailRule` produce new root events. Neither can refuse — the transaction has already been decided.

A `before` rule refuses by returning a `Failure`, or by *yielding* one if it is a generator (where `return` would be swallowed). Producing events and a refusal together is incoherent and rejected.

## Ordering is declared

`run_before` and `run_after` name other rules, and are topologically sorted when the rule set compiles. Registration order is the tiebreak between rules whose relative order is otherwise unconstrained.

Numeric priorities are deliberately absent. `push` must precede `solid`, and with priority numbers getting that wrong produces a *plausible* wrong answer — the move is rejected as obstructed and keys are never consumed — rather than an error. Declared constraints put the dependency where a reader will find it, make a cycle a load-time failure, and stop a newly added rule from silently reordering an existing pair.

Constraints naming a rule that is not installed are vacuous, so a mechanic can declare where it sits without requiring its neighbours.

## Two subtleties worth knowing

**A rule sees one view of the world; rules are sequential with each other.** The engine drains `do` completely before resolving anything it produced, so a rule that fans out decides every iteration against the same world. But the *next* rule sees everything the previous one did. That is what makes prerequisites compose:

```python
class Unlock(BeforeRule[MoveEntity]):
    run_before = ("solid",)

    def do(self, world, event):
        for door in world.query("locked", position=event.destination):
            yield IncrementComponent(
                entity=event.entity, component="keys", value=-1, minimum=0
            )
            yield SetComponent(entity=door, component="solid", value=False)
```

`Unlock` clears `door.solid`, so when `solid` runs afterwards it finds no obstruction and the move proceeds. If the player has no keys the spend refuses, the unlocking is rolled back with it, and the move fails.

**Replacement is an intercept, not a fixed point.** Each replace rule applies at most once, in declared order, and a rule that already ran does not re-examine a later rewrite. This is deliberate: a confusion effect that flips a direction must not ping-pong. If you want a chain, declare the ordering — do not rely on one rewrite being re-matched.

## State

A world is entities; an entity is components; a component holds one **immutable** value.

```python
type JsonValue = None | bool | int | str | tuple[JsonValue, ...]
```

Component keys are flat strings, namespaced with dots:

```python
world.value("player", "inventory.keys.red")
SetComponent(entity="player", component="statuses.poison.turns", value=3)
```

Both decisions are load-bearing, and they are the same decision twice.

**Values are immutable** so that a transaction is cheap and aliasing is impossible. Copying an entity is `dict(components)` rather than a deep copy; hashing a world only has to settle ordering; and nothing can be stored into the world and then changed from outside it. Tuples rather than lists because a tuple is hashable, and events — frozen dataclasses that get hashed for transposition tables — carry component values as fields.

**Keys are flat** because that is how you get immutability in Python without a `frozendict`. A key that cannot address *into* a value never has to be walked, split, or type-dispatched: reading a component is one dict lookup. Tuples are opaque leaves, so there is no `"order.0"`. A rule that wants to change one element reads the tuple, computes the new one, and sets it — which also keeps the resulting event meaningful independently of when it runs.

A list decoded from JSON is converted to a tuple on the way in. A float, a dict, or anything else is refused at the write, where the author can see which line caused it.

### The trade

Flat keys cannot check that a parent exists. `component="inventroy.keys"` silently creates a new component where a nested store would have refused, because a flat store cannot tell a leaf from an interior node. `SetComponent(create=False)` is the lever when it matters; a declared per-game component schema is the real answer and is not built.

### Reading components

A component holds a `JsonValue`, so passing one straight to grid arithmetic is untyped. `world.vector(entity_id)` validates and narrows in one step, which is the difference between a checked rule and a rule full of casts:

```python
offset = delta(world.vector(event.entity), event.destination)   # Vec2
count  = world.value("player", "keys", default=0)              # defaults instead of raising
```

For whether a component is *present*, ask the mapping: `"keys" in world["player"]`.

### Queries

```python
world.query("pushable", position=event.destination)
world.query("solid", falsy=("fixed",), position=(1, 0))
```

`truthy` (the positional arguments) and `falsy` read a component **truthily**: absent, `False`, and `0` are alike to them. That is what a game wants. `Unlock` above disables a door by setting `solid` to `False` rather than removing it, and `solid` has to stop matching for the move to proceed; `query("inventory.keys.red")` means "has at least one" without a separate check for zero.

They were `has` and `exclude`, which hid both that they are a symmetric pair and that neither is about presence.

Two shapes the keyword form cannot express — a component literally named `falsy`, and a dotted key, which is not a valid identifier. Build a `Query` and call `run` for either.

## Events

An event is an immutable value object describing an intent. It carries only what its author supplied — never anything derived from state — so it means the same thing whenever it is read: when it is emitted, when it executes, when it is hashed for a transposition table, when it is replayed from a log.

Whatever an operation *computes* goes in the result, which `after` rules receive:

```python
class Footprint(AfterRule[MoveEntity]):
    def do(self, world, event, result):
        yield SetComponent(entity="trail", component="last", value=result["origin"])
```

That split is why `MoveEntity` carries a concrete `destination` rather than a direction. Deriving position lazily would make the event ambiguous between emission and execution — and those differ, because prerequisites resolve in between. Direction-based authoring belongs in a replace rule that resolves a step into a destination while state is at hand; `tests/test_records.py` shows the pattern.

The same rule decides which built-in operations exist. `AppendToList(value=x)` and `RemoveValue(value=x)` are specified by value, so they mean the same thing at emission and at execution. An operation specified by *position* would not — index 2 is whatever happens to be there when it runs — so there is none.

| Operation | Target |
|---|---|
| `CreateEntity`, `DeleteEntity` | an entity |
| `MoveEntity` | `position`, reporting where it came from |
| `SetComponent`, `RemoveComponent` | any component |
| `IncrementComponent` | an integer, optionally clamped |
| `AppendToList`, `ExtendList`, `RemoveValue` | a tuple component |

`IncrementComponent` is relative rather than absolute, so it means the same thing however much a sibling prerequisite changed the value in between. That is what it has over reading a component and setting the sum — and it is worth knowing that the tuple operations, which do read and write a whole value, are last-write-wins if two rules edit one tuple in a single transaction.

Event classes self-register under their `name`, so two classes claiming one name fail at import. A misspelled event is unreachable rather than a silently successful no-op.

Events are frozen dataclasses, so they are equal by value and hashable — which state hashing and replay both rely on. For serialisation, `dataclasses.asdict` is the answer; note it deep-copies recursively, so in a search loop prefer `dataclasses.fields` plus `getattr` if you need a shallow view.

### Type checking is load-bearing

`Event.__init_subclass__` applies `@dataclass(frozen=True)` at runtime, which a type checker cannot follow. Without help, every event would appear to take no arguments — and a misspelled field would look exactly like a correct call, which is the failure mode that makes checked construction worth having at all.

The `@dataclass_transform(frozen_default=True)` marker on `Event` is what makes it visible. With it, pyright reports:

```text
MoveEntity(entity="p", destinaton=(1, 1))   error: No parameter named "destinaton"
MoveEntity(entity="p")                      error: Argument missing for parameter "destination"
MoveEntity(entity=123, destination=(1, 1))  error: "int" not assignable to "EntityId"
event.destination = (2, 2)                  error: Cannot assign to attribute (frozen)
```

That marker is the reason `requires-python` is `>=3.12` — `frozen_default` landed in that version. `tests/test_engine.py` asserts the marker is present, since nothing else in the suite would notice if it were removed.

### Naming

Built-in events read verb then target — `SetComponent`, `DeleteEntity`, `AppendToList` — so a rule reads as a sentence and a log line explains itself. The registered name follows: `set_component`, `append_to_list`.

Their base classes are named for the **precondition** they impose, not for what they act on:

| Base | Requires |
|---|---|
| `EntityEvent` | names an entity, imposes nothing |
| `ExistingEntityEvent` | the entity is there |
| `NewEntityEvent` | the entity is *not* there |
| `ComponentEvent` | an existing entity, plus a component key |

That is why `CreateEntity` and `DeleteEntity` do not share a base: both concern an entity, but they demand opposite worlds. Naming the base for its subject would hide the only thing it does. It also means the entity-existence check is declared once rather than hand-rolled in every operation that needs it.

## Mechanics are rules, including the engine's own

`mechanics.py` holds the rules the engine ships. There is one:

```python
Engine(rules=[WithinBounds(), Push(), Solid()])
```

`WithinBounds` refuses a move that leaves the grid declared as `width` and `height` on the `WORLD` entity, and stays out of the way when either is absent — so a battle system or a card game uses movement without declaring a shape.

This was a check inside `MoveEntity.apply`, which put one game's mechanic in the core and contradicted the whole argument for the four phases. As a rule it is eight lines, a game that does not want it does not install it, and it is a worked example of the API rather than an exception to it.

## Failures are not errors

An operation returns `Effect | Failure`. There is no third wrapper: a refusal *is* a `Failure`, the same class a `before` rule returns to veto an event, so "no" has one name everywhere.

`Failure` is an expected refusal — a legal action that did not work. `EngineFault` means someone's code is wrong.

Parents wrap a child's failure rather than replacing it, so the whole causal chain survives:

```python
result.root.failure.reason        # "required_event_failed"
result.root.failure.root.reason   # "outside_grid"
result.root.failure.chain()       # every link, outermost first
```

A fault raised mid-cascade carries `fault.partial`, the simulation up to that point, so transactions that had already committed are still inspectable.

## Simulation flow

Each event submitted to `simulate` is processed as a root transaction. The working state is a copy-on-write view; the authoritative state is replaced only if the root event and every requirement beneath it succeed.

### Successful prerequisite chain

```text
Submit MOVE player

BEGIN TRANSACTION
├── working_state = copy-on-write(current_state)
│
├── Resolve MOVE player
│   ├── replace rules
│   │
│   ├── before: push rule matches
│   │   └── emit required MOVE crate
│   │
│   ├── Resolve MOVE crate
│   │   ├── replace rules
│   │   ├── before rules
│   │   ├── EXECUTE MOVE crate operation
│   │   │   └── mutate working_state
│   │   └── record successful MOVE crate frame
│   │
│   ├── continue remaining MOVE player before rules
│   │   └── rules observe working_state with crate already moved
│   │
│   ├── EXECUTE MOVE player operation
│   │   └── mutate working_state
│   │
│   └── record successful MOVE player frame
│
├── COMMIT TRANSACTION
│   └── current_state = working_state
│
├── evaluate after rules in postorder
│   ├── after(MOVE crate)
│   └── after(MOVE player)
│
└── append produced events to the top-level queue
```

The crate move is visible to later rules through `working_state`, but it is never independently committed. If the player move fails, both movements are discarded.

### Failed prerequisite chain

```text
Submit MOVE player

BEGIN TRANSACTION
├── working_state = copy-on-write(current_state)
│
├── Resolve MOVE player
│   ├── before: push rule matches
│   │   └── emit required MOVE crate
│   │
│   ├── Resolve MOVE crate
│   │   ├── before: within-bounds refuses
│   │   │   └── REJECT: outside_grid
│   │   └── record failed MOVE crate frame
│   │
│   ├── propagate failure to MOVE player
│   │   └── REJECT: required_event_failed
│   └── record failed MOVE player frame
│
├── DISCARD TRANSACTION
│   └── working_state is thrown away
│
├── evaluate fail rules against unchanged current_state
│   ├── fail(MOVE crate)
│   └── fail(MOVE player)
│
└── append produced events to the top-level queue
```

A transaction may also fail *after* a requirement has already executed. The requirement's work is discarded with everything else — it never committed on its own, and the record stream keeps it, marked, so a transcript can still explain what happened.

### Top-level consequence queue

```text
1. Open a copy-on-write view of current_state.
2. Resolve the root event and every required before event.
3. If successful: commit, collect after events, append them to the queue.
4. If rejected:   discard, collect fail events, append them to the queue.
5. Continue until the queue is empty.
```

Consequences are processed breadth-first. `A` producing `D, E, F`, with `D` producing `H, I`, runs as `A D E F H I`.

Once an `after` or `fail` event is on the queue it is an ordinary root event, with its own working copy, prerequisite chain, transaction outcome, and consequences.

### Guards

There is no cycle detection. Identical events on one path are legal — spending a coin at two levels of a prerequisite chain is not a loop, and rejecting it outright refused terminating games. Three budgets bound the work instead: `max_depth`, `max_events_per_transaction` (because depth alone permits exponential fan-out), and `max_transactions`. Their defaults are `MAX_DEPTH`, `MAX_EVENTS_PER_TRANSACTION`, and `MAX_TRANSACTIONS`, declared once in `engine.py`. A depth fault carries the ancestor chain and the rule that emitted each link.

## State is immutable by convention — and now by construction

A transaction shallow-copies the entity mapping and copies an entity only when something first writes to it. Cost tracks what changes rather than the size of the world — around 8µs per transaction whether the world holds 32 entities or 200, where a whole-world `deepcopy` alone runs from 80µs to 500µs.

The consequence is structural sharing: an entity nothing wrote to is the same object across state generations, including the state you passed in.

**Never mutate a state the engine gave you.** Emit an event instead. `Engine(strict=True)`, the default, hands rules a read-only view of each entity.

That view is now *complete*. It is one `MappingProxyType` over the component mapping, and because every value inside is immutable there is no second level to hand out. When a component could hold a nested dict, the proxy was one level deep: a rule could take `world["player"]["inventory"]` — a live dict, possibly the caller's own — and write straight through it into committed state, surviving rollback. Recursive proxying was the alternative fix, and it was both expensive and the cause of a workaround in the copy path.

## The record stream

`result.records` is an append-only log serving three consumers.

Attempts are recorded on the way in and frames on the way out, so the stream is a tree traversal rather than a flat list — which is what lets a transcript say "the player attempted to move" before knowing whether it worked. Rule firings carry the rule that fired, because "the player pushed the crate" names a rule, not an event. And a discarded transaction is **marked, never truncated**: the renderer needs the failed attempt that the hasher must ignore.

```text
The player attempted to move left.
The player pushed the crate.
The crate attempted to move left.
The crate move failed because it was blocked by the wall.
The player move failed because the crate did not move.
```

The engine emits structure and stays out of the phrasing. `tests/test_records.py` contains a renderer that produces exactly the above.

A `Frame` is one event's resolution and its outcome. The resolver accumulates frames unconditionally, because deciding which `after` or `fail` rules to run is exactly a question about them, and notes the *same object* to the log, where narration picks it up. There used to be a second, field-identical `Outcome` record alongside it; one object in two containers does the job, and marking a rolled-back span now shows up through `Transaction.frames` too.

Mutation records are unconditional — rollback and hashing depend on them — and carry both the previous and the new value, which is what makes incremental hashing and cheap diffing possible. Everything else is narration, enabled with `Engine(narrate=True)`. In a search loop that overhead is multiplied by every node, so it is off by default.

## Search

The engine holds no world state, so it can be asked what *would* happen:

```python
for destination in candidates:
    outcome = engine.speculate(state, MoveEntity(entity="player", destination=destination))
    if outcome.committed:
        seen[destination] = state_key(outcome.entities)
```

`speculate` resolves one transaction, reports the outcome, and commits nothing. Consequences are collected but not cascaded — a search wants one ply at a time.

`state_key` gives a canonical, order-independent key, so states reached by different move orders compare equal. Because values are immutable it only has to settle ordering, which is roughly ten times cheaper than the recursive canonicalisation it replaced. `Transaction.touched` is the set of entities a transaction copied, which together with the mutation records is everything needed for incremental hashing.

## Layout

| Module | Responsibility |
|---|---|
| `types.py` | The shared vocabulary: `Entities`, `Components`, `JsonValue`, `WORLD` |
| `events.py` | `Event`, `Failure`, `Effect`, registration |
| `operations.py` | Built-in mutation events, and the checks hoisted onto their base classes |
| `mechanics.py` | Rules the engine ships |
| `state.py` | `World`: copy-on-write, the write boundary, mutation recording, hashing |
| `query.py` | `Query`, the inspectable selection structure |
| `rules.py` | Phase base classes, ordering, dispatch |
| `records.py` | Record types, `Frame`, and `RecordLog` |
| `resolve.py` | Event resolution and the transaction boundary |
| `engine.py` | Facade, the consequence cascade, and the limits |
| `vectors.py` | Grid arithmetic |

## Tests

```sh
uv run pytest                                 # everything
uv run pytest -m "not perf"                   # skip throughput checks
uv run pytest tests/test_performance.py -s    # print the numbers
uvx ruff check src/ tests/                    # lint
uvx pyright src/                              # types
```

`tests/test_invariants.py` encodes the semantics the engine guarantees. The implementation may be replaced freely; those assertions may not change without a deliberate decision.

## Not built

- **Query indexing.** The inspectable representation is in place; the index is not, so selection is a sorted scan. When it lands it should ship with a debug mode cross-checking indexed results against a full scan — a silently wrong index is the worst failure mode here.
- **A component schema.** The one thing flat keys gave up is catching a misspelled key at the write. A per-game declaration of which components exist would get it back, and is also what a YAML loader would want.
- **Namespace operations.** Clearing `statuses.*` in one event, and a `namespace(entity, prefix)` read helper, so games do not hand-roll prefix scans.
- **Seeded RNG.** Reserved as an engine service on the rule, so adding it will not change any method signature.
- **Simultaneous resolution.** The cascade is sequential. True simultaneity — all reads against pre-state, all writes applied together — would be a third resolution mode, not a rule pattern.
- **YAML authoring.** Rules are Python. The rule API is kept data-shaped so a loader remains possible, mainly as a sandbox for untrusted content.
