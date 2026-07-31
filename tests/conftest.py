from __future__ import annotations

import pytest

from baffle import Engine, WithinBounds
from scenarios import Push, Solid, Unlock, door_world, push_world

EAST = (1, 0)

# `within_bounds` is what makes the cramped world cramped: the grid is declared on the
# world entity, and refusing a move that leaves it is a rule like any other.


@pytest.fixture
def open_world():
    """The crate has room to be pushed."""
    return push_world(width=5)


@pytest.fixture
def cramped_world():
    """The crate is against the far wall and cannot move."""
    return push_world(width=2)


@pytest.fixture
def door_state():
    return door_world(keys=1)


@pytest.fixture
def push_engine():
    return Engine(rules=[WithinBounds(), Push(), Solid()], narrate=True)


@pytest.fixture
def door_engine():
    return Engine(rules=[WithinBounds(), Unlock(), Solid()], narrate=True)
