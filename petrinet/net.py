"""Petri net data model and the plain-text format parser.

The format follows the formal definition N = (P, T, F, M0):

    places: p1 p2 p3                  # P
    transitions: t1 t2                # T
    initial: p1=1 p3=2                # M0, unlisted places are 0
    arcs: (p1, t1) (t1, p2)           # F, each arc is (place, transition)
          (p2, t2, 2) (t2, p3)        #    or (transition, place); optional 3rd
                                      #    element is the arc weight (default 1)

`#` starts a comment. Each keyword may be repeated; a line that does not
start with a keyword continues the previous one. Names may contain
letters, digits and underscores.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

NAME = r"[A-Za-z_][A-Za-z0-9_]*"
NAME_RE = re.compile(NAME)
KEYWORD_RE = re.compile(r"^(places|transitions|initial|arcs)\s*:(.*)$")
ARC_RE = re.compile(rf"\(\s*({NAME})\s*,\s*({NAME})\s*(?:,\s*(\d+)\s*)?\)")
MARKING_RE = re.compile(rf"({NAME})\s*=\s*(\d+)")


class ParseError(ValueError):
    def __init__(self, lineno: int, msg: str):
        super().__init__(f"line {lineno}: {msg}" if lineno else msg)
        self.lineno = lineno


@dataclass
class Transition:
    name: str
    pre: dict[str, int] = field(default_factory=dict)   # place -> weight consumed
    post: dict[str, int] = field(default_factory=dict)  # place -> weight produced


@dataclass
class PetriNet:
    places: list[str] = field(default_factory=list)
    initial: dict[str, int] = field(default_factory=dict)
    transitions: list[Transition] = field(default_factory=list)

    def initial_marking(self) -> tuple[int, ...]:
        return tuple(self.initial.get(p, 0) for p in self.places)


def _scan(body: str, pattern: re.Pattern, lineno: int, what: str) -> list[re.Match]:
    """Match `pattern` repeatedly over body, allowing only whitespace/commas between matches."""
    matches, pos = [], 0
    for m in pattern.finditer(body):
        if body[pos:m.start()].strip(" \t,"):
            break
        matches.append(m)
        pos = m.end()
    rest = body[pos:].strip(" \t,")
    if rest:
        raise ParseError(lineno, f"bad {what} near {rest!r}")
    return matches


def parse(text: str) -> PetriNet:
    places: list[tuple[int, str]] = []
    transitions: list[tuple[int, str]] = []
    initial: list[tuple[int, str, int]] = []
    arcs: list[tuple[int, str, str, int]] = []

    section = None
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        m = KEYWORD_RE.match(line)
        if m:
            section, body = m.group(1), m.group(2)
        elif section is None:
            raise ParseError(lineno, f"expected 'places:', 'transitions:', 'initial:' or 'arcs:', got {line!r}")
        else:
            body = line

        if section in ("places", "transitions"):
            target = places if section == "places" else transitions
            for name in re.split(r"[,\s]+", body.strip()):
                if not name:
                    continue
                if not NAME_RE.fullmatch(name):
                    raise ParseError(lineno, f"bad name {name!r}")
                target.append((lineno, name))
        elif section == "initial":
            for mm in _scan(body, MARKING_RE, lineno, "marking (expected place=N)"):
                initial.append((lineno, mm.group(1), int(mm.group(2))))
        else:
            for mm in _scan(body, ARC_RE, lineno, "arc (expected (x, y) or (x, y, weight))"):
                arcs.append((lineno, mm.group(1), mm.group(2), int(mm.group(3) or 1)))

    # --- semantic checks -------------------------------------------------
    net = PetriNet()
    seen: dict[str, str] = {}
    for kind, items in (("place", places), ("transition", transitions)):
        for lineno, name in items:
            if name in seen:
                raise ParseError(lineno, f"{name!r} already declared as a {seen[name]}")
            seen[name] = kind
    net.places = [n for _, n in places]
    trans = {n: Transition(n) for _, n in transitions}
    net.transitions = list(trans.values())

    for lineno, p, n in initial:
        if seen.get(p) != "place":
            raise ParseError(lineno, f"initial marking: {p!r} is not a declared place")
        if p in net.initial:
            raise ParseError(lineno, f"initial marking for {p!r} given twice")
        net.initial[p] = n

    for lineno, src, dst, w in arcs:
        arc = f"({src}, {dst})"
        for name in (src, dst):
            if name not in seen:
                raise ParseError(lineno, f"arc {arc}: {name!r} is not a declared place or transition")
        if w == 0:
            raise ParseError(lineno, f"arc {arc}: weight must be positive")
        kinds = (seen[src], seen[dst])
        if kinds == ("place", "transition"):
            side, t, p = trans[dst].pre, dst, src
        elif kinds == ("transition", "place"):
            side, t, p = trans[src].post, src, dst
        else:
            raise ParseError(lineno, f"arc {arc} connects two {kinds[0]}s; arcs must go place->transition or transition->place")
        if p in side:
            raise ParseError(lineno, f"arc {arc} given twice")
        side[p] = w

    return net


def load(path: str) -> PetriNet:
    with open(path, encoding="utf-8") as f:
        return parse(f.read())
