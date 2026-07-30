"""Copy-on-write throughput, which is what makes search viable.

Thresholds are expressed as ratios against ``deepcopy`` measured in the same process,
rather than as absolute rates, so the assertions mean the same thing on a fast laptop
and a slow CI box.

The claim being defended: a whole transaction -- resolve, execute, record, commit --
costs less than copying the world once. Under the previous implementation a transaction
paid that copy *before* doing any work.
"""

from __future__ import annotations

import time
from copy import deepcopy

import pytest

from baffle import WORLD, Engine, MoveEntity

pytestmark = pytest.mark.perf

SIZES = (32, 64, 200)


def world(entities: int) -> dict:
    state = {
        f"e{index}": {
            "position": (index % 8, index // 8),
            "hp": 10,
            "tags": ["a", "b", "c"],
        }
        for index in range(entities)
    }
    state[WORLD] = {"width": 1_000, "height": 1_000}
    return state


def _seconds(work, repeats: int) -> float:
    start = time.perf_counter()
    for _ in range(repeats):
        work()
    return (time.perf_counter() - start) / repeats


def measure(entities: int, repeats: int = 200) -> tuple[float, float]:
    """Per-transaction cost, and the cost of one deepcopy of the same world."""
    state = world(entities)
    # Narration is the play-time default; a search loop turns it off.
    engine = Engine(narrate=False)
    event = MoveEntity(entity="e3", destination=(4, 4))

    engine.simulate(state, event)  # warm up
    per_transaction = _seconds(lambda: engine.simulate(state, event), repeats)
    per_copy = _seconds(lambda: deepcopy(state), repeats)
    return per_transaction, per_copy


@pytest.mark.parametrize("entities", SIZES)
def test_a_transaction_costs_less_than_copying_the_world(entities):
    per_transaction, per_copy = measure(entities)

    assert per_transaction < per_copy, (
        f"{entities} entities: {per_transaction * 1e6:.1f}us per transaction vs "
        f"{per_copy * 1e6:.1f}us per deepcopy"
    )


def test_transaction_cost_barely_grows_with_world_size():
    """Copy-on-write makes cost track what changed, not how much world there is.

    A whole-world copy is linear in entity count; this should be close to flat.
    """
    small, _ = measure(SIZES[0])
    large, _ = measure(SIZES[-1])
    growth = large / small
    ratio = SIZES[-1] / SIZES[0]

    assert growth < ratio / 2, (
        f"cost grew {growth:.1f}x for a {ratio:.1f}x larger world, which looks linear"
    )


def test_report(capsys):
    """Not an assertion -- run with -s to see the numbers behind the plan."""
    with capsys.disabled():
        print(f"\n{'entities':>9} {'per tx':>12} {'deepcopy':>12} {'tx/s':>12}")
        for entities in SIZES:
            per_transaction, per_copy = measure(entities)
            print(
                f"{entities:>9} {per_transaction * 1e6:>10.1f}us "
                f"{per_copy * 1e6:>10.1f}us {1 / per_transaction:>12,.0f}"
            )
