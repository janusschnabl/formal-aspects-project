"""Independent explicit-state reachability, used to cross-check Tina in tests.

Explores every marking reachable from m_0 (breadth first). A transition is
dead if it is enabled in none of them. Only works for bounded nets (finite
state space); gives up after `limit` markings.
"""

from __future__ import annotations

from collections import deque

from .net import Marking, PetriNet, Transition


class StateSpaceTooLarge(RuntimeError):
    pass


def dead_transitions(net: PetriNet, limit: int = 1_000_000) -> list[Transition]:
    seen: set[Marking] = {net.initial_marking}
    queue: deque[Marking] = deque([net.initial_marking])
    has_been_enabled: set[Transition] = set()

    while queue:
        marking = queue.popleft()
        for t in net.enabled_transitions(marking):
            has_been_enabled.add(t)

            next_marking = net.fire(marking, t)
            if next_marking in seen:
                continue
            if len(seen) >= limit:
                raise StateSpaceTooLarge(f"more than {limit} reachable markings (net may be unbounded)")
            seen.add(next_marking)
            queue.append(next_marking)

        if len(has_been_enabled) == len(net.transitions):
            break  # every transition can fire somewhere, so none are dead

    dead = []
    for t in sorted(net.transitions):
        if t not in has_been_enabled:
            dead.append(t)
    return dead
