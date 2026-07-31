"""How a class name becomes an identifier.

Events and rules both carry a ``name``: an event registers under it, and a rule is
referred to by it in ``run_before`` and ``run_after``. Both derive one from the class
name when the author supplies none. Neither module owns that convention, so it lives
here rather than in one of them with the other importing sideways for it.

Its own module, and not a general utility bin, because the surrounding modules each hold
the helpers for the thing they are about: `is_vec2` sits with grid arithmetic,
`validate_key` with the write boundary. A `utils.py` would only be a place for the next
helper to avoid that decision.
"""

import re

#: Before any capital that is not the first character. A zero-width match, so `sub`
#: inserts rather than replaces and no character is consumed.
_CAMEL_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")


def snake_case(name: str) -> str:
    """Convert a class name: ``"SetComponent"`` becomes ``"set_component"``.

    One convention for both, which they did not always share: rules once derived
    kebab-case and events snake_case, so ``WithinBounds`` became ``within-bounds`` while
    ``SetComponent`` became ``set_component``, for no reason a reader could reconstruct.

    Every capital is a boundary, so a run of them separates: ``"HPCost"`` becomes
    ``"h_p_cost"``. Any class whose name reads better otherwise should set ``name``
    explicitly -- which is always available, and is what the derivation is a fallback for.
    """
    return _CAMEL_BOUNDARY.sub("_", name).lower()
