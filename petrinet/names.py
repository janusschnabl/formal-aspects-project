"""Name syntax shared by the multi-agent parser and converter."""

import re


_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
_NAME = re.compile(rf"{_IDENTIFIER}(?:\[(?:-?\d+|{_IDENTIFIER})\])*")


def validate_agent_name(name: str) -> None:
    """Accept source names with repeated integer or symbolic index brackets.

    The Tina exporter quotes identifiers, so names do not reserve checker
    keywords or temporal-logic operators.
    """
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ValueError(f"invalid agent-net name {name!r}; use a base identifier and indices such as q[i][j]")
