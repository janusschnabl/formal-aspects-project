"""Built-in explicit-state reachability, used to cross-check LoLA.

Only works for bounded nets (finite state space); gives up after `limit` states.
"""

from __future__ import annotations

from collections import deque

from .net import PetriNet


class StateSpaceTooLarge(RuntimeError):
    pass


def dead_transitions(net: PetriNet, limit: int = 1_000_000) -> list[str]:
    idx = {p: i for i, p in enumerate(net.places)}
    trans = [
        (t.name, [(idx[p], w) for p, w in t.pre.items()], [(idx[p], w) for p, w in t.post.items()])
        for t in net.transitions
    ]
    start = net.initial_marking()
    seen = {start}
    queue = deque([start])
    fired: set[str] = set()

    while queue:
        m = queue.popleft()
        for name, pre, post in trans:
            if all(m[i] >= w for i, w in pre):
                fired.add(name)
                nxt = list(m)
                for i, w in pre:
                    nxt[i] -= w
                for i, w in post:
                    nxt[i] += w
                nxt = tuple(nxt)
                if nxt not in seen:
                    if len(seen) >= limit:
                        raise StateSpaceTooLarge(f"more than {limit} reachable markings (net may be unbounded)")
                    seen.add(nxt)
                    queue.append(nxt)
        if len(fired) == len(trans):
            break  # every transition already shown live-ish; nothing can be dead
    return [name for name, _, _ in trans if name not in fired]
